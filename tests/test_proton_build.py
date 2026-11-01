import contextlib
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fsol import doctor, proton_build

ROOT = Path(__file__).resolve().parent.parent
PATCH = "windows-storage-applicationdata-no-package.patch"
MODULE = "lib/wine/x86_64-windows/windows.storage.applicationdata.dll"
GOOD = "the module is the patched one"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class CheckCommandTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.proton = Path(self.temp.name) / "proton"
        self.write_build(b"patched module")

    def write_build(self, data, recorded=None, patch=None):
        module = self.proton / "files" / MODULE
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_bytes(data)
        patch_sha = patch or sha256((ROOT / "patches" / "wine" / PATCH).read_bytes())
        record = ["release cachyos-11.0-20261005-slr x86_64", f"patch {patch_sha} {PATCH}", f"module {recorded or sha256(data)} {MODULE}"]
        (self.proton / "fs2024-build.txt").write_text("\n".join(record) + "\n")

    def check(self, *args):
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = proton_build.main(list(args))
        return code, output.getvalue().splitlines(), errors.getvalue()

    def check_proton(self):
        return self.check("check", str(self.proton), PATCH, MODULE, GOOD)

    def test_a_build_that_lists_the_patch_and_carries_its_module_passes(self):
        self.assertEqual(self.check_proton()[:2], (0, [f"ok {GOOD}"]))

    def test_a_proton_without_a_build_record_fails_and_says_to_build(self):
        (self.proton / "fs2024-build.txt").unlink()
        code, lines, _ = self.check_proton()
        self.assertEqual(code, 1)
        self.assertEqual(lines, [f"FAIL {self.proton / 'fs2024-build.txt'} does not list this repository's {PATCH}; run: flightsim-on-linux proton build"])

    def test_a_build_with_another_version_of_the_patch_fails(self):
        self.write_build(b"patched module", patch="0" * 64)
        code, lines, _ = self.check_proton()
        self.assertEqual(code, 1)
        self.assertIn(PATCH, lines[0])

    def test_a_module_that_differs_from_the_build_record_fails(self):
        self.write_build(b"stock module", recorded=sha256(b"patched module"))
        code, lines, _ = self.check_proton()
        self.assertEqual(code, 1)
        self.assertEqual(lines, [f"FAIL {MODULE} in {self.proton / 'files'} is not the one its build record lists; run: flightsim-on-linux proton build"])

    def test_a_missing_module_fails(self):
        (self.proton / "files" / MODULE).unlink()
        code, lines, _ = self.check_proton()
        self.assertEqual(code, 1)
        self.assertIn(MODULE, lines[0])

    def test_no_proton_folder_fails_instead_of_reading_the_current_folder(self):
        code, lines, _ = self.check("check", "", PATCH, MODULE, GOOD)
        self.assertEqual(code, 1)
        self.assertEqual(lines, ["FAIL no Proton found; run: flightsim-on-linux machine"])

    def test_wrong_arguments_print_the_usage(self):
        code, lines, errors = self.check("check", str(self.proton))
        self.assertEqual(code, 2)
        self.assertEqual(lines, [])
        self.assertIn("usage:", errors)

    def test_an_incomplete_second_patch_prints_the_usage(self):
        code, lines, errors = self.check("check", str(self.proton), PATCH, MODULE, GOOD, PATCH, MODULE)
        self.assertEqual(code, 2)
        self.assertEqual(lines, [])
        self.assertIn("usage:", errors)

    def test_every_patch_gets_its_own_line_and_one_failure_fails_the_check(self):
        other = "wintrust-create-indirect-data.patch"
        code, lines, _ = self.check("check", str(self.proton), PATCH, MODULE, GOOD, other, MODULE, "wintrust is patched")
        self.assertEqual(code, 1)
        self.assertEqual(lines[0], f"ok {GOOD}")
        self.assertEqual(lines[1], f"FAIL {self.proton / 'fs2024-build.txt'} does not list this repository's {other}; run: flightsim-on-linux proton build")
        self.assertEqual(len(lines), 2)

    def test_no_proton_folder_fails_once_for_several_patches(self):
        code, lines, _ = self.check("check", "", PATCH, MODULE, GOOD, PATCH, MODULE, GOOD)
        self.assertEqual(code, 1)
        self.assertEqual(lines, ["FAIL no Proton found; run: flightsim-on-linux machine"])



VKD3D_PATCH = "root-signature-reuse-identical-blob.patch"
VKD3D_MODULE = "lib/wine/vkd3d-proton/x86_64-windows/d3d12core.dll"


class VulkanPatchKindTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.proton = Path(self.temp.name) / "proton"
        module = self.proton / "files" / VKD3D_MODULE
        module.parent.mkdir(parents=True)
        module.write_bytes(b"d3d12core of the build")

    def record(self, kind="vkd3d-patch", patch_sha=None, module_sha=None):
        patch_sha = patch_sha or sha256((ROOT / "patches" / "vkd3d-proton" / VKD3D_PATCH).read_bytes())
        lines = [f"{kind} {patch_sha} {VKD3D_PATCH}", f"module {module_sha or sha256(b'd3d12core of the build')} {VKD3D_MODULE}"]
        (self.proton / "fs2024-build.txt").write_text("\n".join(lines) + "\n")

    def test_a_vkd3d_patch_is_listed_under_its_own_kind_in_its_own_folder(self):
        self.record()
        self.assertEqual(proton_build.patch_line(self.proton, VKD3D_PATCH, VKD3D_MODULE, GOOD, kind="vkd3d-patch"), (True, GOOD))

    def test_the_same_name_listed_as_a_wine_patch_does_not_count(self):
        self.record(kind="patch")
        good, text = proton_build.patch_line(self.proton, VKD3D_PATCH, VKD3D_MODULE, GOOD, kind="vkd3d-patch")
        self.assertFalse(good)
        self.assertIn(f"does not list this repository's {VKD3D_PATCH}", text)

    def test_another_version_of_the_vkd3d_patch_does_not_count(self):
        self.record(patch_sha="0" * 64)
        self.assertFalse(proton_build.patch_line(self.proton, VKD3D_PATCH, VKD3D_MODULE, GOOD, kind="vkd3d-patch")[0])

    def test_the_default_kind_is_still_the_wine_patch_folder(self):
        self.record()
        self.assertFalse(proton_build.patch_line(self.proton, VKD3D_PATCH, VKD3D_MODULE, GOOD)[0])

    def test_the_patch_and_the_module_are_looked_at_apart(self):
        self.record(module_sha="0" * 64)
        self.assertEqual(proton_build.listed_line(self.proton, VKD3D_PATCH, GOOD, "vkd3d-patch"), (True, GOOD))
        good, text = proton_build.module_line(self.proton, VKD3D_MODULE, GOOD)
        self.assertFalse(good)
        self.assertEqual(text, f"{VKD3D_MODULE} in {self.proton / 'files'} is not the one its build record lists; run: flightsim-on-linux proton build")


OTHER = "lib/wine/x86_64-windows/wintrust.dll"


class ProtonDoctorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.proton = Path(self.temp.name) / "Proton-FS2024"
        files = self.proton / "files"
        record = ["release cachyos-11.0-20261005-slr x86_64"]
        for module in (MODULE, OTHER):
            (files / module).parent.mkdir(parents=True, exist_ok=True)
            (files / module).write_bytes(b"patched " + module.encode())
            record.append(f"module {sha256(b'patched ' + module.encode())} {module}")
        (self.proton / "fs2024-build.txt").write_text("\n".join(record) + "\n")

    def machine(self, fs2024=True):
        return SimpleNamespace(proton=self.proton, compat_tool="Proton-FS2024", proton_is_fs2024=fs2024)

    def test_a_build_whose_modules_match_its_record_is_fine(self):
        self.assertEqual(doctor.check_proton(self.machine()).status, "ok")

    def test_a_module_written_over_after_the_build_fails_and_names_it(self):
        (self.proton / "files" / OTHER).write_bytes(b"another proton's module")
        check = doctor.check_proton(self.machine())
        self.assertEqual(check.status, "fail")
        self.assertIn("wintrust.dll", check.detail)
        self.assertNotIn("windows.storage.applicationdata.dll", check.detail)
        self.assertTrue(check.command.endswith(" proton build"))

    def test_a_missing_module_fails(self):
        (self.proton / "files" / MODULE).unlink()
        check = doctor.check_proton(self.machine())
        self.assertEqual(check.status, "fail")
        self.assertIn("windows.storage.applicationdata.dll", check.detail)

    def test_a_build_without_a_record_is_not_reported_as_written_over(self):
        (self.proton / "fs2024-build.txt").unlink()
        self.assertEqual(doctor.check_proton(self.machine()).status, "ok")

    def test_another_proton_is_not_read_for_a_build_record(self):
        (self.proton / "files" / OTHER).write_bytes(b"another proton's module")
        self.assertEqual(doctor.check_proton(self.machine(fs2024=False)).status, "warn")


if __name__ == "__main__":
    unittest.main()
