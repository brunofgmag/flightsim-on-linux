import hashlib
import json
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
from tests.test_dotnet48_addon import version_resource
from tests.test_machine import FakeMachine, write

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addons" / "maddog-md80"
PAGES = ROOT / "site" / "pages"
MDCLIENT = r"C:\users\steamuser\AppData\Local\Maddog X Files\MSFS 2024\MDClient\MDClient.exe"
LOAD_MANAGER = r"C:\users\steamuser\AppData\Local\Maddog X Files\Load Manager\maddog_lm.exe"
LOCAL = "drive_c/users/steamuser/AppData/Local/Maddog X Files"
KEY = r"HKCU\Software\Wine\AppDefaults\MDClient.exe\DllOverrides"
OVERRIDE_SECTION = r"[Software\\Wine\\AppDefaults\\MDClient.exe\\DllOverrides] 1793000000"
REAL_MSCOREE = {
    "system32": "93077039e6606decda91ea85e785227e0aeecf01356f257c35fce1446b0a0c91",
    "syswow64": "24d1919b63ed444f5c6629626c239ec78a5bb7a3f29b154feea03a6fb8f26148",
}
SECTIONS = ["Get it", "Install it", "Make it run", "Start it", "Check it", "Known problems", "Undo"]
BANNED = ("seamless", "robust", "leverage", "simply")


def page_problems(name, sections=SECTIONS):
    text = (PAGES / f"{name}.html").read_text()
    head, divider, body = text.partition("\n---\n")
    problems = []
    lines = head.splitlines()
    if not (len(lines) == 2 and lines[0].startswith("title: ") and lines[1].startswith("description: ") and divider):
        problems.append("the head is not title, description and ---")
    if not body.lstrip().startswith("<h1>") or '<p class="lead">' not in body:
        problems.append("the page does not start with h1 and a lead")
    if "\u2014" in text:
        problems.append("the page has an em dash")
    problems += [f"the page says {word}" for word in BANNED if re.search(rf"\b{word}\b", text, re.IGNORECASE)]
    headings = re.findall(r"<h2>(.*?)</h2>", body)
    if sections:
        order = [sections.index(heading) for heading in headings if heading in sections]
        if order != sorted(order):
            problems.append(f"the sections are out of order: {headings}")
    for tag in ("pre", "code", "ul", "ol", "p", "li"):
        if len(re.findall(rf"<{tag}[ >]", body)) != body.count(f"</{tag}>"):
            problems.append(f"<{tag}> is not balanced")
    return problems


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def recording(file, after=""):
    return f'#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "{file}"\nprintf "%s\\n" "cwd=$PWD" >> "{file}"\nenv >> "{file}.env"\n{after}'


def stub(path, text):
    write(path, text)
    path.chmod(0o755)
    return path


def windows(path):
    return "Z:" + str(path).replace("/", "\\")


class MaddogCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fake = FakeMachine(self.root).build()
        self.found = self.fake.discover()
        self.community = self.found.community
        self.prefix = self.found.prefix
        self.proton = self.fake.tool
        self.record = self.root / "record"
        self.processes = []
        self.addCleanup(self.stop_processes)
        self.token = str(self.root)
        process_stubs(self.root / "bin", self.token)
        self.config = self.root / "config"
        self.machine_json = json.dumps({"prefix_in_use": False, "prefix_programs": []})
        self.build_repo()
        self.install_maddog()
        self.install_dotnet()
        self.set_override("native")
        self.stubs()

    def stop_processes(self):
        for process in self.processes:
            process.kill()
            process.wait()

    def build_repo(self):
        self.repo = self.root / "repo"
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(ROOT / "fsol", self.repo / "fsol", ignore=ignore)
        shutil.copytree(ROOT / "tools", self.repo / "tools", ignore=ignore)
        shutil.copytree(ADDON, self.repo / "addons" / "maddog-md80", ignore=ignore)
        parts = self.repo / "addons" / "dotnet48" / "parts"
        parts.mkdir(parents=True)
        self.mscoree = {"system32": b"microsoft 64", "syswow64": b"microsoft 32"}
        text = (ROOT / "addons" / "dotnet48" / "parts" / "dotnet48.py").read_text()
        for folder, data in self.mscoree.items():
            text = text.replace(REAL_MSCOREE[folder], sha256(data))
        (parts / "dotnet48.py").write_text(text)
        self.addon = self.repo / "addons" / "maddog-md80"

    def install_maddog(self):
        local = self.prefix / LOCAL
        write(local / "MSFS 2024/Data/LM.ini", f"[GENERAL]\r\nVersion=2.1 b283\r\nFSPath={windows(self.community)}\r\n")
        write(local / "MSFS 2024/Data/registration.ini", "[REGISTRATION]\r\nKey=ABCD-EFGH-JKLM-NPQR\r\nPurchaseCode=TEST-CODE\r\n")
        write(local / "manager.ini", "[MANAGER]\r\nSim=1\r\n")
        write(local / "MSFS 2024/MDClient/MDClient.exe", "MZ")
        write(local / "Load Manager/maddog_lm.exe", "MZ")
        write(self.community / "lsh-maddogx-aircraft/manifest.json", '{"title": "Maddog"}')

    def install_dotnet(self):
        for folder in ("Framework64", "Framework"):
            file = self.prefix / "drive_c/windows/Microsoft.NET" / folder / "v4.0.30319/clr.dll"
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(version_resource("4.8.3761.0"))
        for folder, data in self.mscoree.items():
            file = self.prefix / "drive_c/windows" / folder / "mscoree.dll"
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)

    def set_override(self, value):
        text = "WINE REGISTRY Version 2\n\n"
        if value is not None:
            text += f'{OVERRIDE_SECTION}\n"mscoree"="{value}"\n'
        (self.prefix / "user.reg").write_text(text)

    def stubs(self, wine_exit=0):
        stub(self.root / "bin" / "notify-send", recording(self.record))
        cli = f'#!/bin/sh\nif [ "$1" = machine ]; then printf \'%s\' \'{self.machine_json}\'; exit 0; fi\n' + recording(self.record)[len("#!/bin/sh\n"):]
        stub(self.root / "cli", cli)
        stub(self.proton / "files/bin/wine", recording(self.record) + f"exit {wine_exit}\n")
        stub(self.proton / "files/bin/wineserver", recording(f"{self.record}.server"))

    def environment(self, **extra):
        env = {
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "XDG_CONFIG_HOME": str(self.config),
            "FSOL_PREFIX": str(self.prefix),
            "FSOL_PROTON": str(self.found.proton),
            "FSOL_COMMUNITY": str(self.community),
            "FSOL_BASE": str(self.found.base),
            "FSOL_SIM_INSTALL": str(self.found.sim_install),
            "FSOL_COMPATDATA": str(self.found.compatdata),
            "FSOL_CACHE": str(self.root / "cache"),
            "FSOL_CLI": str(self.root / "cli"),
            "FSOL_ADDON": "maddog-md80",
            "FSOL_SIM_RUNNING": "0",
            "FSOL_CLOSE_WAIT": "1",
        }
        env.update(extra)
        return env

    def script(self, name, *args, **extra):
        return subprocess.run([str(self.addon / name), *args], env=self.environment(**extra), capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def recorded(self):
        return self.record.read_text().splitlines() if self.record.exists() else []

    def recorded_env(self):
        path = Path(f"{self.record}.env")
        return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line) if path.exists() else {}

    def wine_calls(self):
        return [line for line in self.recorded() if line.startswith(f"{self.proton}/files/bin/wine")]

    def wait_for_record(self, seconds=5):
        deadline = time.monotonic() + seconds
        while not (self.record.exists() and "cwd=" in self.record.read_text()) and time.monotonic() < deadline:
            time.sleep(0.05)

    def run_mdclient(self):
        process = start_stand_in(self.root, self.token, stand_in_name(MDCLIENT), 60)
        self.processes.append(process)
        time.sleep(0.2)


class ShapeTest(MaddogCase):
    def test_the_manifest_is_complete(self):
        addon = addons.load(ADDON)
        self.assertEqual((addon.id, addon.needs, addon.tested_version, addon.tested_on), ("maddog-md80", ["proton-fs2024", "simulator-closed"], "2.1 b283", "2026-10-22"))
        self.assertEqual(addon.install, {"from": "https://www.flythemaddog.com", "paid": True, "kind": "installer"})
        self.assertEqual([(entry["title"], entry["script"]) for entry in addon.shortcuts], [("Maddog Manager", "maddog-manager"), ("Maddog registration", "register")])
        self.assertTrue(addon.can_install)
        self.assertTrue(addon.touches_addon_files)

    def test_the_add_on_has_no_script_of_its_own_for_dotnet_48_because_the_base_add_on_does_it(self):
        self.assertFalse((ADDON / "dotnet48").exists())
        self.assertEqual([entry["script"] for entry in addons.load(ADDON).shortcuts], ["maddog-manager", "register"])

    def test_the_watcher_starts_the_client_with_a_maddog_and_stops_it_with_another_aircraft(self):
        watch = addons.load(ADDON).watcher
        self.assertEqual((watch["trigger"], watch["stop"], watch["script"]), ({"aircraft": "Maddog"}, "on_other_aircraft", "launch-mdclient"))
        self.assertTrue(re.search(watch["trigger"]["aircraft"], r"SimObjects\Airplanes\MaddogX20thNV\aircraft.CFG", re.IGNORECASE))
        self.assertFalse(re.search(watch["trigger"]["aircraft"], r"SimObjects\Airplanes\FSLabs A321-NEO PW\aircraft.CFG", re.IGNORECASE))

    def test_the_watcher_pattern_matches_the_client_and_not_other_programs(self):
        pattern = addons.load(ADDON).watcher["app"]
        self.assertTrue(re.search(pattern, MDCLIENT))
        self.assertFalse(re.search(pattern, LOAD_MANAGER))
        self.assertFalse(re.search(pattern, "/usr/bin/python3 flightsim-on-linux addon watch maddog-md80"))

    def test_it_counts_as_installed_where_the_client_and_the_package_are(self):
        addon = addons.load(ADDON)
        self.assertTrue(addons.is_installed(addon, self.found))
        (self.prefix / LOCAL / "MSFS 2024/MDClient/MDClient.exe").unlink()
        (self.community / "lsh-maddogx-aircraft/manifest.json").unlink()
        self.assertFalse(addons.is_installed(addon, self.found))

    def test_the_menu_entries_pass_the_desktop_validator(self):
        written = desktop.write_shortcuts(addons.load(ADDON), self.found, ROOT / "bin" / "flightsim-on-linux", self.root / "data", RecordingTools(), lambda line: None)
        self.assertEqual([path.name for path in written], ["flightsim-on-linux-maddog-md80.desktop", "flightsim-on-linux-maddog-md80-2.desktop"])
        cli = ROOT / "bin" / "flightsim-on-linux"
        self.assertIn(f'Exec="{cli}" addon launch maddog-md80 maddog-manager', written[0].read_text())
        self.assertIn(f'Exec="{cli}" addon launch maddog-md80 register', written[1].read_text())
        if shutil.which("desktop-file-validate"):
            for path in written:
                result = subprocess.run(["desktop-file-validate", str(path)], capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""), path.name)

    def test_the_manager_window_is_matched_to_the_first_menu_entry(self):
        app = (ADDON / "manager" / "maddog_manager" / "app.py").read_text()
        self.assertIn('DESKTOP_FILE = "flightsim-on-linux-maddog-md80"', app)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "needs systemd-analyze")
    def test_the_service_it_writes_passes_systemd_verify(self):
        path = self.root / "unit.service"
        path.write_text(watcher.unit_text(addons.load(ADDON), ROOT / "bin" / "flightsim-on-linux"))
        result = subprocess.run(["systemd-analyze", "--user", "verify", str(path)], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""))
        self.assertIn("KillMode=process", path.read_text())


class CheckTest(MaddogCase):
    LINES = 5

    def assert_only_failure(self, word, lines=LINES, **extra):
        result = self.script("check", **extra)
        out = result.stdout.splitlines()
        self.assertEqual(result.returncode, 1, out)
        failures = [line for line in out if line.startswith("FAIL")]
        self.assertEqual(len(failures), 1, out)
        self.assertIn(word, failures[0])
        self.assertEqual(len(out), lines)

    def test_a_machine_with_every_piece_passes_with_five_ok_lines(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok"] * self.LINES)

    def test_a_missing_package_fails(self):
        (self.community / "lsh-maddogx-aircraft/manifest.json").unlink()
        self.assert_only_failure("is not in")

    def test_a_load_manager_that_points_at_another_folder_fails(self):
        other = self.root / "elsewhere"
        other.mkdir()
        write(self.prefix / LOCAL / "MSFS 2024/Data/LM.ini", f"[GENERAL]\r\nFSPath={windows(other)}\r\n")
        self.assert_only_failure("FSPath")

    def test_a_load_manager_with_a_folder_that_does_not_exist_fails(self):
        write(self.prefix / LOCAL / "MSFS 2024/Data/LM.ini", "[GENERAL]\r\nFSPath=Z:\\no\\such\\folder\r\n")
        self.assert_only_failure("not a folder that exists")

    def test_no_maddog_folder_in_the_prefix_fails_the_load_manager_and_registration_lines(self):
        shutil.rmtree(self.prefix / LOCAL)
        result = self.script("check")
        failures = [line for line in result.stdout.splitlines() if line.startswith("FAIL")]
        self.assertEqual(len(failures), 2, result.stdout)
        self.assertIn("Maddog X Files", failures[0])
        self.assertIn("not registered", failures[1])

    def test_an_unregistered_aircraft_fails_and_the_key_is_never_printed(self):
        write(self.prefix / LOCAL / "MSFS 2024/Data/registration.ini", "[REGISTRATION]\r\nKey=\r\nPurchaseCode=\r\n")
        self.assert_only_failure("not registered")
        write(self.prefix / LOCAL / "MSFS 2024/Data/registration.ini", "[REGISTRATION]\r\nKey=ABCD-EFGH-JKLM-NPQR\r\nPurchaseCode=TEST-CODE\r\n")
        result = self.script("check")
        self.assertNotIn("ABCD", result.stdout + result.stderr)
        self.assertNotIn("TEST-CODE", result.stdout + result.stderr)

    def test_a_missing_registration_file_fails(self):
        (self.prefix / LOCAL / "MSFS 2024/Data/registration.ini").unlink()
        self.assert_only_failure("not registered")

    def test_a_prefix_without_dotnet_48_fails_with_the_command_that_adds_it(self):
        (self.prefix / "drive_c/windows/Microsoft.NET/Framework64/v4.0.30319/clr.dll").unlink()
        self.assert_only_failure("addon apply dotnet48")

    def test_a_wine_builtin_mscoree_fails(self):
        (self.prefix / "drive_c/windows/syswow64/mscoree.dll").write_bytes(b"wine builtin")
        self.assert_only_failure("mscoree")

    def test_a_client_on_wine_mono_fails_and_says_what_it_cost(self):
        self.set_override(None)
        self.assert_only_failure("40 to 44%")
        self.assertIn("addon apply maddog-md80", self.script("check").stdout)

    def test_another_override_value_fails(self):
        self.set_override("builtin")
        self.assert_only_failure("mscoree set to builtin")

    def test_without_the_watcher_unit_simconnect_is_not_looked_at(self):
        self.assertEqual(len(self.script("check").stdout.splitlines()), self.LINES)

    def test_with_the_watcher_unit_the_simconnect_port_is_looked_at(self):
        write(self.config / "systemd" / "user" / "flightsim-on-linux-maddog-md80.service", "[Service]\n")
        write(self.found.base / "SimConnect.xml", "<SimBase.Document><SimConnect.Comm><Protocol>IPv4</Protocol><Port>5111</Port></SimConnect.Comm></SimBase.Document>")
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(result.stdout.splitlines()), self.LINES + 1)
        self.assertIn("TCP port 5111", result.stdout.splitlines()[-1])
        write(self.found.base / "SimConnect.xml", "<SimBase.Document><SimConnect.Comm><Protocol>IPv4</Protocol><Port>500</Port></SimConnect.Comm></SimBase.Document>")
        self.assert_only_failure("addon apply simconnect-tcp", lines=self.LINES + 1)

    def test_a_unit_with_another_name_does_not_count(self):
        write(self.config / "systemd" / "user" / "mdclient-watcher.service", "[Service]\n")
        self.assertEqual(len(self.script("check").stdout.splitlines()), self.LINES)

    def test_no_folders_found_fails_every_line_instead_of_reading_the_current_folder(self):
        result = self.script("check", FSOL_PREFIX="", FSOL_COMMUNITY="")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(all(line.startswith("FAIL no ") for line in result.stdout.splitlines()), result.stdout)

    def listing(self):
        return sorted((str(path), path.stat().st_size if path.is_file() else 0) for path in self.root.rglob("*") if "__pycache__" not in path.parts)

    def test_check_changes_nothing(self):
        before = self.listing()
        self.script("check")
        self.assertEqual(self.listing(), before)


class ApplyAndUndoTest(MaddogCase):
    def test_apply_writes_the_override_alone_in_the_prefix_when_dotnet_48_is_there(self):
        self.set_override(None)
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.wine_calls(), [f"{self.proton}/files/bin/wine reg add {KEY} /v mscoree /t REG_SZ /d native /f"])
        self.assertEqual(self.recorded_env()["WINEPREFIX"], str(self.prefix))
        self.assertTrue(Path(f"{self.record}.server").exists())
        self.assertIn("changed MDClient.exe", result.stdout)

    def test_apply_leaves_the_client_on_wine_mono_when_dotnet_48_is_missing_and_says_how_to_add_it(self):
        self.set_override(None)
        (self.prefix / "drive_c/windows/Microsoft.NET/Framework/v4.0.30319/clr.dll").unlink()
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.wine_calls(), [])
        self.assertIn("addon apply dotnet48", result.stdout)

    def test_apply_does_nothing_when_the_override_is_there(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertIn("already MDClient.exe runs on .NET Framework 4.8", result.stdout)
        self.assertEqual(self.wine_calls(), [])

    def test_apply_does_not_overwrite_another_value(self):
        self.set_override("builtin")
        result = self.script("apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn("mscoree set to builtin", result.stderr)
        self.assertEqual(self.wine_calls(), [])

    def test_undo_deletes_the_override_and_keeps_dotnet(self):
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.wine_calls(), [f"{self.proton}/files/bin/wine reg delete {KEY} /v mscoree /f"])
        self.assertIn(".NET Framework 4.8 stays in the prefix", result.stdout)
        self.assertTrue((self.prefix / "drive_c/windows/Microsoft.NET/Framework64/v4.0.30319/clr.dll").is_file())

    def test_undo_without_the_override_does_nothing(self):
        self.set_override(None)
        result = self.script("undo")
        self.assertEqual(result.returncode, 0)
        self.assertIn("already no mscoree override", result.stdout)
        self.assertEqual(self.wine_calls(), [])

    def test_undo_leaves_a_value_apply_did_not_write(self):
        self.set_override("builtin")
        result = self.script("undo")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.wine_calls(), [])
        self.assertIn("builtin", result.stdout)

    def test_neither_touches_the_registration_or_the_package(self):
        before = {path: path.read_bytes() for path in (self.prefix / LOCAL).rglob("*.ini")}
        self.set_override(None)
        self.script("apply")
        self.script("undo")
        self.assertEqual({path: path.read_bytes() for path in (self.prefix / LOCAL).rglob("*.ini")}, before)


class InstallTest(MaddogCase):
    def setUp(self):
        super().setUp()
        self.setup = self.root / "downloads" / "Leonardo-Fly-the-Maddog-installer.exe"
        write(self.setup, "MZ")

    def test_the_installer_runs_alone_in_the_prefix(self):
        result = self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.wine_calls(), [f"{self.proton}/files/bin/wine {self.setup}"])
        self.assertEqual(self.recorded_env()["WINEPREFIX"], str(self.prefix))
        self.assertTrue(Path(f"{self.record}.server").exists())

    def test_the_variable_that_breaks_wine_installers_does_not_reach_it(self):
        self.script("install", FSOL_INSTALLER=str(self.setup), NoDefaultCurrentDirectoryInExePath="1")
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", self.recorded_env())

    def test_without_an_installer_the_script_says_how_to_give_one(self):
        result = self.script("install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("addon install maddog-md80", result.stderr)
        self.assertEqual(self.wine_calls(), [])


class RegisterTest(MaddogCase):
    def test_it_opens_the_load_manager_alone_in_the_prefix_from_its_own_folder(self):
        result = self.script("register")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.wine_calls(), [f"{self.proton}/files/bin/wine {LOAD_MANAGER}"])
        self.assertIn(f"cwd={self.prefix / LOCAL / 'Load Manager'}", self.recorded())
        self.assertEqual(self.recorded_env()["WINEPREFIX"], str(self.prefix))
        self.assertTrue(Path(f"{self.record}.server").exists())

    def test_a_running_simulator_stops_it(self):
        result = self.script("register", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("close the simulator first", result.stderr)
        self.assertEqual(self.wine_calls(), [])

    def test_another_program_in_the_prefix_stops_it_and_is_named(self):
        self.machine_json = json.dumps({"prefix_in_use": True, "prefix_programs": ["Navigraph Hub.exe", "tritrisim-hub.exe"]})
        self.stubs()
        result = self.script("register")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Navigraph Hub.exe, tritrisim-hub.exe", result.stderr)
        self.assertEqual(self.wine_calls(), [])

    def test_a_wineserver_without_a_known_program_stops_it_too(self):
        self.machine_json = json.dumps({"prefix_in_use": True, "prefix_programs": []})
        self.stubs()
        result = self.script("register")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Wine is still running", result.stderr)

    def test_a_missing_load_manager_says_how_to_install(self):
        (self.prefix / LOCAL / "Load Manager/maddog_lm.exe").unlink()
        result = self.script("register")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install maddog-md80", result.stderr)

    def test_the_load_manager_output_goes_to_a_private_log(self):
        self.script("register")
        log = self.root / "cache" / "logs" / "maddog-md80.log"
        self.assertEqual(oct(log.stat().st_mode & 0o777), "0o600")


class LaunchMdclientTest(MaddogCase):
    def cli_runs(self):
        return [line for line in self.recorded() if line.startswith(f"{self.root}/cli run")]

    def test_a_closed_simulator_stops_it_with_a_notice_and_starts_nothing(self):
        result = self.script("launch-mdclient")
        self.assertEqual(result.returncode, 1)
        self.assertIn("start Microsoft Flight Simulator 2024 first", result.stderr)
        self.assertEqual(self.cli_runs(), [])

    def test_a_copy_already_running_is_left_alone(self):
        self.run_mdclient()
        result = self.script("launch-mdclient", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.cli_runs(), [])
        self.assertTrue(any("already running" in line for line in self.recorded()))

    def test_it_starts_inside_the_container_from_the_simulator_folder(self):
        result = self.script("launch-mdclient", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.cli_runs(), [f"{self.root}/cli run {MDCLIENT}"])
        self.assertIn(f"cwd={self.found.sim_install}", self.recorded())

    def test_arguments_go_to_the_program(self):
        self.script("launch-mdclient", "-x", FSOL_SIM_RUNNING="1")
        self.wait_for_record()
        self.assertEqual(self.cli_runs(), [f"{self.root}/cli run {MDCLIENT} -x"])

    def test_a_client_that_is_not_installed_says_how_to_install_it(self):
        (self.prefix / LOCAL / "MSFS 2024/MDClient/MDClient.exe").unlink()
        result = self.script("launch-mdclient", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install maddog-md80", result.stderr)

    def test_its_output_goes_to_a_private_log_under_the_cache(self):
        self.script("launch-mdclient", FSOL_SIM_RUNNING="1")
        log = self.root / "cache" / "logs" / "maddog-md80.log"
        self.assertEqual(oct(log.stat().st_mode & 0o777), "0o600")


class ManagerScriptTest(MaddogCase):
    def test_a_missing_pyqt6_is_said_and_nothing_starts(self):
        stub(self.root / "bin" / "python3", '#!/bin/sh\nexit 1\n')
        result = self.script("maddog-manager")
        self.assertEqual(result.returncode, 1)
        self.assertIn("python-pyqt6", result.stderr)

    def test_it_puts_the_manager_folder_on_the_python_path_and_hands_over_the_arguments(self):
        stub(self.root / "bin" / "python3", recording(self.record) + "exit 0\n")
        result = self.script("maddog-manager", "--prefix", "/somewhere")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{self.root}/bin/python3 -B -m maddog_manager --prefix /somewhere", self.recorded())
        self.assertEqual(self.recorded_env()["PYTHONPATH"], str(self.addon / "manager"))


class PageTest(unittest.TestCase):
    def test_the_page_follows_the_template(self):
        self.assertEqual(page_problems("maddog-md80"), [])

    def test_the_page_says_what_the_card_and_the_code_say(self):
        text = (PAGES / "maddog-md80.html").read_text()
        for needed in (
            "40 to 44%",
            "10 to 19%",
            "addon launch maddog-md80 register",
            "addon apply dotnet48",
            "addon watcher maddog-md80",
            "addon shortcut maddog-md80",
            "exe.xml",
            "WinUI 3",
            "has not caught a real MD-80",
            "simconnect-tcp",
            "flightsim-on-linux-maddog-md80.service",
        ):
            self.assertIn(needed, text)
        self.assertNotIn("/home/", text)


if __name__ == "__main__":
    unittest.main()
