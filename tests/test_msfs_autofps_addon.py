import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from fsol import addons, desktop, watcher
from tests.support import process_stubs, stand_in_name, start_stand_in
from tests.test_addons import RecordingTools
from tests.test_machine import FakeMachine, write

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addons" / "msfs-autofps"
PATCH = "shell32-notifyicon-guid.patch"
MODULE = "lib/wine/x86_64-windows/shell32.dll"
PROGRAM = "C:\\users\\steamuser\\AppData\\Roaming\\MSFS_AutoFPS\\bin\\MSFS_AutoFPS.exe"
EXE = "drive_c/users/steamuser/AppData/Roaming/MSFS_AutoFPS/bin/MSFS_AutoFPS.exe"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def recording(file, after=""):
    return f'#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "{file}"\nenv >> "{file}.env"\n{after}'


def stub(path, text):
    write(path, text)
    path.chmod(0o755)
    return path


class AutoFpsCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake = FakeMachine(self.root).build()
        self.found = self.fake.discover()
        self.community = self.found.community
        self.proton = self.fake.tool
        self.record = self.root / "record"
        self.processes = []
        self.token = str(self.root)
        process_stubs(self.root / "bin", self.token)
        self.write_build(b"patched shell32")
        (self.community / "autofps-html-widget-panel").mkdir()

    def tearDown(self):
        for process in self.processes:
            process.kill()
            process.wait()
        self.temp.cleanup()

    def write_build(self, data, recorded=None, patch=None):
        module = self.proton / "files" / MODULE
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_bytes(data)
        patch_sha = patch or sha256((ROOT / "patches" / "wine" / PATCH).read_bytes())
        record = ["release cachyos-11.0-20261005-slr x86_64", f"patch {patch_sha} {PATCH}", f"module {recorded or sha256(data)} {MODULE}"]
        (self.proton / "fs2024-build.txt").write_text("\n".join(record) + "\n")

    def environment(self, **extra):
        env = {
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "FSOL_PREFIX": str(self.found.prefix),
            "FSOL_PROTON": str(self.found.proton),
            "FSOL_COMMUNITY": str(self.community),
            "FSOL_BASE": str(self.found.base),
            "FSOL_CACHE": str(self.root / "cache"),
            "FSOL_CLI": str(self.root / "cli"),
            "FSOL_ADDON": "msfs-autofps",
            "FSOL_SIM_RUNNING": "0",
            "FSOL_CLOSE_WAIT": "1",
        }
        env.update(extra)
        return env

    def script(self, name, *args, **extra):
        return subprocess.run([str(ADDON / name), *args], env=self.environment(**extra), capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def stubs(self, wine_exit=0, after=""):
        stub(self.root / "bin" / "notify-send", recording(self.record))
        stub(self.root / "cli", recording(self.record, after))
        stub(self.proton / "files/bin/wine", recording(self.record, after) + f"exit {wine_exit}\n")
        stub(self.proton / "files/bin/wineserver", recording(f"{self.record}.server"))

    def recorded(self):
        return self.record.read_text().splitlines() if self.record.exists() else []

    def recorded_env(self):
        path = Path(f"{self.record}.env")
        return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line) if path.exists() else {}

    def wait_for_record(self, seconds=5):
        deadline = time.monotonic() + seconds
        while not self.record.exists() and time.monotonic() < deadline:
            time.sleep(0.05)

    def install_program(self):
        write(self.found.prefix / EXE, "MZ")

    def run_program(self):
        process = start_stand_in(self.root, self.token, stand_in_name(PROGRAM), 60)
        self.processes.append(process)
        time.sleep(0.2)
        return process


class ShapeTest(AutoFpsCase):
    def test_the_manifest_is_complete(self):
        addon = addons.load(ADDON)
        self.assertEqual((addon.id, addon.needs, addon.tested_version, addon.tested_on), ("msfs-autofps", ["proton-fs2024"], "0.5.4.4", "2026-10-16"))
        self.assertEqual(addon.install, {"from": "https://github.com/ResetXPDR/MSFS_AutoFPS/releases", "paid": None, "kind": "installer"})
        self.assertEqual([shortcut["title"] for shortcut in addon.shortcuts], ["MSFS AutoFPS"])
        self.assertEqual((addon.watcher["stop"], addon.watcher["grace"], addon.watcher["script"]), ("with_simulator", 20, "launch"))
        self.assertTrue(addon.can_install)

    def test_the_watcher_pattern_matches_the_program_and_not_other_programs(self):
        pattern = addons.load(ADDON).watcher["app"]
        self.assertTrue(re.search(pattern, PROGRAM + " --minimized"))
        self.assertFalse(re.search(pattern, "C:\\Program Files\\Other\\Other.exe"))
        self.assertFalse(re.search(pattern, "/usr/bin/python3 flightsim-on-linux addon watch msfs-autofps"))

    def test_it_counts_as_installed_where_the_program_is(self):
        addon = addons.load(ADDON)
        self.assertFalse(addons.is_installed(addon, self.found))
        self.install_program()
        self.assertTrue(addons.is_installed(addon, self.found))

    def test_the_menu_entry_it_writes_passes_the_desktop_validator(self):
        addon = addons.load(ADDON)
        tools = RecordingTools()
        self.install_program()
        written = desktop.write_shortcuts(addon, self.found, ROOT / "bin" / "flightsim-on-linux", self.root / "data", tools, lambda line: None)
        text = written[0].read_text()
        self.assertIn(f'Exec="{ROOT / "bin" / "flightsim-on-linux"}" addon launch msfs-autofps launch', text)
        self.assertIn("Actions=close;", text)
        self.assertIn("Name=Close MSFS AutoFPS", text)
        if shutil.which("desktop-file-validate"):
            result = subprocess.run(["desktop-file-validate", str(written[0])], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""))

    @unittest.skipUnless(shutil.which("systemd-analyze"), "needs systemd-analyze")
    def test_the_service_it_writes_passes_systemd_verify(self):
        path = self.root / "unit.service"
        path.write_text(watcher.unit_text(addons.load(ADDON), ROOT / "bin" / "flightsim-on-linux"))
        result = subprocess.run(["systemd-analyze", "--user", "verify", str(path)], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""))
        self.assertIn("KillMode=process", path.read_text())


class CheckTest(AutoFpsCase):
    def assert_only_failure(self, word):
        result = self.script("check")
        lines = result.stdout.splitlines()
        self.assertEqual(result.returncode, 1, lines)
        failures = [line for line in lines if line.startswith("FAIL")]
        self.assertEqual(len(failures), 1, lines)
        self.assertIn(word, failures[0])
        self.assertEqual(len(lines), 2)

    def test_a_machine_with_the_patch_and_a_real_widget_folder_passes_with_two_ok_lines(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok", "ok"])

    def test_a_proton_without_the_notify_icon_patch_in_its_build_record_fails(self):
        self.write_build(b"patched shell32", patch="0" * 64)
        self.assert_only_failure(PATCH)

    def test_a_shell32_that_differs_from_the_build_record_fails(self):
        self.write_build(b"stock shell32", recorded=sha256(b"patched shell32"))
        self.assert_only_failure("shell32.dll")

    def test_a_proton_without_a_build_record_fails(self):
        (self.proton / "fs2024-build.txt").unlink()
        self.assert_only_failure("proton build")

    def test_a_widget_that_is_a_linux_link_fails(self):
        widget = self.community / "autofps-html-widget-panel"
        widget.rmdir()
        target = self.root / "elsewhere"
        target.mkdir()
        os.symlink(target, widget)
        self.assert_only_failure("Linux link")

    def test_a_widget_that_is_missing_fails(self):
        (self.community / "autofps-html-widget-panel").rmdir()
        self.assert_only_failure("does not exist")

    def test_no_community_folder_fails_instead_of_reading_the_root(self):
        result = self.script("check", FSOL_COMMUNITY="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL no Community folder found", result.stdout)

    def test_no_proton_fails_instead_of_reading_the_current_folder(self):
        result = self.script("check", FSOL_PROTON="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL no Proton found", result.stdout)

    def test_check_changes_nothing(self):
        before = sorted(str(path) for path in self.root.rglob("*"))
        self.script("check")
        self.assertEqual(sorted(str(path) for path in self.root.rglob("*")), before)


class ApplyAndUndoTest(AutoFpsCase):
    def test_apply_and_undo_say_there_is_nothing_to_do_and_change_nothing(self):
        before = sorted(str(path) for path in self.root.rglob("*"))
        for name in ("apply", "undo"):
            result = self.script(name)
            self.assertEqual(result.returncode, 0)
            self.assertIn("already nothing to", result.stdout)
        self.assertEqual(sorted(str(path) for path in self.root.rglob("*")), before)


class InstallTest(AutoFpsCase):
    def setUp(self):
        super().setUp()
        self.stubs()
        self.setup = self.root / "downloads" / "MSFS_AutoFPS-installer-v0.5.4.4.exe"
        write(self.setup, "MZ")

    def test_the_installer_runs_alone_in_the_prefix_with_direct3d_9_off(self):
        result = self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {self.setup}"])
        env = self.recorded_env()
        self.assertEqual((env["WINEPREFIX"], env["WINEDLLOVERRIDES"]), (str(self.found.prefix), "d3d9=d"))
        self.assertTrue(Path(f"{self.record}.server").exists())

    def test_a_linux_link_in_place_of_the_widget_is_removed_first_and_said(self):
        widget = self.community / "autofps-html-widget-panel"
        widget.rmdir()
        target = self.root / "organizer" / "autofps-html-widget-panel"
        write(target / "layout.json", "{}")
        os.symlink(target, widget)
        result = self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(widget.is_symlink() or widget.exists())
        self.assertTrue((target / "layout.json").exists())
        self.assertIn(f"removed the link {widget}, which pointed to {target}", result.stdout)
        self.assertEqual(len(self.recorded()), 1)

    def test_a_real_widget_folder_is_never_touched(self):
        widget = self.community / "autofps-html-widget-panel"
        write(widget / "layout.json", "{}")
        result = self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 0)
        self.assertTrue((widget / "layout.json").exists())
        self.assertNotIn("removed the link", result.stdout)

    def test_the_installer_exit_code_is_the_script_exit_code(self):
        self.stubs(wine_exit=3)
        self.assertEqual(self.script("install", FSOL_INSTALLER=str(self.setup)).returncode, 3)

    def test_without_an_installer_the_script_says_how_to_give_one(self):
        result = self.script("install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("flightsim-on-linux addon install msfs-autofps", result.stderr)
        self.assertEqual(self.recorded(), [])

    def test_the_variable_that_breaks_wine_installers_does_not_reach_them(self):
        self.script("install", FSOL_INSTALLER=str(self.setup), NoDefaultCurrentDirectoryInExePath="1")
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", self.recorded_env())


class LaunchTest(AutoFpsCase):
    def setUp(self):
        super().setUp()
        self.stubs()
        self.install_program()

    def test_a_closed_simulator_stops_it_with_a_notice_and_starts_nothing(self):
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("start Microsoft Flight Simulator 2024 first", result.stderr)
        self.assertFalse(any(line.startswith(f"{self.root}/cli run") for line in self.recorded()))
        self.assertTrue(any("start Microsoft Flight Simulator 2024 first" in line for line in self.recorded()))

    def test_a_copy_already_running_is_left_alone(self):
        self.run_program()
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(any(line.startswith(f"{self.root}/cli run") for line in self.recorded()))
        self.assertTrue(any("already running" in line for line in self.recorded()))

    def test_an_addon_that_is_not_installed_says_how_to_install_it(self):
        (self.found.prefix / EXE).unlink()
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install msfs-autofps", result.stderr)

    def test_it_starts_inside_the_simulators_container_with_direct3d_9_off_and_the_whole_card(self):
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.root}/cli run DXVK_CONFIG= {PROGRAM}"])
        self.assertEqual(self.recorded_env()["EXTRA_WINEDLLOVERRIDES"], "d3d9=d")

    def test_arguments_go_to_the_program(self):
        self.script("launch", "-autostart", FSOL_SIM_RUNNING="1")
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.root}/cli run DXVK_CONFIG= {PROGRAM} -autostart"])

    def test_its_output_goes_to_a_private_log_under_the_cache(self):
        self.script("launch", FSOL_SIM_RUNNING="1")
        log = self.root / "cache" / "logs" / "msfs-autofps.log"
        self.assertTrue(log.is_file())
        self.assertEqual(oct(log.stat().st_mode & 0o777), "0o600")


class CloseTest(AutoFpsCase):
    def test_nothing_running_means_nothing_to_close(self):
        self.stubs()
        result = self.script("close")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "AutoFPS is not running"))
        self.assertEqual(self.recorded(), [])

    def test_with_the_simulator_running_the_window_is_asked_to_close_inside_the_container_and_nothing_is_forced(self):
        program = self.run_program()
        self.stubs(after=f"kill {program.pid}\n")
        result = self.script("close", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.root}/cli run taskkill /im MSFS_AutoFPS.exe"])

    def test_with_the_simulator_closed_the_window_is_asked_to_close_through_the_prefix(self):
        program = self.run_program()
        self.stubs(after=f"kill {program.pid}\n")
        result = self.script("close")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine taskkill /im MSFS_AutoFPS.exe"])

    def test_a_program_that_stays_after_the_request_is_reported_and_never_killed(self):
        program = self.run_program()
        self.stubs()
        result = self.script("close", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("still running", result.stderr)
        self.assertIn("Exit in its tray menu", result.stderr)
        self.assertIsNone(program.poll())

    def test_the_request_never_carries_the_force_flag(self):
        program = self.run_program()
        self.stubs(after=f"kill {program.pid}\n")
        self.script("close", FSOL_SIM_RUNNING="1")
        self.assertNotIn("/f", " ".join(self.recorded()).split())


if __name__ == "__main__":
    unittest.main()
