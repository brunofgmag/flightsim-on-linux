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


def registry_file(sections):
    head = "WINE REGISTRY Version 2\n;; All keys relative to \\\\User\\\\S-1-5-21-0-0-0-1000\n\n"
    return head + "".join(f"[{path.replace(chr(92), chr(92) * 2)}] 1792018458\n{body}\n\n" for path, body in sections)


def wpf_section(raw):
    return r"Software\Microsoft\Avalon.Graphics", f'"DisableHWAcceleration"={raw}'


def entry(path, disabled="False"):
    return f"    <Launch.Addon>\n        <Name>FSL Control Center</Name>\n        <Disabled>{disabled}</Disabled>\n        <Path>{path}</Path>\n        <CommandLine>-NOTIFY</CommandLine>\n    </Launch.Addon>\n"


def exe_xml(*entries):
    return ('<?xml version="1.0" encoding="windows-1252"?>\n<SimBase.Document Type="Launch" version="1,0">\n    <Filename>exe.xml</Filename>\n'
            + "".join(entries) + "</SimBase.Document>\n").encode("latin-1")


class MachineCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prefix = self.root / "pfx"
        self.proton = self.root / "proton"
        self.base = self.root / "base"
        for folder in (self.prefix, self.proton, self.base):
            folder.mkdir()
        self.write_settings(WRITECOPY)
        self.write_gdiplus(b"patched gdiplus")
        self.write_wpf("dword:00000001")
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

    def write_wpf(self, raw):
        sections = [] if raw is None else [wpf_section(raw)]
        (self.prefix / "user.reg").write_text(registry_file(sections))

    def write_exe_xml(self, data):
        (self.base / "exe.xml").write_bytes(data)

    def check(self):
        parsed = fslabs.parser().parse_args(["check", str(self.prefix), str(self.proton), str(self.base)])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = parsed.run(parsed)
        return code, output.getvalue().splitlines()


class CheckTest(MachineCase):
    def assert_only_failure(self, word):
        code, lines = self.check()
        self.assertEqual(code, 1)
        failures = [line for line in lines if line.startswith("FAIL")]
        self.assertEqual(len(failures), 1, lines)
        self.assertIn(word, failures[0])
        self.assertEqual(len(lines), 4)

    def test_a_machine_with_every_piece_passes_with_four_ok_lines(self):
        code, lines = self.check()
        self.assertEqual(code, 0)
        self.assertEqual([line.split()[0] for line in lines], ["ok"] * 4)

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

    def test_wpf_in_hardware_fails(self):
        self.write_wpf("dword:00000000")
        self.assert_only_failure("DisableHWAcceleration 0")

    def test_a_prefix_without_the_wpf_value_fails(self):
        self.write_wpf(None)
        self.assert_only_failure("has no DisableHWAcceleration")

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
        parsed = fslabs.parser().parse_args(["check", "", "", ""])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = parsed.run(parsed)
        lines = output.getvalue().splitlines()
        self.assertEqual(code, 1)
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(line.startswith("FAIL no ") and "flightsim-on-linux machine" in line for line in lines), lines)

    def test_a_utf16_exe_xml_fails_without_a_traceback(self):
        self.write_exe_xml(b"\xff\xfe<\x00?\x00")
        self.assert_only_failure("cannot be read")


FAKE_WINE = '''#!/usr/bin/env python3
import os, sys
from pathlib import Path
with open(os.environ["WINE_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
if args[:1] == ["reg"]:
    user = Path(os.environ["WINEPREFIX"]) / "user.reg"
    head = "WINE REGISTRY Version 2\\n\\n"
    if args[1] == "add":
        value = int(args[args.index("/d") + 1])
        user.write_text(head + "[Software\\\\\\\\Microsoft\\\\\\\\Avalon.Graphics] 1\\n" + f'"DisableHWAcceleration"=dword:{value:08x}\\n')
    elif args[1] == "delete":
        user.write_text(head)
'''
FAKE_WINESERVER = '''#!/bin/sh
echo "wineserver $*" >> "$WINE_LOG"
'''


class ScriptsTest(MachineCase):
    def setUp(self):
        super().setUp()
        self.compatdata = self.root / "compatdata"
        self.wine_log = self.root / "wine.log"
        for name, body in (("wine", FAKE_WINE), ("wineserver", FAKE_WINESERVER)):
            program = self.proton / "files" / "bin" / name
            program.parent.mkdir(parents=True, exist_ok=True)
            program.write_text(body)
            program.chmod(0o755)
        self.env = {
            **os.environ,
            "HOME": str(self.root / "home"),
            "FSOL_PREFIX": str(self.prefix),
            "FSOL_COMPATDATA": str(self.compatdata),
            "FSOL_PROTON": str(self.proton),
            "FSOL_BASE": str(self.base),
            "WINE_LOG": str(self.wine_log),
        }
        self.env.pop("STEAM_DIR", None)
        self.env.pop("FSOL_STEAM", None)

    def run_action(self, action):
        return subprocess.run([str(ADDON / action)], cwd=ADDON, env=self.env, capture_output=True, text=True)

    def wine_calls(self):
        return self.wine_log.read_text().splitlines() if self.wine_log.exists() else []

    def mark(self):
        return self.compatdata / "flightsim-on-linux-backup" / "fslabs-a321" / "wpf-before"

    def test_apply_sets_the_wpf_value_and_undo_removes_it_when_it_was_absent(self):
        self.write_wpf(None)
        applied = self.run_action("apply")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertIn("changed WPF programs draw in software", applied.stdout)
        self.assertEqual(self.run_action("check").returncode, 0)
        self.assertEqual(self.mark().read_text().strip(), "absent")
        undone = self.run_action("undo")
        self.assertEqual(undone.returncode, 0, undone.stderr)
        self.assertIsNone(fslabs.wpf_value(self.prefix))
        self.assertFalse(self.mark().exists())

    def test_undo_puts_back_a_value_that_was_zero(self):
        self.write_wpf("dword:00000000")
        self.assertEqual(self.run_action("apply").returncode, 0)
        self.assertEqual(self.run_action("undo").returncode, 0)
        self.assertEqual(fslabs.wpf_value(self.prefix), 0)

    def test_apply_leaves_a_prefix_that_already_draws_in_software_alone(self):
        applied = self.run_action("apply")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertIn("already WPF programs draw in software", applied.stdout)
        self.assertEqual(self.wine_calls(), [])
        self.assertFalse(self.mark().exists())

    def test_undo_without_an_apply_changes_nothing(self):
        undone = self.run_action("undo")
        self.assertEqual(undone.returncode, 0, undone.stderr)
        self.assertEqual(self.wine_calls(), [])
        self.assertEqual(fslabs.wpf_value(self.prefix), 1)

    def test_apply_refuses_a_wpf_value_that_is_not_a_number(self):
        self.write_wpf('"yes"')
        applied = self.run_action("apply")
        self.assertEqual(applied.returncode, 2)
        self.assertIn("not a number", applied.stderr)
        self.assertEqual(self.wine_calls(), [])

    def test_apply_and_undo_never_touch_exe_xml(self):
        disabled = exe_xml(entry(CONTROL_CENTER, disabled="True"))
        self.write_exe_xml(disabled)
        applied = self.run_action("apply")
        self.assertIn("Disabled true", applied.stdout)
        self.run_action("undo")
        self.assertEqual((self.base / "exe.xml").read_bytes(), disabled)

    def test_apply_says_how_to_get_the_entry_when_it_is_missing(self):
        (self.base / "exe.xml").unlink()
        applied = self.run_action("apply")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertIn("start the Control Center, which writes its own", applied.stdout)
        self.assertFalse((self.base / "exe.xml").exists())

    def test_the_wine_calls_add_the_dword_and_stop_the_wineserver(self):
        self.write_wpf(None)
        self.run_action("apply")
        self.assertEqual(self.wine_calls(), [
            r"reg add HKCU\Software\Microsoft\Avalon.Graphics /v DisableHWAcceleration /t REG_DWORD /d 1 /f",
            "wineserver -k",
        ])


class ManifestTest(unittest.TestCase):
    def test_the_installed_paths_match_the_control_center_path_the_check_reads(self):
        data = json.loads((ADDON / "addon.json").read_text())
        windows = "drive_c/" + fslabs.CONTROL_CENTER[3:].replace("\\", "/")
        self.assertIn(windows, data["installed"]["prefix"])


if __name__ == "__main__":
    unittest.main()
