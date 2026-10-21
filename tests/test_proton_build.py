import contextlib
import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from fsol import proton_build

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


if __name__ == "__main__":
    unittest.main()
