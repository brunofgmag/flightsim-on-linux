import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fsol import addons, desktop
from tests.support import process_stubs, stand_in_name, start_stand_in
from tests.test_addons import RecordingTools
from tests.test_machine import FakeMachine, write

ROOT = Path(__file__).resolve().parent.parent
LOCAL = "drive_c/users/steamuser/AppData/Local"
HUB_FOLDER = f"{LOCAL}/Programs/navigraph-hub"
HUB_WINDOWS = "C:\\users\\steamuser\\AppData\\Local\\Programs\\navigraph-hub\\Navigraph Hub.exe"
HUB_PATTERN = r"^[A-Za-z]:.*\\navigraph-hub\\Navigraph Hub\.exe"
CONTRAIL_FOLDER = f"{LOCAL}/ContrailApp"
CONTRAIL_PATTERN = r"^[A-Za-z]:.*\\ContrailApp\\app-[^\\]*\\Contrail\.exe"
SIMMARKET_FOLDER = f"{LOCAL}/simMarket"
SIMMARKET_PATTERN = r"^[A-Za-z]:.*\\simMarket\\app-[^\\]*\\simMarket\.exe"
ORBX_FOLDER = f"{LOCAL}/Programs/orbx-central"
ORBX_PATTERN = r"^[A-Za-z]:.*\\orbx-central\\Orbx Central\.exe"
ORBX_CLOSE_PATTERN = r"^[A-Za-z]:.*\\orbx-central\\(Orbx Central\.exe|saturn-win\\)"
TRITRISIM_FOLDER = f"{LOCAL}/TriTriSim Hub"
TRITRISIM_PATTERN = r"^[A-Za-z]:.*\\TriTriSim Hub\\tritrisim-hub\.exe"
GET_RECT_PATCH = "shell32-notifyicon-get-rect.patch"
GET_RECT_MODULES = ("lib/wine/x86_64-windows/shell32.dll", "lib/wine/x86_64-windows/explorer.exe")
SHIM_PROCGOV = b"MZ stand-in for the procgov shim, with navigraph-7z- inside"
SHIM_WINHTTP = b"MZ stand-in for the winhttp proxy, forwarding to winhttp_wine.DllCanUnloadNow"
SHIM_OLETR = b"MZ stand-in for the oletr proxy"
WINE_WINHTTP = b"MZ the winhttp.dll of Proton"
TOKEN = "SECRET-TOKEN-DO-NOT-PRINT"
STORE_IDS = ("navigraph-hub", "contrail", "simmarket", "orbx-central", "tritrisim-hub", "aerosoft-one", "flybywire-installer")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def recording(file, after=""):
    return f'#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "{file}"\nenv >> "{file}.env"\n{after}'


def stub(path, text):
    write(path, text)
    path.chmod(0o755)
    return path


def tree(root):
    return sorted(str(path.relative_to(root)) + (":" + sha256(path.read_bytes()) if path.is_file() and not path.is_symlink() else "") for path in root.rglob("*"))


class StoreCase(unittest.TestCase):
    ADDON = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake = FakeMachine(self.root).build()
        self.found = self.fake.discover()
        self.prefix = self.found.prefix
        self.proton = self.fake.tool
        self.record = self.root / "record"
        self.processes = []
        self.token = str(self.root)

    def tearDown(self):
        for process in self.processes:
            process.kill()
            process.wait()
        self.temp.cleanup()

    def environment(self, **extra):
        env = {
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.fake.home),
            "FSOL_PREFIX": str(self.prefix),
            "FSOL_PROTON": str(self.found.proton),
            "FSOL_COMPATDATA": str(self.found.compatdata),
            "FSOL_COMMUNITY": str(self.found.community),
            "FSOL_PACKAGES": str(self.found.packages),
            "FSOL_BASE": str(self.found.base),
            "FSOL_SIM_INSTALL": str(self.found.sim_install),
            "FSOL_CACHE": str(self.root / "cache"),
            "FSOL_CLI": str(self.root / "cli"),
            "FSOL_ADDON": self.ADDON,
            "FSOL_SIM_RUNNING": "0",
            "FSOL_CLOSE_WAIT": "1",
            "FAKE_PROCESSES": str(self.root / "processes"),
        }
        env.update(extra)
        return env

    def script(self, name, *args, **extra):
        return subprocess.run([str(ROOT / "addons" / self.ADDON / name), *args], env=self.environment(**extra), capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def stubs(self, wine_exit=0):
        write(self.root / "processes", "")
        stub(self.root / "bin" / "pgrep", '#!/bin/sh\nfor last; do :; done\ngrep -qE -- "$last" "$FAKE_PROCESSES"\n')
        stub(self.root / "bin" / "pkill", f'#!/bin/sh\nprintf "%s\\n" "pkill $*" >> "{self.record}.pkill"\nfor last; do :; done\ngrep -vE -- "$last" "$FAKE_PROCESSES" > "$FAKE_PROCESSES.new"\nmv "$FAKE_PROCESSES.new" "$FAKE_PROCESSES"\n')
        stub(self.root / "bin" / "notify-send", recording(self.record))
        stub(self.root / "cli", recording(self.record))
        stub(self.proton / "files/bin/wine", recording(self.record) + f"exit {wine_exit}\n")
        stub(self.proton / "files/bin/wineserver", recording(f"{self.record}.server"))

    def recorded(self):
        return self.record.read_text().splitlines() if self.record.exists() else []

    def programs_run(self):
        notify = str(self.root / "bin" / "notify-send")
        return [line for line in self.recorded() if not line.startswith(notify)]

    def recorded_env(self):
        path = Path(f"{self.record}.env")
        return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line) if path.exists() else {}

    def wait_for_record(self, seconds=5):
        deadline = time.monotonic() + seconds
        while not self.record.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.1)

    def stage_shim(self, name, data):
        write(self.root / "cache" / "shims" / name, "")
        (self.root / "cache" / "shims" / name).write_bytes(data)

    def pkill_calls(self):
        path = Path(f"{self.record}.pkill")
        return path.read_text().splitlines() if path.exists() else []

    def fake_process(self, cmdline):
        with open(self.root / "processes", "a") as file:
            file.write(cmdline + "\n")

    def processes_left(self):
        return (self.root / "processes").read_text().splitlines()

    def run_program(self, argv0):
        process_stubs(self.root / "bin", self.token)
        process = start_stand_in(self.root, self.token, stand_in_name(argv0), 60)
        self.processes.append(process)
        time.sleep(0.2)
        return process

    def put(self, relative, data):
        path = self.prefix / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def backup(self, *parts):
        return self.found.compatdata.joinpath("flightsim-on-linux-backup", *parts)


class ShapeTest(unittest.TestCase):
    def test_every_store_add_on_loads_and_says_what_it_is(self):
        expected = {
            "navigraph-hub": (True, "installer", ["Navigraph Hub"]),
            "contrail": (True, "installer", ["Contrail"]),
            "simmarket": (False, "installer", ["simMarket"]),
            "orbx-central": (False, "installer", ["Orbx Central"]),
            "tritrisim-hub": (True, "installer", ["TriTriSim Hub"]),
            "aerosoft-one": (False, "native", []),
            "flybywire-installer": (False, "native", []),
        }
        for addon_id, (touches, kind, titles) in expected.items():
            with self.subTest(addon_id):
                addon = addons.load(ROOT / "addons" / addon_id)
                self.assertEqual((addon.touches_addon_files, addon.install["kind"], [s["title"] for s in addon.shortcuts]), (touches, kind, titles))
                self.assertIsNone(addon.watcher)
                self.assertRegex(addon.tested_on, r"^2026-10-\d\d$")
                self.assertEqual(addon.can_install, kind == "installer")

    def test_no_install_source_is_a_guessed_download_link(self):
        for addon_id in STORE_IDS:
            with self.subTest(addon_id):
                source = addons.load(ROOT / "addons" / addon_id).install["from"]
                self.assertNotRegex(source, r"https?://")

    def test_the_menu_entries_pass_the_desktop_validator(self):
        for addon_id in ("navigraph-hub", "contrail", "simmarket", "orbx-central", "tritrisim-hub"):
            with self.subTest(addon_id), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                fake = FakeMachine(root).build()
                addon = addons.load(ROOT / "addons" / addon_id)
                written = desktop.write_shortcuts(addon, fake.discover(), ROOT / "bin" / "flightsim-on-linux", root / "data", RecordingTools(), lambda line: None)
                text = written[0].read_text()
                self.assertIn(f'addon launch {addon_id} launch', text)
                self.assertIn("Actions=close;", text)
                if shutil.which("desktop-file-validate"):
                    result = subprocess.run(["desktop-file-validate", str(written[0])], capture_output=True, text=True)
                    self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""))

    def test_the_globs_that_say_installed_find_the_programs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = FakeMachine(root).build()
            found = fake.discover()
            cases = {
                "navigraph-hub": f"{HUB_FOLDER}/Navigraph Hub.exe",
                "contrail": f"{CONTRAIL_FOLDER}/app-3.0.0/Contrail.exe",
                "simmarket": f"{SIMMARKET_FOLDER}/app-4.20.0/simMarket.exe",
                "orbx-central": f"{ORBX_FOLDER}/Orbx Central.exe",
                "tritrisim-hub": f"{TRITRISIM_FOLDER}/tritrisim-hub.exe",
            }
            for addon_id, relative in cases.items():
                addon = addons.load(ROOT / "addons" / addon_id)
                self.assertFalse(addons.is_installed(addon, found), addon_id)
                write(found.prefix / relative, "MZ")
                self.assertTrue(addons.is_installed(addon, found), addon_id)
            for addon_id, relative in (("aerosoft-one", "aerosoft-a346-pro"), ("flybywire-installer", "fsltl-traffic-base")):
                addon = addons.load(ROOT / "addons" / addon_id)
                self.assertFalse(addons.is_installed(addon, found), addon_id)
                (found.community / relative).mkdir()
                self.assertTrue(addons.is_installed(addon, found), addon_id)


class NavigraphHubTest(StoreCase):
    ADDON = "navigraph-hub"

    def setUp(self):
        super().setUp()
        self.exe = self.put(f"{HUB_FOLDER}/Navigraph Hub.exe", b"MZ hub")
        self.procgov = self.put(f"{HUB_FOLDER}/resources/assets/bin/procgov.exe", b"MZ the hub's own procgov")
        self.stage_shim("procgov.exe", SHIM_PROCGOV)
        self.orig = self.backup("navigraph-hub", "procgov.exe.orig")

    def test_apply_puts_the_shim_in_place_and_keeps_the_original(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.procgov.read_bytes(), SHIM_PROCGOV)
        self.assertEqual(self.orig.read_bytes(), b"MZ the hub's own procgov")
        self.assertTrue(os.access(self.procgov, os.X_OK))

    def test_apply_twice_changes_nothing_the_second_time(self):
        self.script("apply")
        before = tree(self.root)
        result = self.script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertIn("already applied", result.stdout)
        self.assertEqual(tree(self.root), before)

    def test_apply_after_a_hub_update_keeps_the_new_original(self):
        self.script("apply")
        self.procgov.write_bytes(b"MZ procgov of the new hub")
        self.script("apply")
        self.assertEqual(self.orig.read_bytes(), b"MZ procgov of the new hub")
        self.assertEqual(self.procgov.read_bytes(), SHIM_PROCGOV)

    def test_apply_over_an_older_build_of_the_shim_keeps_the_real_original(self):
        self.script("apply")
        self.stage_shim("procgov.exe", SHIM_PROCGOV + b" newer build")
        result = self.script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.orig.read_bytes(), b"MZ the hub's own procgov")
        self.assertEqual(self.procgov.read_bytes(), SHIM_PROCGOV + b" newer build")

    def test_apply_without_the_hub_says_to_install_it(self):
        self.procgov.unlink()
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install navigraph-hub", result.stderr)

    def test_check_passes_after_apply_with_two_ok_lines(self):
        self.script("apply")
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok", "ok"])

    def test_check_fails_when_the_hub_has_restored_its_own_procgov(self):
        self.script("apply")
        self.procgov.write_bytes(b"MZ the hub's own procgov")
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stdout, r"FAIL procgov\.exe is not the shim")

    def test_check_fails_without_the_fix_applied(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL procgov.exe is not the shim", result.stdout)
        self.assertIn("FAIL no copy of the original", result.stdout)

    def test_check_fails_when_the_copy_of_the_original_is_gone(self):
        self.script("apply")
        self.orig.unlink()
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok", "FAIL"])

    def test_check_changes_nothing(self):
        self.script("apply")
        before = tree(self.root)
        self.script("check")
        self.assertEqual(tree(self.root), before)

    def test_undo_gives_the_hub_its_own_procgov_back(self):
        self.script("apply")
        result = self.script("undo")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.procgov.read_bytes(), b"MZ the hub's own procgov")
        self.assertFalse(self.orig.exists())

    def test_undo_without_a_copy_of_the_original_refuses_while_the_shim_is_still_there(self):
        self.procgov.write_bytes(SHIM_PROCGOV)
        result = self.script("undo")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.procgov.read_bytes(), SHIM_PROCGOV)

    def test_undo_on_a_hub_that_is_not_the_shim_leaves_the_file_and_drops_the_backup_saying_so(self):
        self.script("apply")
        updated = b"MZ procgov of the new hub"
        self.procgov.write_bytes(updated)
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.procgov.read_bytes(), updated)
        self.assertFalse(self.orig.exists())
        self.assertIn("is not the shim", result.stdout)
        self.assertEqual(self.script("undo").returncode, 0)


class NavigraphHubRunTest(StoreCase):
    ADDON = "navigraph-hub"

    def setUp(self):
        super().setUp()
        self.put(f"{HUB_FOLDER}/Navigraph Hub.exe", b"MZ hub")
        self.stubs()

    def test_install_runs_the_installer_alone_in_the_prefix(self):
        setup = self.root / "downloads" / "Navigraph Hub.exe"
        write(setup, "MZ")
        result = self.script("install", FSOL_INSTALLER=str(setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {setup}"])
        self.assertTrue(Path(f"{self.record}.server").exists())

    def test_launch_starts_the_hub_with_the_gpu_sandbox_off(self):
        result = self.script("launch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {HUB_WINDOWS} --disable-gpu-sandbox"])
        self.assertEqual(self.recorded_env()["WINEPREFIX"], str(self.prefix))

    def test_launch_with_the_simulator_running_goes_through_the_container(self):
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.root / 'cli'} run {HUB_WINDOWS} --disable-gpu-sandbox"])

    def test_launch_does_nothing_when_the_hub_is_already_open(self):
        self.fake_process(HUB_WINDOWS + " --disable-gpu-sandbox")
        result = self.script("launch")
        self.assertEqual(result.returncode, 0)
        time.sleep(0.3)
        self.assertEqual(self.programs_run(), [])

    def test_launch_without_the_hub_says_how_to_install_it(self):
        (self.prefix / HUB_FOLDER / "Navigraph Hub.exe").unlink()
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install navigraph-hub", result.stderr)

    def test_close_ends_the_hub_with_sigterm_and_never_with_kill(self):
        process = self.run_program(HUB_WINDOWS)
        result = self.script("close")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(process.wait(timeout=5), -signal.SIGTERM)

    def test_close_asks_pkill_for_a_plain_term_on_the_hubs_processes(self):
        self.fake_process(HUB_WINDOWS + " --disable-gpu-sandbox")
        self.fake_process(HUB_WINDOWS + " --type=renderer")
        result = self.script("close")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.pkill_calls(), [f"pkill -TERM -f -- {HUB_PATTERN}"])
        self.assertEqual(self.processes_left(), [])

    def test_close_when_the_hub_is_not_running_says_so(self):
        result = self.script("close")
        self.assertEqual(result.returncode, 0)
        self.assertIn("is not running", result.stdout)


class ContrailTest(StoreCase):
    ADDON = "contrail"

    def setUp(self):
        super().setUp()
        self.app = self.prefix / CONTRAIL_FOLDER / "app-2.4.6"
        self.put(f"{CONTRAIL_FOLDER}/app-2.4.6/Contrail.exe", b"MZ contrail 2.4.6")
        write(self.proton / "files/lib/wine/x86_64-windows/winhttp.dll", "")
        (self.proton / "files/lib/wine/x86_64-windows/winhttp.dll").write_bytes(WINE_WINHTTP)
        self.stage_shim("winhttp.dll", SHIM_WINHTTP)
        self.stubs()

    def test_apply_puts_the_proxy_and_proton_winhttp_in_the_app_folder(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.app / "winhttp.dll").read_bytes(), SHIM_WINHTTP)
        self.assertEqual((self.app / "winhttp_wine.dll").read_bytes(), WINE_WINHTTP)

    def test_apply_goes_to_the_newest_app_folder_by_version_not_by_name(self):
        self.put(f"{CONTRAIL_FOLDER}/app-3.0.0/Contrail.exe", b"MZ")
        self.put(f"{CONTRAIL_FOLDER}/app-10.0.0/Contrail.exe", b"MZ")
        self.put(f"{CONTRAIL_FOLDER}/app-9.9.9/Contrail.exe", b"MZ")
        self.script("apply")
        self.assertTrue((self.prefix / CONTRAIL_FOLDER / "app-10.0.0" / "winhttp.dll").exists())
        for older in ("app-9.9.9", "app-3.0.0", "app-2.4.6"):
            self.assertFalse((self.prefix / CONTRAIL_FOLDER / older / "winhttp.dll").exists(), older)

    def test_check_passes_after_apply_with_two_ok_lines(self):
        self.script("apply")
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok", "ok"])

    def test_check_fails_without_the_fix(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["FAIL", "FAIL"])

    def test_check_fails_when_the_proton_winhttp_changed_after_the_copy(self):
        self.script("apply")
        (self.proton / "files/lib/wine/x86_64-windows/winhttp.dll").chmod(0o644)
        (self.proton / "files/lib/wine/x86_64-windows/winhttp.dll").write_bytes(b"MZ a rebuilt winhttp")
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok", "FAIL"])

    def test_check_fails_for_an_older_build_of_the_proxy(self):
        self.script("apply")
        self.stage_shim("winhttp.dll", SHIM_WINHTTP + b" newer")
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("older build", result.stdout)

    def test_check_looks_at_the_newest_folder_after_a_self_update(self):
        self.script("apply")
        self.put(f"{CONTRAIL_FOLDER}/app-3.0.0/Contrail.exe", b"MZ contrail 3.0.0")
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("app-3.0.0", result.stdout)

    def test_check_changes_nothing(self):
        self.script("apply")
        before = tree(self.root)
        self.script("check")
        self.assertEqual(tree(self.root), before)

    def test_a_winhttp_dll_the_app_shipped_is_kept_and_comes_back_on_undo(self):
        own = self.put(f"{CONTRAIL_FOLDER}/app-2.4.6/winhttp.dll", b"MZ the app's own winhttp")
        self.script("apply")
        self.assertEqual(own.read_bytes(), SHIM_WINHTTP)
        self.assertEqual(self.backup("contrail", "app-2.4.6", "winhttp.dll").read_bytes(), b"MZ the app's own winhttp")
        self.script("undo")
        self.assertEqual(own.read_bytes(), b"MZ the app's own winhttp")
        self.assertFalse((self.app / "winhttp_wine.dll").exists())

    def test_apply_twice_does_not_mistake_its_own_proxy_for_the_apps(self):
        self.script("apply")
        self.script("apply")
        self.assertFalse(self.backup("contrail").exists())

    def test_undo_takes_the_files_out_of_every_app_folder(self):
        self.put(f"{CONTRAIL_FOLDER}/app-3.0.0/Contrail.exe", b"MZ")
        self.script("apply")
        self.script("launch")
        self.wait_for_record()
        result = self.script("undo")
        self.assertEqual(result.returncode, 0)
        for folder in ("app-2.4.6", "app-3.0.0"):
            self.assertEqual(sorted(path.name for path in (self.prefix / CONTRAIL_FOLDER / folder).iterdir()), ["Contrail.exe"], folder)

    def test_launch_copies_both_files_into_the_newest_folder_and_starts_it_there(self):
        self.script("apply")
        self.put(f"{CONTRAIL_FOLDER}/app-3.0.0/Contrail.exe", b"MZ contrail 3.0.0")
        result = self.script("launch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        newest = self.prefix / CONTRAIL_FOLDER / "app-3.0.0"
        self.assertEqual((newest / "winhttp.dll").read_bytes(), SHIM_WINHTTP)
        self.assertEqual((newest / "winhttp_wine.dll").read_bytes(), WINE_WINHTTP)
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine C:\\users\\steamuser\\AppData\\Local\\ContrailApp\\app-3.0.0\\Contrail.exe"])
        self.assertEqual(self.recorded_env()["WINEDLLOVERRIDES"], "d3d9=d;winhttp=n,b")

    def test_launch_with_the_simulator_running_asks_the_container_for_the_overrides(self):
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.root / 'cli'} run C:\\users\\steamuser\\AppData\\Local\\ContrailApp\\app-2.4.6\\Contrail.exe"])
        self.assertEqual(self.recorded_env()["EXTRA_WINEDLLOVERRIDES"], "d3d9=d;winhttp=n,b")

    def test_launch_does_nothing_when_contrail_is_already_open(self):
        self.fake_process("C:\\users\\steamuser\\AppData\\Local\\ContrailApp\\app-2.4.6\\Contrail.exe")
        result = self.script("launch")
        self.assertEqual(result.returncode, 0)
        time.sleep(0.3)
        self.assertEqual(self.programs_run(), [])
        self.assertFalse((self.app / "winhttp.dll").exists())

    def test_close_asks_pkill_for_a_plain_term_on_contrails_processes(self):
        self.fake_process("C:\\users\\steamuser\\AppData\\Local\\ContrailApp\\app-3.0.0\\Contrail.exe")
        result = self.script("close")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.pkill_calls(), [f"pkill -TERM -f -- {CONTRAIL_PATTERN}"])
        self.assertEqual(self.processes_left(), [])

    def test_close_ends_contrail_with_sigterm(self):
        process = self.run_program("C:\\users\\steamuser\\AppData\\Local\\ContrailApp\\app-3.0.0\\Contrail.exe")
        result = self.script("close")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(process.wait(timeout=5), -signal.SIGTERM)

    def test_install_runs_the_installer_alone(self):
        setup = self.root / "downloads" / "ContrailSetup.exe"
        write(setup, "MZ")
        result = self.script("install", FSOL_INSTALLER=str(setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {setup}"])


class SimMarketTest(StoreCase):
    ADDON = "simmarket"

    def setUp(self):
        super().setUp()
        self.put(f"{SIMMARKET_FOLDER}/app-4.19.0/simMarket.exe", b"MZ")
        self.put(f"{SIMMARKET_FOLDER}/app-4.20.0/simMarket.exe", b"MZ")
        self.stubs()

    def test_apply_and_undo_change_nothing(self):
        before = tree(self.root)
        for name in ("apply", "undo"):
            result = self.script(name)
            self.assertEqual(result.returncode, 0)
            self.assertIn("already nothing to", result.stdout)
        self.assertEqual(tree(self.root), before)

    def test_check_passes_with_the_program_there_and_fails_without_it(self):
        self.assertEqual(self.script("check").returncode, 0)
        shutil.rmtree(self.prefix / SIMMARKET_FOLDER)
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL simMarket is not installed", result.stdout)

    def test_launch_starts_the_newest_app_with_the_sandbox_off_and_the_toast_stub_off(self):
        result = self.script("launch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine C:\\users\\steamuser\\AppData\\Local\\simMarket\\app-4.20.0\\simMarket.exe --disable-gpu-sandbox"])
        self.assertEqual(self.recorded_env()["WINEDLLOVERRIDES"], "toaststub=d")

    def test_launch_keeps_the_machine_choices_out(self):
        text = (ROOT / "addons" / "simmarket" / "launch").read_text()
        for owner_only in ("VK_LOADER_DRIVERS_SELECT", "FILTER_DEVICE_NAME", "radeon", "RADV"):
            self.assertNotIn(owner_only, text)

    def test_launch_with_the_simulator_running_goes_through_the_container(self):
        self.script("launch", FSOL_SIM_RUNNING="1")
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.root / 'cli'} run C:\\users\\steamuser\\AppData\\Local\\simMarket\\app-4.20.0\\simMarket.exe --disable-gpu-sandbox"])
        self.assertEqual(self.recorded_env()["EXTRA_WINEDLLOVERRIDES"], "toaststub=d")

    def test_close_asks_pkill_for_a_plain_term_on_its_processes(self):
        self.fake_process("C:\\users\\steamuser\\AppData\\Local\\simMarket\\app-4.20.0\\simMarket.exe --type=gpu-process")
        result = self.script("close")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.pkill_calls(), [f"pkill -TERM -f -- {SIMMARKET_PATTERN}"])
        self.assertEqual(self.processes_left(), [])

    def test_close_ends_it_with_sigterm(self):
        process = self.run_program("C:\\users\\steamuser\\AppData\\Local\\simMarket\\app-4.20.0\\simMarket.exe")
        result = self.script("close")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(process.wait(timeout=5), -signal.SIGTERM)

    def test_install_runs_the_installer_alone(self):
        setup = self.root / "downloads" / "simMarket-4.20.0.win-x64.exe"
        write(setup, "MZ")
        result = self.script("install", FSOL_INSTALLER=str(setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {setup}"])
        self.assertNotIn("WINEDLLOVERRIDES", self.recorded_env())


class OrbxCentralTest(StoreCase):
    ADDON = "orbx-central"

    def setUp(self):
        super().setUp()
        self.put(f"{ORBX_FOLDER}/Orbx Central.exe", b"MZ")
        self.stubs()

    def test_apply_and_undo_change_nothing(self):
        before = tree(self.root)
        for name in ("apply", "undo"):
            self.assertEqual(self.script(name).returncode, 0)
        self.assertEqual(tree(self.root), before)

    def test_check_wants_the_windows_build_in_the_prefix(self):
        self.assertEqual(self.script("check").returncode, 0)
        (self.prefix / ORBX_FOLDER / "Orbx Central.exe").unlink()
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL the Windows build of Orbx Central is not installed", result.stdout)

    def test_the_installer_runs_with_powershell_off(self):
        setup = self.root / "downloads" / "orbx-central-4.5.50.exe"
        write(setup, "MZ")
        result = self.script("install", FSOL_INSTALLER=str(setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded_env()["WINEDLLOVERRIDES"], "powershell.exe=d")
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {setup}"])

    def test_launch_opens_it_in_the_prefix_with_the_sandbox_off(self):
        result = self.script("launch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine C:\\users\\steamuser\\AppData\\Local\\Programs\\orbx-central\\Orbx Central.exe --disable-gpu-sandbox"])

    def test_launch_refuses_while_the_simulator_runs_and_starts_nothing(self):
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("close Microsoft Flight Simulator 2024", result.stderr)
        time.sleep(0.3)
        self.assertEqual(self.programs_run(), [])

    def test_launch_keeps_the_machine_choices_out(self):
        text = (ROOT / "addons" / "orbx-central" / "launch").read_text()
        for owner_only in ("swiftshader", "angle", "radeon", "VK_LOADER"):
            self.assertNotIn(owner_only, text)

    def test_close_asks_pkill_for_the_program_and_its_helper_folder(self):
        self.fake_process("C:\\users\\steamuser\\AppData\\Local\\Programs\\orbx-central\\saturn-win\\saturn.exe")
        result = self.script("close")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.pkill_calls(), [f"pkill -TERM -f -- {ORBX_CLOSE_PATTERN}"])
        self.assertEqual(self.processes_left(), [])

    def test_close_takes_the_helper_processes_too(self):
        main = self.run_program("C:\\users\\steamuser\\AppData\\Local\\Programs\\orbx-central\\Orbx Central.exe")
        helper = self.run_program("C:\\users\\steamuser\\AppData\\Local\\Programs\\orbx-central\\saturn-win\\saturn.exe")
        result = self.script("close")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((main.wait(timeout=5), helper.wait(timeout=5)), (-signal.SIGTERM, -signal.SIGTERM))


class ConfigCase(StoreCase):
    def setUp(self):
        super().setUp()
        self.config = self.fake.home / ".config"
        self.packages = self.found.packages
        self.link = self.root / "link-to-packages"
        os.symlink(self.packages, self.link)

    def settings(self, text):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(text)

    def assert_quiet(self, result):
        self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def assert_no_changes(self, run):
        before = tree(self.root)
        run()
        self.assertEqual(tree(self.root), before)


class AerosoftOneTest(ConfigCase):
    ADDON = "aerosoft-one"

    def setUp(self):
        super().setUp()
        self.file = self.config / "Aerosoft One" / "persistent-store.json"

    def store(self, directory, **extra):
        data = {"userToken": TOKEN, "userEmail": "pilot@example.com", "manuallyDetectedGamesData": {"msfs2024Steam": {"directory": directory, "userDirectory": "elsewhere"}}}
        data.update(extra)
        self.settings(json.dumps(data))

    def test_it_passes_when_the_folder_is_the_packages_folder(self):
        self.store(str(self.packages))
        result = self.script("check")
        self.assertEqual((result.returncode, result.stdout.split()[0]), (0, "ok"), result.stdout)
        self.assert_quiet(result)

    def test_a_link_to_the_packages_folder_and_a_trailing_slash_count(self):
        self.store(str(self.link) + "/")
        self.assertEqual(self.script("check").returncode, 0)

    def test_a_different_folder_fails_and_names_both(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.store(str(elsewhere))
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(elsewhere), result.stdout)
        self.assertIn(str(self.packages), result.stdout)
        self.assert_quiet(result)

    def test_a_missing_key_fails(self):
        self.settings(json.dumps({"userToken": TOKEN, "manuallyDetectedGamesData": {}}))
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("has no MSFS 2024 (Steam) folder", result.stdout)
        self.assert_quiet(result)

    def test_a_file_that_is_not_json_fails_without_printing_it(self):
        self.settings('{"userToken": "' + TOKEN + '", broken')
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assert_quiet(result)

    def test_a_missing_settings_file_fails_and_says_to_start_the_app(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("start it once", result.stdout)

    def test_no_packages_folder_fails_instead_of_comparing_with_nothing(self):
        self.store(str(self.packages))
        result = self.script("check", FSOL_PACKAGES="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no packages folder found", result.stdout)

    def test_apply_reads_one_key_prints_no_other_value_and_changes_nothing(self):
        self.store(str(self.packages))
        self.assert_no_changes(lambda: self.assert_quiet(self.script("apply")))
        result = self.script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("pilot@example.com", result.stdout)
        self.assertNotIn("elsewhere", result.stdout)

    def test_apply_with_a_wrong_folder_exits_zero_and_leaves_the_failure_to_check(self):
        self.store(str(self.root))
        result = self.script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertIn("FAIL", result.stdout)

    def test_undo_changes_nothing(self):
        self.store(str(self.packages))
        self.assert_no_changes(lambda: self.assertEqual(self.script("undo").returncode, 0))

    def test_check_reads_only(self):
        self.store(str(self.packages))
        self.assert_no_changes(lambda: self.script("check"))

    def test_the_script_has_no_other_way_to_print_the_file(self):
        source = (ROOT / "addons" / "aerosoft-one" / "parts" / "aerosoft.py").read_text()
        self.assertNotIn("userToken", source)
        self.assertEqual(len(re.findall(r"print\(", source)), 3)


class FlyByWireTest(ConfigCase):
    ADDON = "flybywire-installer"

    def setUp(self):
        super().setUp()
        self.file = self.config / "FlyByWire Installer" / "config.json"
        self.community = self.found.community

    def config_with(self, community, install, base=None):
        block = {"enabled": True, "basePath": base or str(self.found.base), "communityPath": community, "installPath": install}
        data = {"cache": {"main": {"token": TOKEN}}, "mainSettings": {"simulator": {"msfs2020": {"enabled": False, "basePath": None}, "msfs2024": block}}}
        self.settings(json.dumps(data))

    def test_it_passes_when_both_paths_are_the_community_folder(self):
        self.config_with(str(self.community), str(self.community))
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok", "ok"])
        self.assert_quiet(result)

    def test_a_link_to_the_community_folder_counts(self):
        self.config_with(str(self.link / "Community"), str(self.link / "Community") + "/")
        self.assertEqual(self.script("check").returncode, 0)

    def test_the_trap_both_paths_set_to_the_base_folder_fails_twice(self):
        base = str(self.found.base)
        self.config_with(base, base)
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["FAIL", "FAIL"])
        self.assertIn(f"Set the Community folder in the installer's settings to {self.community}", result.stdout)
        self.assert_quiet(result)

    def test_only_the_install_path_wrong_fails_once(self):
        self.config_with(str(self.community), str(self.packages))
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok", "FAIL"])

    def test_a_null_community_path_fails_and_says_to_set_it(self):
        self.settings(json.dumps({"mainSettings": {"simulator": {"msfs2024": {"enabled": True, "communityPath": None, "installPath": None}}}}))
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("has no communityPath", result.stdout)

    def test_no_msfs2024_block_fails(self):
        self.settings(json.dumps({"mainSettings": {"simulator": {}}, "cache": {"main": {"token": TOKEN}}}))
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no MSFS 2024 block", result.stdout)
        self.assert_quiet(result)

    def test_a_file_that_is_not_json_fails_without_printing_it(self):
        self.settings('{"token": "' + TOKEN + '", nope')
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assert_quiet(result)

    def test_a_missing_file_fails(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("start it once", result.stdout)

    def test_apply_prints_no_other_value_and_changes_nothing(self):
        self.config_with(str(self.community), str(self.community))
        self.assert_no_changes(lambda: self.assert_quiet(self.script("apply")))
        result = self.script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(str(self.found.base), result.stdout)

    def test_undo_and_check_change_nothing(self):
        self.config_with(str(self.community), str(self.community))
        self.assert_no_changes(lambda: [self.script("undo"), self.script("check")])


class TriTriSimHubTest(StoreCase):
    ADDON = "tritrisim-hub"
    ORIGINAL = b"MZ\x00padding KERNEL32.dll\x00 ole32.dll\x00 USER32.dll\x00tail"
    PATCHED = ORIGINAL.replace(b"ole32.dll\x00", b"oletr.dll\x00")

    def setUp(self):
        super().setUp()
        self.exe = self.put(f"{TRITRISIM_FOLDER}/tritrisim-hub.exe", self.ORIGINAL)
        self.oletr = self.exe.parent / "oletr.dll"
        self.stage_shim("oletr.dll", SHIM_OLETR)
        self.orig = self.backup("tritrisim-hub", "tritrisim-hub.exe.orig")
        self.stubs()
        self.write_build()

    def write_build(self, listed=True, modules=GET_RECT_MODULES):
        record = ["release test x86_64"]
        if listed:
            record.append(f"patch {hashlib.sha256((ROOT / 'patches' / 'wine' / GET_RECT_PATCH).read_bytes()).hexdigest()} {GET_RECT_PATCH}")
        for module in GET_RECT_MODULES:
            built = self.proton / "files" / module
            built.parent.mkdir(parents=True, exist_ok=True)
            built.write_bytes(f"built {module}".encode())
            data = built.read_bytes() if module in modules else b"something else"
            record.append(f"module {hashlib.sha256(data).hexdigest()} {module}")
        (self.proton / "fs2024-build.txt").write_text("\n".join(record) + "\n")

    def test_apply_changes_one_import_name_and_puts_the_stand_in_beside_the_exe(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.exe.read_bytes(), self.PATCHED)
        self.assertEqual(len(self.exe.read_bytes()), len(self.ORIGINAL))
        self.assertEqual(self.oletr.read_bytes(), SHIM_OLETR)
        self.assertEqual(self.orig.read_bytes(), self.ORIGINAL)

    def test_apply_twice_changes_nothing_the_second_time(self):
        self.script("apply")
        before = tree(self.root)
        result = self.script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertIn("already names oletr.dll", result.stdout)
        self.assertEqual(tree(self.root), before)

    def test_an_executable_with_two_ole32_names_is_left_alone(self):
        odd = self.ORIGINAL + b"ole32.dll\x00"
        self.exe.write_bytes(odd)
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.exe.read_bytes(), odd)
        self.assertFalse(self.oletr.exists())
        self.assertFalse(self.orig.exists())

    def test_a_stand_in_that_does_not_build_leaves_the_executable_and_the_backup_alone(self):
        (self.root / "cache" / "shims" / "oletr.dll").unlink()
        stub(self.root / "bin" / "x86_64-w64-mingw32-gcc", "#!/bin/sh\nexit 1\n")
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("stand-in did not build", result.stderr)
        self.assertEqual(self.exe.read_bytes(), self.ORIGINAL)
        self.assertFalse(self.orig.exists())
        self.assertFalse(self.oletr.exists())

    def test_apply_refuses_while_the_hub_is_open_and_changes_nothing(self):
        self.fake_process("C:\\users\\steamuser\\AppData\\Local\\TriTriSim Hub\\tritrisim-hub.exe")
        before = tree(self.root)
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is open", result.stderr)
        self.assertEqual(tree(self.root), before)

    def test_undo_refuses_while_the_hub_is_open_and_keeps_the_patch(self):
        self.script("apply")
        self.fake_process("C:\\users\\steamuser\\AppData\\Local\\TriTriSim Hub\\tritrisim-hub.exe")
        result = self.script("undo")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is open", result.stderr)
        self.assertEqual(self.exe.read_bytes(), self.PATCHED)
        self.assertTrue(self.oletr.exists())

    def test_check_passes_after_apply_with_four_ok_lines(self):
        self.script("apply")
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok"] * 4)

    def test_check_fails_without_the_fix(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["FAIL", "FAIL", "ok", "ok"])

    def test_check_fails_after_an_update_brought_the_executable_back(self):
        self.script("apply")
        self.exe.write_bytes(self.ORIGINAL)
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["FAIL", "ok", "ok", "ok"])

    def test_check_fails_when_the_stand_in_is_missing(self):
        self.script("apply")
        self.oletr.unlink()
        result = self.script("check")
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok", "FAIL", "ok", "ok"])

    def test_check_fails_with_a_proton_built_without_the_tray_rectangle_patch(self):
        self.script("apply")
        self.write_build(listed=False)
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok", "ok", "FAIL", "FAIL"])
        self.assertIn("run: flightsim-on-linux proton build", result.stdout)

    def test_check_fails_when_explorer_is_not_the_built_one(self):
        self.script("apply")
        self.write_build(modules=GET_RECT_MODULES[:1])
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok", "ok", "ok", "FAIL"])
        self.assertIn("explorer.exe", result.stdout.splitlines()[3])

    def test_check_changes_nothing(self):
        self.script("apply")
        before = tree(self.root)
        self.script("check")
        self.assertEqual(tree(self.root), before)

    def test_undo_restores_the_executable_byte_for_byte_and_removes_the_stand_in(self):
        self.script("apply")
        result = self.script("undo")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.exe.read_bytes(), self.ORIGINAL)
        self.assertFalse(self.oletr.exists())

    def test_undo_on_an_updated_executable_keeps_the_new_version(self):
        self.script("apply")
        newer = self.ORIGINAL + b" version 1.2.3"
        self.exe.write_bytes(newer)
        self.script("undo")
        self.assertEqual(self.exe.read_bytes(), newer)

    def test_launch_patches_again_after_an_update_and_starts_the_hub(self):
        self.script("apply")
        self.exe.write_bytes(self.ORIGINAL + b" version 1.2.3")
        result = self.script("launch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertIn(b"oletr.dll\x00", self.exe.read_bytes())
        self.assertNotIn(b"ole32.dll\x00", self.exe.read_bytes())
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine C:\\users\\steamuser\\AppData\\Local\\TriTriSim Hub\\tritrisim-hub.exe"])

    def test_launch_with_the_simulator_running_goes_through_the_container(self):
        self.script("launch", FSOL_SIM_RUNNING="1")
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.root / 'cli'} run C:\\users\\steamuser\\AppData\\Local\\TriTriSim Hub\\tritrisim-hub.exe"])

    def test_launch_does_not_touch_the_executable_while_the_hub_is_open(self):
        self.fake_process("C:\\users\\steamuser\\AppData\\Local\\TriTriSim Hub\\tritrisim-hub.exe")
        result = self.script("launch")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.exe.read_bytes(), self.ORIGINAL)

    def test_launch_stops_when_the_import_cannot_be_patched(self):
        self.exe.write_bytes(b"MZ no import name here")
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        time.sleep(0.3)
        self.assertEqual(self.programs_run(), [])

    def test_close_asks_pkill_for_a_plain_term_on_its_processes(self):
        self.fake_process("C:\\users\\steamuser\\AppData\\Local\\TriTriSim Hub\\tritrisim-hub.exe")
        result = self.script("close")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.pkill_calls(), [f"pkill -TERM -f -- {TRITRISIM_PATTERN}"])
        self.assertEqual(self.processes_left(), [])

    def test_close_ends_it_with_sigterm(self):
        process = self.run_program("C:\\users\\steamuser\\AppData\\Local\\TriTriSim Hub\\tritrisim-hub.exe")
        result = self.script("close")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(process.wait(timeout=5), -signal.SIGTERM)

    def test_install_runs_the_installer_alone(self):
        setup = self.root / "downloads" / "TriTriSim Hub_1.2.2_x64-setup.exe"
        write(setup, "MZ")
        result = self.script("install", FSOL_INSTALLER=str(setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {setup}"])


class ShimSourceTest(unittest.TestCase):
    def build(self, name, output):
        env = dict(os.environ, GOCACHE=subprocess.run(["go", "env", "GOCACHE"], capture_output=True, text=True).stdout.strip()) if shutil.which("go") else os.environ
        return subprocess.run([str(ROOT / "shims" / name / "build.sh"), str(output)], capture_output=True, text=True, env=env)

    def without_tools(self, name):
        with tempfile.TemporaryDirectory() as temp:
            bin_folder = Path(temp) / "bin"
            bin_folder.mkdir()
            for tool in ("bash", "dirname", "readlink", "mkdir", "basename"):
                os.symlink(shutil.which(tool), bin_folder / tool)
            return subprocess.run([str(ROOT / "shims" / name / "build.sh"), str(Path(temp) / "out")], capture_output=True, text=True, env={"PATH": str(bin_folder)})

    def test_each_shim_names_the_missing_compiler_package(self):
        for name, words in (("procgov", ("golang-go", "Arch")), ("winhttp-proxy", ("mingw-w64-gcc", "gcc-mingw-w64-x86-64")), ("oletr", ("mingw-w64-gcc", "gcc-mingw-w64-x86-64"))):
            with self.subTest(name):
                result = self.without_tools(name)
                self.assertEqual(result.returncode, 1)
                for word in words:
                    self.assertIn(word, result.stderr)

    def test_no_argument_prints_the_usage(self):
        for name in ("procgov", "winhttp-proxy", "oletr"):
            result = subprocess.run([str(ROOT / "shims" / name / "build.sh")], capture_output=True, text=True)
            self.assertEqual((result.returncode, "usage:" in result.stderr), (2, True), name)

    @unittest.skipUnless(shutil.which("x86_64-w64-mingw32-gcc"), "needs mingw-w64")
    def test_the_c_shims_build_and_export_what_the_programs_import(self):
        with tempfile.TemporaryDirectory() as temp:
            for name, file, exports in (
                ("winhttp-proxy", "winhttp.dll", ("WinHttpAddRequestHeaders", "WinHttpOpen", "WinHttpWebSocketSend", "DllGetClassObject")),
                ("oletr", "oletr.dll", ("RevokeDragDrop", "RegisterDragDrop", "OleInitialize", "CoCreateInstance", "CoUninitialize", "CoInitializeEx", "CoInitialize", "CoTaskMemAlloc")),
            ):
                result = self.build(name, temp)
                self.assertEqual(result.returncode, 0, result.stderr)
                built = Path(temp) / file
                self.assertEqual(built.read_bytes()[:2], b"MZ")
                if shutil.which("objdump"):
                    listing = subprocess.run(["objdump", "-p", str(built)], capture_output=True, text=True, env=dict(os.environ, LC_ALL="C")).stdout
                    for export in exports:
                        self.assertRegex(listing, rf"\b{export}\b", f"{file} must export {export}")

    @unittest.skipUnless(shutil.which("x86_64-w64-mingw32-gcc"), "needs mingw-w64")
    def test_the_oletr_stand_in_forwards_seven_functions_and_answers_revoke_itself(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(self.build("oletr", temp).returncode, 0)
            if not shutil.which("objdump"):
                self.skipTest("needs objdump")
            listing = subprocess.run(["objdump", "-p", str(Path(temp) / "oletr.dll")], capture_output=True, text=True, env=dict(os.environ, LC_ALL="C")).stdout
            self.assertEqual(len(re.findall(r"Forwarder RVA -- ole32\.", listing)), 7)

    @unittest.skipUnless(shutil.which("x86_64-w64-mingw32-gcc"), "needs mingw-w64")
    def test_the_winhttp_stand_in_forwards_all_but_one_function_to_the_copy_of_proton(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(self.build("winhttp-proxy", temp).returncode, 0)
            if not shutil.which("objdump"):
                self.skipTest("needs objdump")
            listing = subprocess.run(["objdump", "-p", str(Path(temp) / "winhttp.dll")], capture_output=True, text=True, env=dict(os.environ, LC_ALL="C")).stdout
            self.assertEqual(len(re.findall(r"Forwarder RVA -- winhttp_wine\.", listing)), 48)
            self.assertIn(b"winhttp_wine.DllCanUnloadNow", (Path(temp) / "winhttp.dll").read_bytes())

    @unittest.skipUnless(shutil.which("go"), "needs go")
    def test_the_procgov_shim_builds_for_windows_and_carries_the_mark_the_add_on_looks_for(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.build("procgov", temp)
            self.assertEqual(result.returncode, 0, result.stderr)
            built = (Path(temp) / "procgov.exe").read_bytes()
            self.assertEqual(built[:2], b"MZ")
            self.assertIn(b"navigraph-7z-", built)

    @unittest.skipUnless(shutil.which("go"), "needs go")
    def test_the_procgov_host_path_logic_passes_its_own_go_tests(self):
        env = dict(os.environ, GOCACHE=subprocess.run(["go", "env", "GOCACHE"], capture_output=True, text=True).stdout.strip())
        result = subprocess.run(["go", "test", "./..."], cwd=ROOT / "shims" / "procgov", capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class PatchImportsTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("patch_imports", ROOT / "addons" / "tritrisim-hub" / "parts" / "patch_imports.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_the_patched_file_is_written_beside_the_executable_and_swapped_in(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / "hub.exe"
            exe.write_bytes(b"a ole32.dll\x00 b")
            exe.chmod(0o755)
            calls = []
            real = self.module.os.replace

            def replace(source, target):
                calls.append((Path(source).parent, Path(target)))
                real(source, target)

            with mock.patch.object(self.module.os, "replace", replace):
                self.assertEqual(self.module.main(["patch", str(exe)]), 0)
            self.assertEqual(calls, [(exe.parent, exe)])
            self.assertEqual(exe.read_bytes(), b"a oletr.dll\x00 b")
            self.assertEqual(stat.S_IMODE(exe.stat().st_mode), 0o755)
            self.assertEqual(list(exe.parent.iterdir()), [exe])

    def test_a_write_that_fails_leaves_the_executable_whole_and_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / "hub.exe"
            exe.write_bytes(b"a ole32.dll\x00 b")
            with mock.patch.object(self.module.os, "replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    self.module.main(["patch", str(exe)])
            self.assertEqual(exe.read_bytes(), b"a ole32.dll\x00 b")
            self.assertEqual(list(exe.parent.iterdir()), [exe])

    def test_state_counts_exactly_one_import_name(self):
        state = self.module.state
        self.assertEqual(state(b"a ole32.dll\x00 b"), "original")
        self.assertEqual(state(b"a oletr.dll\x00 b"), "patched")
        self.assertEqual(state(b"a ole32.dll\x00 b ole32.dll\x00"), "unknown")
        self.assertEqual(state(b"a ole32.dll\x00 oletr.dll\x00"), "unknown")
        self.assertEqual(state(b"nothing"), "unknown")
        self.assertEqual(state(b"xole32.dll b"), "unknown")


if __name__ == "__main__":
    unittest.main()
