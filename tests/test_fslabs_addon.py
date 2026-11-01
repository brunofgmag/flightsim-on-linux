import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fsol import addons, desktop, watcher as watcher_loop
from tests.support import process_stubs, stand_in_name, start_stand_in
from tests.test_addons import RecordingTools
from tests.test_machine import FakeMachine, write
from tests.test_maddog_addon import page_problems

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addons" / "fslabs-a321"
CONTROL_CENTER = r"C:\FlightSimLabs\ControlCenter\FSLControlCenter.exe"
WRITECOPY = "apps:Fenix.exe,FlightSimulator2024.exe,cef_browser.exe"
PATCH = "gdiplus-path-gradient-and-centered-text.patch"
MODULE = "lib/wine/x86_64-windows/gdiplus.dll"
LINES = 5


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
        self.config = self.root / "config"
        for folder in (self.proton, self.base):
            folder.mkdir()
        environment = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config)})
        environment.start()
        self.addCleanup(environment.stop)
        self.modules = {module: (f"patched {Path(module).name}".encode(), None) for module, _, _ in fslabs.PATCH_GROUPS}
        self.patch_shas = {patch: sha256((ROOT / "patches" / "wine" / patch).read_bytes()) for _, patches, _ in fslabs.PATCH_GROUPS for patch in patches}
        self.write_settings(WRITECOPY)
        self.write_build()
        self.write_exe_xml(exe_xml(entry(CONTROL_CENTER)))

    def write_settings(self, writecopy):
        (self.proton / "user_settings.py").write_text(f'user_settings = {{\n    "WINE_TRACK_WRITECOPY": "{writecopy}",\n    "WINE_FENIX_HELPER_WINDOWS": "1",\n}}\n')

    def write_build(self):
        record = ["release cachyos-11.0-20261005-slr x86_64"]
        for module, patches, _ in fslabs.PATCH_GROUPS:
            data, recorded = self.modules[module]
            file = self.proton / "files" / module
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
            record += [f"patch {self.patch_shas[patch]} {patch}" for patch in patches if self.patch_shas[patch]]
            record.append(f"module {recorded or sha256(data)} {module}")
        (self.proton / "fs2024-build.txt").write_text("\n".join(record) + "\n")

    def write_module(self, module, data, recorded=None):
        self.modules[module] = (data, recorded)
        self.write_build()

    def write_gdiplus(self, data, recorded=None, patch=None):
        self.modules[MODULE] = (data, recorded)
        if patch:
            self.patch_shas[PATCH] = patch
        self.write_build()

    def drop_patches(self, *patches):
        for patch in patches:
            self.patch_shas[patch] = None
        self.write_build()

    def switch_watcher_on(self):
        write(self.config / "systemd" / "user" / "flightsim-on-linux-fslabs-a321.service", "[Service]\n")

    def write_simconnect(self, port):
        write(self.base / "SimConnect.xml", f"<SimBase.Document><SimConnect.Comm><Protocol>IPv4</Protocol><Port>{port}</Port></SimConnect.Comm></SimBase.Document>")

    def write_exe_xml(self, data):
        (self.base / "exe.xml").write_bytes(data)

    def check(self):
        return run_check(str(self.proton), str(self.base))


class CheckTest(MachineCase):
    def assert_only_failure(self, word, count=LINES):
        code, lines = self.check()
        self.assertEqual(code, 1)
        failures = [line for line in lines if line.startswith("FAIL")]
        self.assertEqual(len(failures), 1, lines)
        self.assertIn(word, failures[0])
        self.assertEqual(len(lines), count)

    def test_a_machine_with_every_piece_passes_with_one_ok_line_for_each_piece(self):
        code, lines = self.check()
        self.assertEqual(code, 0)
        self.assertEqual([line.split()[0] for line in lines], ["ok"] * LINES)

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
        self.assert_only_failure("proton build", count=3)

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
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(line.startswith("FAIL no ") and "flightsim-on-linux machine" in line for line in lines), lines)

    def test_a_utf16_exe_xml_fails_without_a_traceback(self):
        self.write_exe_xml(b"\xff\xfe<\x00?\x00")
        self.assert_only_failure("cannot be read")


class ScriptsTest(MachineCase):
    def run_action(self, action):
        env = {**os.environ, "FSOL_PROTON": str(self.proton), "FSOL_BASE": str(self.base), "XDG_CONFIG_HOME": str(self.config)}
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
        self.assertEqual(len(checked.stdout.splitlines()), LINES)


class ManifestTest(unittest.TestCase):
    def test_the_installed_paths_match_the_control_center_path_the_check_reads(self):
        data = json.loads((ADDON / "addon.json").read_text())
        windows = "drive_c/" + fslabs.CONTROL_CENTER[3:].replace("\\", "/")
        self.assertIn(windows, data["installed"]["prefix"])


NINE_PATCHES = {
    "gdiplus-path-gradient-and-centered-text.patch",
    "gdiplus-nested-containers.patch",
    "gdiplus-sourcecopy-text-covers-glyphs.patch",
    "gdiplus-font-family-lookup-in-collection.patch",
    "gdiplus-premultiply-sse2.patch",
    "win32u-larger-font-caches.patch",
    "win32u-banded-region-from-rects.patch",
    "win32u-alpha-blend-sse2.patch",
    "ntdll-heap-bounded-free-list-scan.patch",
}
WIN32U = "lib/wine/x86_64-unix/win32u.so"
NTDLL = "lib/wine/x86_64-windows/ntdll.dll"


class DisplayPatchTest(MachineCase):
    assert_only_failure = CheckTest.assert_only_failure

    def failures(self):
        return [line for line in self.check()[1] if line.startswith("FAIL")]

    def test_the_check_names_the_patches_of_29_and_30_october_and_the_heap_patch(self):
        listed = {patch for _, patches, _ in fslabs.PATCH_GROUPS for patch in patches}
        self.assertEqual(listed, NINE_PATCHES)
        for patch in NINE_PATCHES:
            self.assertTrue((ROOT / "patches" / "wine" / patch).is_file(), patch)

    def test_a_build_without_one_of_the_new_patches_fails_with_one_line_that_names_it(self):
        for patch in sorted(NINE_PATCHES - {PATCH}):
            with self.subTest(patch=patch):
                original = self.patch_shas[patch]
                self.drop_patches(patch)
                failures = self.failures()
                self.assertEqual(len(failures), 1, failures)
                self.assertIn(patch, failures[0])
                self.assertIn("proton build", failures[0])
                self.patch_shas[patch] = original
                self.write_build()

    def test_two_missing_patches_of_one_module_give_one_line_each(self):
        self.drop_patches("win32u-larger-font-caches.patch", "win32u-alpha-blend-sse2.patch")
        failures = self.failures()
        self.assertEqual(len(failures), 2, failures)
        self.assertIn("win32u-larger-font-caches.patch", failures[0])
        self.assertIn("win32u-alpha-blend-sse2.patch", failures[1])

    def test_a_patch_listed_with_another_version_fails(self):
        self.patch_shas["ntdll-heap-bounded-free-list-scan.patch"] = "0" * 64
        self.write_build()
        failures = self.failures()
        self.assertEqual(len(failures), 1, failures)
        self.assertIn("ntdll-heap-bounded-free-list-scan.patch", failures[0])

    def test_a_win32u_that_differs_from_the_build_record_fails_once_for_its_three_patches(self):
        self.write_module(WIN32U, b"stock win32u", recorded=sha256(b"patched win32u.so"))
        failures = self.failures()
        self.assertEqual(len(failures), 1, failures)
        self.assertIn("win32u.so", failures[0])

    def test_an_ntdll_that_differs_from_the_build_record_fails(self):
        self.write_module(NTDLL, b"stock ntdll", recorded=sha256(b"patched ntdll.dll"))
        failures = self.failures()
        self.assertEqual(len(failures), 1, failures)
        self.assertIn("ntdll.dll", failures[0])

    def test_a_missing_win32u_file_fails(self):
        (self.proton / "files" / WIN32U).unlink()
        failures = self.failures()
        self.assertEqual(len(failures), 1, failures)
        self.assertIn("win32u.so", failures[0])


class WatcherTest(MachineCase):
    assert_only_failure = CheckTest.assert_only_failure

    def test_without_the_watcher_a_disabled_entry_stays_a_failure(self):
        self.write_exe_xml(exe_xml(entry(CONTROL_CENTER, disabled="True")))
        self.assert_only_failure("Disabled true")

    def test_with_the_watcher_unit_on_a_disabled_entry_is_expected_and_the_simconnect_port_is_looked_at(self):
        self.write_exe_xml(exe_xml(entry(CONTROL_CENTER, disabled="True")))
        self.switch_watcher_on()
        self.write_simconnect(5111)
        code, lines = self.check()
        self.assertEqual(code, 0, lines)
        self.assertEqual([line.split()[0] for line in lines], ["ok"] * (LINES + 1))
        self.assertIn("watcher starts it", lines[-2])
        self.assertIn("TCP port 5111", lines[-1])

    def test_with_the_watcher_unit_an_enabled_entry_is_still_fine(self):
        self.switch_watcher_on()
        self.write_simconnect(5111)
        self.assertEqual(self.check()[0], 0)

    def test_with_the_watcher_unit_an_absent_entry_is_still_a_failure(self):
        self.write_exe_xml(exe_xml(entry(r"C:\Other\Tool.exe")))
        self.switch_watcher_on()
        self.write_simconnect(5111)
        failures = [line for line in self.check()[1] if line.startswith("FAIL")]
        self.assertEqual(len(failures), 1, failures)
        self.assertIn("no entry for the Control Center", failures[0])

    def test_with_the_watcher_unit_a_privileged_simconnect_port_fails(self):
        self.switch_watcher_on()
        self.write_simconnect(500)
        failures = [line for line in self.check()[1] if line.startswith("FAIL")]
        self.assertEqual(len(failures), 1, failures)
        self.assertIn("addon apply simconnect-tcp", failures[0])

    def test_with_the_watcher_unit_a_missing_simconnect_file_fails(self):
        self.switch_watcher_on()
        failures = [line for line in self.check()[1] if line.startswith("FAIL")]
        self.assertEqual(len(failures), 1, failures)
        self.assertIn("SimConnect.xml", failures[0])

    def test_a_unit_with_another_name_does_not_count(self):
        write(self.config / "systemd" / "user" / "fsl-control-center-watcher.service", "[Service]\n")
        self.write_exe_xml(exe_xml(entry(CONTROL_CENTER, disabled="True")))
        self.assert_only_failure("Disabled true")


PROGRAM = "C:\\FlightSimLabs\\ControlCenter\\FSLControlCenter.exe"
EXE = "drive_c/FlightSimLabs/ControlCenter/FSLControlCenter.exe"


def recording(file, after=""):
    return f'#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "{file}"\nprintf "%s\\n" "cwd=$PWD" >> "{file}"\nenv >> "{file}.env"\n{after}'


def stub(path, text):
    write(path, text)
    path.chmod(0o755)
    return path


class LaunchCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fake = FakeMachine(self.root).build()
        self.found = self.fake.discover()
        self.proton = self.fake.tool
        self.record = self.root / "record"
        self.processes = []
        self.addCleanup(self.stop_processes)
        self.token = str(self.root)
        process_stubs(self.root / "bin", self.token)
        stub(self.root / "bin" / "notify-send", recording(self.record))
        stub(self.root / "cli", recording(self.record))
        stub(self.proton / "files/bin/wine", recording(self.record))
        stub(self.proton / "files/bin/wineserver", recording(f"{self.record}.server"))

    def stop_processes(self):
        for process in self.processes:
            process.kill()
            process.wait()

    def install_program(self):
        write(self.found.prefix / EXE, "MZ")

    def launch(self, *args, **extra):
        env = {
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "FSOL_PREFIX": str(self.found.prefix),
            "FSOL_PROTON": str(self.found.proton),
            "FSOL_CACHE": str(self.root / "cache"),
            "FSOL_CLI": str(self.root / "cli"),
            "FSOL_ADDON": "fslabs-a321",
            "FSOL_SIM_RUNNING": "0",
            **extra,
        }
        return subprocess.run([str(ADDON / "launch"), *args], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def recorded(self):
        return self.record.read_text().splitlines() if self.record.exists() else []

    def recorded_env(self):
        path = Path(f"{self.record}.env")
        return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line) if path.exists() else {}

    def wait_for_record(self, seconds=5):
        deadline = time.monotonic() + seconds
        while not (self.record.exists() and "cwd=" in self.record.read_text()) and time.monotonic() < deadline:
            time.sleep(0.05)

    def run_program(self):
        process = start_stand_in(self.root, self.token, stand_in_name(PROGRAM), 60)
        self.processes.append(process)
        time.sleep(0.2)


class ShapeTest(LaunchCase):
    def test_the_manifest_has_the_menu_entry_the_watcher_and_the_install_source(self):
        addon = addons.load(ADDON)
        self.assertEqual(addon.install, {"from": "https://www.flightsimlabs.com/", "paid": None, "kind": "installer"})
        self.assertEqual([shortcut["title"] for shortcut in addon.shortcuts], ["FSLabs Control Center"])
        self.assertEqual(addon.watcher["trigger"], {"aircraft": "FSLabs A3|fsl-a32x"})
        self.assertEqual((addon.watcher["stop"], addon.watcher["args"], addon.watcher["script"]), ("with_simulator_if_started", ["-NOTIFY"], "launch"))

    def test_the_aircraft_trigger_matches_an_fslabs_aircraft_and_not_a_fenix(self):
        trigger = addons.load(ADDON).watcher["trigger"]["aircraft"]
        self.assertTrue(re.search(trigger, r"SimObjects\Airplanes\FSLabs A321-NEO PW\aircraft.CFG", re.IGNORECASE))
        self.assertTrue(re.search(trigger, r"SimObjects\Airplanes\fsl-a32x\aircraft.cfg", re.IGNORECASE))
        self.assertFalse(re.search(trigger, r"SimObjects\Airplanes\Fenix_A320\aircraft.cfg", re.IGNORECASE))

    def test_the_watcher_pattern_matches_the_program_and_not_other_programs(self):
        pattern = addons.load(ADDON).watcher["app"]
        self.assertTrue(re.search(pattern, PROGRAM + " -NOTIFY"))
        self.assertFalse(re.search(pattern, "C:\\Program Files\\Other\\Other.exe"))
        self.assertFalse(re.search(pattern, "/usr/bin/python3 flightsim-on-linux addon watch fslabs-a321"))

    def test_the_menu_entry_it_writes_passes_the_desktop_validator(self):
        self.install_program()
        written = desktop.write_shortcuts(addons.load(ADDON), self.found, ROOT / "bin" / "flightsim-on-linux", self.root / "data", RecordingTools(), lambda line: None)
        self.assertIn(f'Exec="{ROOT / "bin" / "flightsim-on-linux"}" addon launch fslabs-a321 launch', written[0].read_text())
        if shutil.which("desktop-file-validate"):
            result = subprocess.run(["desktop-file-validate", str(written[0])], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""))

    @unittest.skipUnless(shutil.which("systemd-analyze"), "needs systemd-analyze")
    def test_the_service_it_writes_passes_systemd_verify(self):
        path = self.root / "unit.service"
        path.write_text(watcher_loop.unit_text(addons.load(ADDON), ROOT / "bin" / "flightsim-on-linux"))
        result = subprocess.run(["systemd-analyze", "--user", "verify", str(path)], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""))


class LaunchTest(LaunchCase):
    def setUp(self):
        super().setUp()
        self.install_program()

    def test_with_the_simulator_running_it_starts_inside_the_container_with_direct3d_9_off(self):
        result = self.launch(FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.recorded()[0], f"{self.root}/cli run {PROGRAM}")
        self.assertEqual(self.recorded_env()["EXTRA_WINEDLLOVERRIDES"], "d3d9=d")

    def test_with_the_simulator_closed_it_starts_through_the_prefix_with_direct3d_9_off(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.recorded()[0], f"{self.proton}/files/bin/wine {PROGRAM}")
        env = self.recorded_env()
        self.assertEqual((env["WINEPREFIX"], env["WINEDLLOVERRIDES"]), (str(self.found.prefix), "d3d9=d"))

    def test_it_starts_from_the_control_center_folder(self):
        self.launch()
        self.wait_for_record()
        self.assertIn(f"cwd={self.found.prefix / 'drive_c/FlightSimLabs/ControlCenter'}", self.recorded())

    def test_arguments_go_to_the_program(self):
        self.launch("-NOTIFY", FSOL_SIM_RUNNING="1")
        self.wait_for_record()
        self.assertEqual(self.recorded()[0], f"{self.root}/cli run {PROGRAM} -NOTIFY")

    def test_a_copy_already_running_is_left_alone(self):
        self.run_program()
        result = self.launch(FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(any(line.startswith(f"{self.root}/cli run") for line in self.recorded()))
        self.assertTrue(any("already running" in line for line in self.recorded()))

    def test_a_control_center_that_is_not_installed_says_so_and_starts_nothing(self):
        (self.found.prefix / EXE).unlink()
        result = self.launch(FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not installed", result.stderr)
        self.assertFalse(any("cli run" in line for line in self.recorded()))

    def test_its_output_goes_nowhere_because_the_control_center_prints_the_activation_key(self):
        stub(self.root / "cli", recording(self.record, 'echo "key SECRET-KEY-1"\necho "key SECRET-KEY-1" >&2\n'))
        result = self.launch(FSOL_SIM_RUNNING="1")
        self.wait_for_record()
        time.sleep(0.3)
        self.assertNotIn("SECRET-KEY-1", result.stdout + result.stderr)
        cache = self.root / "cache"
        leaked = [path for path in cache.rglob("*") if path.is_file() and b"SECRET-KEY-1" in path.read_bytes()] if cache.exists() else []
        self.assertEqual(leaked, [])
        self.assertFalse((cache / "logs" / "fslabs-a321.log").exists())


PAGES = ROOT / "site" / "pages"


class PageTest(unittest.TestCase):
    def setUp(self):
        self.text = (PAGES / "fslabs-a321.html").read_text()

    def test_the_page_follows_the_template(self):
        self.assertEqual(page_problems("fslabs-a321"), [])

    def test_the_page_names_every_patch_the_check_looks_for(self):
        for patch in sorted(NINE_PATCHES):
            self.assertIn(patch.removesuffix(".patch"), self.text)

    def test_the_page_gives_the_numbers_as_measured(self):
        for number in ("24.6%", "790 a second", "6.6 ms", "0.005 ms", "6.7 microseconds", "2.10 ms", "0.44 ms", "0.98 ms", "0.36 ms", "981 microseconds", "9,657 of 49,889", "0.76%", "50 to 185 ms"):
            self.assertIn(number, self.text)

    def test_the_patches_not_seen_in_flight_are_written_as_expected(self):
        rows = {row.split("<code>")[1].split("</code>")[0]: row for row in self.text.split("<tr>") if "<code>" in row}
        for patch, wording in (("win32u-alpha-blend-sse2", "Not confirmed"), ("gdiplus-premultiply-sse2", "Not confirmed"), ("ntdll-heap-bounded-free-list-scan", "Not seen")):
            self.assertIn("Expected", rows[patch], patch)
            self.assertIn(wording, rows[patch], patch)
        for patch in ("gdiplus-nested-containers", "win32u-larger-font-caches", "win32u-banded-region-from-rects"):
            self.assertIn("Flown", rows[patch], patch)
            self.assertNotIn("Expected", rows[patch], patch)

    def test_the_page_says_the_service_has_not_been_run_with_an_fslabs_aircraft_and_exe_xml_is_the_plain_way(self):
        self.assertIn("has not yet been run with an FSLabs aircraft", self.text)
        self.assertIn("<code>exe.xml</code> is still the plain way", self.text)

    def test_the_page_names_the_commands_and_the_service_file(self):
        for needed in ("addon shortcut fslabs-a321", "addon watcher fslabs-a321", "flightsim-on-linux-fslabs-a321.service", "addon apply simconnect-tcp", "-NOTIFY", "156 MiB", "flightsimlabs.com"):
            self.assertIn(needed, self.text)

    def test_the_page_names_no_personal_path(self):
        for word in ("/home/", "brunom", "Jogos", "SteamLibrary"):
            self.assertNotIn(word, self.text)


class GpuWarningPageTest(unittest.TestCase):
    def setUp(self):
        self.text = (PAGES / "simulator-gpu-memory-warning.html").read_text()

    def test_the_page_follows_the_template(self):
        self.assertEqual(page_problems("simulator-gpu-memory-warning"), [])

    def test_the_page_says_the_files_it_changes_and_how_to_put_them_back(self):
        for needed in (
            "touches add-on files",
            "addon undo simulator-gpu-memory-warning",
            "Packages/fs-base-ui/html_ui/Global/notifications.html",
            "layout.json",
            "compatdata/2537590/flightsim-on-linux-backup/simulator-gpu-memory-warning",
            "NOTIF_LOW_GPU_MEMORY_CAPACITY",
            "flightsim-on-linux-gpu-memory-warning",
        ):
            self.assertIn(needed, self.text)

    def test_the_page_gives_the_measurements_of_29_october_and_does_not_promise_more_memory(self):
        for needed in ("10:36:08", "26.2 GB", "6.1 GB of RAM free", "20.0 to 20.2 GB", "hasn't been proven"):
            self.assertIn(needed, self.text)
        self.assertIn("doesn't give the simulator more memory", self.text)


if __name__ == "__main__":
    unittest.main()
