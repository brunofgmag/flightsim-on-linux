import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addons" / "fslabs-a321"
CONTROL_CENTER = r"C:\FlightSimLabs\ControlCenter\FSLControlCenter.exe"
WRITECOPY = "apps:Fenix.exe,FlightSimulator2024.exe,cef_browser.exe"
PATCH = "gdiplus-path-gradient-and-centered-text.patch"
MODULE = "lib/wine/x86_64-windows/gdiplus.dll"


def load_module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fslabs = load_module("fslabs_parts", ADDON / "parts" / "fslabs.py")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def entry(path, disabled="False"):
    return f"    <Launch.Addon>\n        <Name>FSL Control Center</Name>\n        <Disabled>{disabled}</Disabled>\n        <Path>{path}</Path>\n        <CommandLine>-NOTIFY</CommandLine>\n    </Launch.Addon>\n"


def exe_xml(*entries):
    return ('<?xml version="1.0" encoding="windows-1252"?>\n<SimBase.Document Type="Launch" version="1,0">\n    <Filename>exe.xml</Filename>\n'
            + "".join(entries) + "</SimBase.Document>\n").encode("latin-1")


def run_check(*args):
    parsed = fslabs.parser().parse_args(["check", *args])
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = parsed.run(parsed)
    return code, output.getvalue().splitlines()


class MachineCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proton = self.root / "proton"
        self.base = self.root / "base"
        for folder in (self.proton, self.base):
            folder.mkdir()
        self.write_settings(WRITECOPY)
        self.write_gdiplus(b"patched gdiplus")
        self.write_exe_xml(exe_xml(entry(CONTROL_CENTER)))

    def write_settings(self, writecopy):
        (self.proton / "user_settings.py").write_text(f'user_settings = {{\n    "WINE_TRACK_WRITECOPY": "{writecopy}",\n    "WINE_FENIX_HELPER_WINDOWS": "1",\n}}\n')

    def write_gdiplus(self, data, recorded=None, patch=None):
        module = self.proton / "files" / MODULE
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_bytes(data)
        patch_sha = patch or sha256((ROOT / "patches" / "wine" / PATCH).read_bytes())
        record = ["release cachyos-11.0-20261005-slr x86_64", f"patch {patch_sha} {PATCH}", f"module {recorded or sha256(data)} {MODULE}"]
        (self.proton / "fs2024-build.txt").write_text("\n".join(record) + "\n")

    def write_exe_xml(self, data):
        (self.base / "exe.xml").write_bytes(data)

    def check(self):
        return run_check(str(self.proton), str(self.base))


class CheckTest(MachineCase):
    def assert_only_failure(self, word):
        code, lines = self.check()
        self.assertEqual(code, 1)
        failures = [line for line in lines if line.startswith("FAIL")]
        self.assertEqual(len(failures), 1, lines)
        self.assertIn(word, failures[0])
        self.assertEqual(len(lines), 3)

    def test_a_machine_with_every_piece_passes_with_three_ok_lines(self):
        code, lines = self.check()
        self.assertEqual(code, 0)
        self.assertEqual([line.split()[0] for line in lines], ["ok"] * 3)

    def test_a_proton_without_cef_browser_in_the_writecopy_list_fails(self):
        self.write_settings("apps:Fenix.exe,FlightSimulator2024.exe")
        self.assert_only_failure("cef_browser.exe")

    def test_the_writecopy_list_is_read_without_regard_to_case(self):
        self.write_settings("apps:Fenix.exe,CEF_Browser.exe")
        self.assertEqual(self.check()[0], 0)

    def test_a_proton_without_user_settings_fails_the_writecopy_line(self):
        (self.proton / "user_settings.py").unlink()
        self.assert_only_failure("cef_browser.exe")

    def test_a_proton_without_a_build_record_fails_the_gdiplus_line(self):
        (self.proton / "fs2024-build.txt").unlink()
        self.assert_only_failure("proton build")

    def test_a_build_with_another_version_of_the_gdiplus_patch_fails(self):
        self.write_gdiplus(b"patched gdiplus", patch="0" * 64)
        self.assert_only_failure(PATCH)

    def test_a_gdiplus_dll_that_differs_from_the_build_record_fails(self):
        self.write_gdiplus(b"stock gdiplus", recorded=sha256(b"patched gdiplus"))
        self.assert_only_failure("gdiplus.dll")

    def test_a_disabled_control_center_entry_fails(self):
        self.write_exe_xml(exe_xml(entry(CONTROL_CENTER, disabled="True")))
        self.assert_only_failure("Disabled true")

    def test_an_exe_xml_without_the_control_center_fails(self):
        self.write_exe_xml(exe_xml(entry(r"C:\Other\Tool.exe")))
        self.assert_only_failure("no entry for the Control Center")

    def test_a_missing_exe_xml_fails(self):
        (self.base / "exe.xml").unlink()
        self.assert_only_failure("does not exist")

    def test_folders_the_machine_did_not_find_fail_instead_of_reading_the_current_folder(self):
        code, lines = run_check("", "")
        self.assertEqual(code, 1)
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(line.startswith("FAIL no ") and "flightsim-on-linux machine" in line for line in lines), lines)

    def test_a_utf16_exe_xml_fails_without_a_traceback(self):
        self.write_exe_xml(b"\xff\xfe<\x00?\x00")
        self.assert_only_failure("cannot be read")


class ScriptsTest(MachineCase):
    def run_action(self, action):
        env = {**os.environ, "FSOL_PROTON": str(self.proton), "FSOL_BASE": str(self.base)}
        return subprocess.run([str(ADDON / action)], cwd=ADDON, env=env, capture_output=True, text=True)

    def test_apply_reports_an_enabled_entry_and_changes_nothing(self):
        before = (self.base / "exe.xml").read_bytes()
        applied = self.run_action("apply")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertIn("already exe.xml starts the Control Center", applied.stdout)
        self.assertEqual((self.base / "exe.xml").read_bytes(), before)

    def test_apply_and_undo_never_touch_a_disabled_entry(self):
        disabled = exe_xml(entry(CONTROL_CENTER, disabled="True"))
        self.write_exe_xml(disabled)
        applied = self.run_action("apply")
        self.assertIn("Disabled true", applied.stdout)
        self.assertEqual(self.run_action("undo").returncode, 0)
        self.assertEqual((self.base / "exe.xml").read_bytes(), disabled)

    def test_apply_says_how_to_get_the_entry_when_it_is_missing(self):
        (self.base / "exe.xml").unlink()
        applied = self.run_action("apply")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertIn("start the Control Center, which writes its own", applied.stdout)
        self.assertFalse((self.base / "exe.xml").exists())

    def test_apply_stops_on_an_exe_xml_it_cannot_read(self):
        self.write_exe_xml(b"\xff\xfe<\x00?\x00")
        applied = self.run_action("apply")
        self.assertEqual(applied.returncode, 2)
        self.assertIn("cannot be read", applied.stderr)

    def test_the_check_script_passes_the_machine_folders_through(self):
        checked = self.run_action("check")
        self.assertEqual(checked.returncode, 0, checked.stdout)
        self.assertEqual(len(checked.stdout.splitlines()), 3)


class ManifestTest(unittest.TestCase):
    def test_the_installed_paths_match_the_control_center_path_the_check_reads(self):
        data = json.loads((ADDON / "addon.json").read_text())
        windows = "drive_c/" + fslabs.CONTROL_CENTER[3:].replace("\\", "/")
        self.assertIn(windows, data["installed"]["prefix"])


if __name__ == "__main__":
    unittest.main()
