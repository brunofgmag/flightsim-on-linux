import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from fsol import addons, container, desktop
from tests.support import process_stubs, stand_in_name, start_stand_in
from tests.test_addons import RecordingTools
from tests.test_machine import FakeMachine, write
from tests.test_maddog_addon import page_problems

ROOT = Path(__file__).resolve().parent.parent
NEST = ROOT / "addons" / "bbs-nest"
AIRCRAFT = ROOT / "addons" / "bbs-757"
RADAR = ROOT / "addons" / "bbs-757-weather-radar"
PAGES = ROOT / "site" / "pages"
PACE_PATCH = "winewayland-pace-hidden-gl-swaps.patch"
PACE_MODULE = "lib/wine/x86_64-unix/winewayland.so"
NEST_DIR = "drive_c/users/steamuser/AppData/Local/Programs/BlueBird Simulations/NEST"
WINDOWS_DIR = r"C:\users\steamuser\AppData\Local\Programs\BlueBird Simulations\NEST"
WINDOWS_EXE = WINDOWS_DIR + r"\bbs_nest.exe"
PATTERN = r"^[A-Za-z]:\\.*\\bbs_nest\.exe"
PANEL = "SimObjects/Airplanes/bbs-757-200-base/panel"
PACKAGE = "bluebirdsim-aircraft-757-200"
LIVERIES = "bluebirdsim-aircraft-757-200-liveries"
BBS_SECTIONS = ["Get it", "Install it", "Make it run", "Start it", "Check it", "Known problems", "Undo"]


def load_module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


connections = load_module("bbs_connections", NEST / "parts" / "connections.py")
radar = load_module("bbs_radar", RADAR / "parts" / "radar.py")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def recording(file, after=""):
    return f'#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "{file}"\nprintf "%s\\n" "cwd=$PWD" >> "{file}"\nenv >> "{file}.env"\n{after}'


def stub(path, text):
    write(path, text)
    path.chmod(0o755)
    return path


class Recorder:
    def recorded(self):
        return self.record.read_text().splitlines() if self.record.exists() else []

    def recorded_env(self):
        path = Path(f"{self.record}.env")
        return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line) if path.exists() else {}

    def wait_for_record(self, seconds=5):
        deadline = time.monotonic() + seconds
        while not (self.record.exists() and "cwd=" in self.record.read_text()) and time.monotonic() < deadline:
            time.sleep(0.05)


class MachineCase(unittest.TestCase, Recorder):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fake = FakeMachine(self.root).build()
        self.found = self.fake.discover()
        self.prefix = self.found.prefix
        self.community = self.found.community
        self.proton = self.fake.tool
        self.record = self.root / "record"
        self.processes = []
        self.token = str(self.root)
        process_stubs(self.root / "bin", self.token)
        self.addCleanup(self.stop_processes)
        self.machine_json = json.dumps({"prefix_in_use": False, "prefix_programs": []})

    def stop_processes(self):
        for process in self.processes:
            process.kill()
            process.wait()

    def stubs(self, wine_exit=0, after=""):
        stub(self.root / "bin" / "notify-send", recording(self.record))
        cli = f'#!/bin/sh\nif [ "$1" = machine ]; then printf \'%s\' \'{self.machine_json}\'; exit 0; fi\n' + recording(self.record, after)[len("#!/bin/sh\n"):]
        stub(self.root / "cli", cli)
        stub(self.proton / "files/bin/wine", recording(self.record, after) + f"exit {wine_exit}\n")
        stub(self.proton / "files/bin/wineserver", recording(f"{self.record}.server"))

    def environment(self, **extra):
        env = {
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "FSOL_PREFIX": str(self.prefix),
            "FSOL_PROTON": str(self.found.proton),
            "FSOL_COMMUNITY": str(self.community),
            "FSOL_BASE": str(self.found.base),
            "FSOL_COMPATDATA": str(self.found.compatdata),
            "FSOL_CACHE": str(self.root / "cache"),
            "FSOL_CLI": str(self.root / "cli"),
            "FSOL_SIM_RUNNING": "0",
            "FSOL_CLOSE_WAIT": "1",
        }
        env.update(extra)
        return env

    def script(self, folder, name, *args, **extra):
        env = self.environment(FSOL_ADDON=folder.name, **extra)
        return subprocess.run([str(folder / name), *args], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def stand_in(self, argv0):
        process = start_stand_in(self.root, self.token, stand_in_name(argv0), 60)
        self.processes.append(process)
        time.sleep(0.2)
        return process


def user_reg(community_value, key="Software/BlueBirdSimulations/Nest"):
    value = community_value.replace("\\", "\\\\")
    return f'WINE REGISTRY Version 2\n\n[{key}] 1792881792\n"MSFS2024CommunityPath"="{value}"\n'


class NestCase(MachineCase):
    def setUp(self):
        super().setUp()
        write(self.prefix / NEST_DIR / "bbs_nest.exe", "MZ")
        (self.prefix / "user.reg").write_text(user_reg("Z:" + str(self.community).replace("/", "\\")))
        self.write_build()
        self.stubs()

    def write_build(self, data=b"patched winewayland", recorded=None, patch=None):
        module = self.proton / "files" / PACE_MODULE
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_bytes(data)
        patch_sha = patch or sha256((ROOT / "patches" / "wine" / PACE_PATCH).read_bytes())
        record = ["release cachyos-11.0-20261005-slr x86_64", f"patch {patch_sha} {PACE_PATCH}", f"module {recorded or sha256(data)} {PACE_MODULE}"]
        (self.proton / "fs2024-build.txt").write_text("\n".join(record) + "\n")

    def nest(self, name, *args, **extra):
        return self.script(NEST, name, *args, **extra)


class NestShapeTest(NestCase):
    def test_the_manifest_is_complete(self):
        addon = addons.load(NEST)
        self.assertEqual((addon.id, addon.needs, addon.tested_version, addon.tested_on), ("bbs-nest", ["proton-fs2024"], "1.0.0.0", "2026-10-28"))
        self.assertEqual(addon.install["kind"], "installer")
        self.assertIsNone(addon.install["paid"])
        self.assertEqual([(entry["title"], entry["script"], entry["close"]) for entry in addon.shortcuts], [("BBS Nest", "launch", "close")])
        self.assertIsNone(addon.watcher)
        self.assertTrue(addon.can_install)

    def test_it_counts_as_installed_where_nest_is(self):
        addon = addons.load(NEST)
        self.assertTrue(addons.is_installed(addon, self.found))
        (self.prefix / NEST_DIR / "bbs_nest.exe").unlink()
        self.assertFalse(addons.is_installed(addon, self.found))

    def test_the_menu_entry_has_a_close_action_and_passes_the_desktop_validator(self):
        written = desktop.write_shortcuts(addons.load(NEST), self.found, ROOT / "bin" / "flightsim-on-linux", self.root / "data", RecordingTools(), lambda line: None)
        text = written[0].read_text()
        self.assertIn(f'Exec="{ROOT / "bin" / "flightsim-on-linux"}" addon launch bbs-nest launch', text)
        self.assertIn("Actions=close;", text)
        if shutil.which("desktop-file-validate"):
            result = subprocess.run(["desktop-file-validate", str(written[0])], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""))

    def test_apply_and_undo_say_there_is_nothing_to_do(self):
        before = sorted(str(path) for path in self.root.rglob("*"))
        for name in ("apply", "undo"):
            result = self.nest(name)
            self.assertEqual(result.returncode, 0)
            self.assertIn("already nothing to", result.stdout)
        self.assertEqual(sorted(str(path) for path in self.root.rglob("*")), before)


class NestCheckTest(NestCase):
    def assert_only_failure(self, word, **extra):
        result = self.nest("check", **extra)
        lines = result.stdout.splitlines()
        self.assertEqual(result.returncode, 1, lines)
        failures = [line for line in lines if line.startswith("FAIL")]
        self.assertEqual(len(failures), 1, lines)
        self.assertIn(word, failures[0])
        self.assertEqual(len(lines), 3)

    def test_a_machine_with_every_piece_passes_with_three_ok_lines(self):
        result = self.nest("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok"] * 3)

    def test_a_prefix_without_nest_fails(self):
        (self.prefix / NEST_DIR / "bbs_nest.exe").unlink()
        self.assert_only_failure("addon install bbs-nest")

    def test_a_nest_that_never_stored_a_community_folder_fails_and_says_to_open_it_once(self):
        (self.prefix / "user.reg").write_text("WINE REGISTRY Version 2\n")
        self.assert_only_failure("open Nest once")

    def test_a_nest_that_stored_another_folder_than_the_real_community_fails_and_says_where_to_set_it(self):
        other = self.root / "Community2024"
        other.mkdir()
        (self.prefix / "user.reg").write_text(user_reg("Z:" + str(other).replace("/", "\\")))
        self.assert_only_failure("set it in Nest's Settings")

    def test_a_stored_folder_that_does_not_exist_fails(self):
        (self.prefix / "user.reg").write_text(user_reg(r"Z:\no\such\folder"))
        self.assert_only_failure("not a folder")

    def test_the_key_with_backslashes_counts_as_well_as_the_key_the_test_machine_has(self):
        (self.prefix / "user.reg").write_text(user_reg("Z:" + str(self.community).replace("/", "\\"), key=r"Software\\BlueBirdSimulations\\Nest"))
        self.assertEqual(self.nest("check").returncode, 0)

    def test_the_stored_folder_may_differ_from_the_real_one_by_case(self):
        (self.prefix / "user.reg").write_text(user_reg("Z:" + str(self.community).replace("/", "\\").upper().replace("COMMUNITY", "community")))
        self.assertEqual(self.nest("check").returncode, 0)

    def test_a_stored_folder_on_the_c_drive_is_followed_through_the_prefix(self):
        real = self.prefix / "drive_c" / "Community"
        real.mkdir(parents=True)
        (self.prefix / "dosdevices").mkdir(exist_ok=True)
        if not (self.prefix / "dosdevices" / "c:").exists():
            os.symlink("../drive_c", self.prefix / "dosdevices" / "c:")
        (self.prefix / "user.reg").write_text(user_reg(r"C:\Community"))
        result = self.nest("check", FSOL_COMMUNITY=str(real))
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_a_proton_without_the_pacing_patch_in_its_build_record_fails(self):
        self.write_build(patch="0" * 64)
        self.assert_only_failure(PACE_PATCH)

    def test_a_winewayland_that_differs_from_the_build_record_fails(self):
        self.write_build(data=b"stock", recorded=sha256(b"patched winewayland"))
        self.assert_only_failure("winewayland.so")

    def test_a_proton_without_a_build_record_fails(self):
        (self.proton / "fs2024-build.txt").unlink()
        self.assert_only_failure("proton build")

    def test_no_folders_found_fails_every_line_instead_of_reading_the_current_folder(self):
        result = self.nest("check", FSOL_PREFIX="", FSOL_COMMUNITY="", FSOL_PROTON="")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.stdout.splitlines()), 3)
        self.assertTrue(all(line.startswith("FAIL no ") for line in result.stdout.splitlines()), result.stdout)

    def test_check_changes_nothing(self):
        before = sorted(str(path) for path in self.root.rglob("*"))
        self.nest("check")
        self.assertEqual(sorted(str(path) for path in self.root.rglob("*")), before)


class NestInstallTest(NestCase):
    def setUp(self):
        super().setUp()
        self.setup = self.root / "downloads" / "BlueBirdNestSetup.exe"
        write(self.setup, "MZ")

    def test_the_setup_runs_alone_in_the_prefix(self):
        result = self.nest("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line for line in self.recorded() if line.startswith(str(self.proton))], [f"{self.proton}/files/bin/wine {self.setup}"])
        self.assertEqual(self.recorded_env()["WINEPREFIX"], str(self.prefix))
        self.assertTrue(Path(f"{self.record}.server").exists())

    def test_the_variable_that_breaks_wine_installers_does_not_reach_it(self):
        self.nest("install", FSOL_INSTALLER=str(self.setup), NoDefaultCurrentDirectoryInExePath="1")
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", self.recorded_env())

    def test_a_failing_setup_keeps_its_exit_code_and_points_at_the_check(self):
        self.stubs(wine_exit=3)
        result = self.nest("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 3)
        self.assertIn("exited with code 3", result.stderr)
        self.assertIn("addon check bbs-nest", result.stderr)

    def test_a_setup_that_succeeds_prints_no_warning(self):
        self.assertEqual(self.nest("install", FSOL_INSTALLER=str(self.setup)).stderr, "")

    def test_without_a_setup_the_script_says_how_to_give_one(self):
        result = self.nest("install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("addon install bbs-nest", result.stderr)


class NestLaunchTest(NestCase):
    def cli_runs(self):
        return [line for line in self.recorded() if line.startswith(f"{self.root}/cli run")]

    def wine_runs(self):
        return [line for line in self.recorded() if line.startswith(f"{self.proton}/files/bin/wine")]

    def test_with_the_simulator_running_nest_starts_in_the_container_with_start_d_and_its_own_folder(self):
        result = self.nest("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.cli_runs(), [f"{self.root}/cli run start /d {WINDOWS_DIR} {WINDOWS_EXE}"])
        self.assertIn(f"cwd={self.prefix / NEST_DIR}", self.recorded())
        self.assertEqual(self.wine_runs(), [])

    def test_the_arguments_the_script_hands_the_cli_become_the_wine_command_of_the_container(self):
        self.nest("launch", FSOL_SIM_RUNNING="1")
        self.wait_for_record()
        handed = self.cli_runs()[0].split(" ", 2)[2]
        self.assertEqual(handed, f"start /d {WINDOWS_DIR} {WINDOWS_EXE}")
        self.fake.run_sim()
        write(self.fake.proc / "4242" / "environ", "WINEPREFIX=/pfx\0DISPLAY=:1\0")
        (self.fake.proc / "4242" / "root").mkdir(parents=True, exist_ok=True)
        command, env = container.plan(self.fake.discover(), ["start", "/d", WINDOWS_DIR, WINDOWS_EXE], {}, proc=self.fake.proc)
        self.assertEqual(command, ["nsenter", "-t", "4242", "-U", "-m", "--preserve-credentials", str(self.proton / "files/bin/wine"), "start", "/d", WINDOWS_DIR, WINDOWS_EXE])
        self.assertEqual(env["WINEPREFIX"], "/pfx")

    def test_with_the_simulator_closed_nest_starts_alone_through_the_prefix_from_its_own_folder(self):
        result = self.nest("launch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.wine_runs(), [f"{self.proton}/files/bin/wine {WINDOWS_EXE}"])
        self.assertEqual(self.recorded_env()["WINEPREFIX"], str(self.prefix))
        self.assertIn(f"cwd={self.prefix / NEST_DIR}", self.recorded())
        self.assertEqual(self.cli_runs(), [])

    def test_alone_it_refuses_while_another_program_holds_the_prefix_and_names_it(self):
        self.machine_json = json.dumps({"prefix_in_use": True, "prefix_programs": ["Navigraph Hub.exe"]})
        self.stubs()
        result = self.nest("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Navigraph Hub.exe", result.stderr)
        self.assertEqual(self.wine_runs(), [])

    def test_with_the_simulator_running_other_programs_in_the_prefix_do_not_stop_it(self):
        self.machine_json = json.dumps({"prefix_in_use": True, "prefix_programs": ["Navigraph Hub.exe"]})
        self.stubs()
        self.assertEqual(self.nest("launch", FSOL_SIM_RUNNING="1").returncode, 0)

    def test_a_copy_already_running_is_left_alone(self):
        self.stand_in(WINDOWS_EXE)
        result = self.nest("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.cli_runs(), [])
        self.assertTrue(any("already running" in line for line in self.recorded()))

    def test_a_nest_that_is_not_installed_says_how_to_install_it(self):
        (self.prefix / NEST_DIR / "bbs_nest.exe").unlink()
        result = self.nest("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install bbs-nest", result.stderr)

    def test_arguments_go_to_nest(self):
        self.nest("launch", "-x", FSOL_SIM_RUNNING="1")
        self.wait_for_record()
        self.assertEqual(self.cli_runs(), [f"{self.root}/cli run start /d {WINDOWS_DIR} {WINDOWS_EXE} -x"])

    def test_its_output_goes_to_a_private_log_under_the_cache(self):
        self.nest("launch", FSOL_SIM_RUNNING="1")
        log = self.root / "cache" / "logs" / "bbs-nest.log"
        self.assertEqual(oct(log.stat().st_mode & 0o777), "0o600")

    def test_the_install_folder_may_belong_to_any_wine_user(self):
        shutil.move(self.prefix / "drive_c/users/steamuser", self.prefix / "drive_c/users/pilot")
        self.nest("launch", FSOL_SIM_RUNNING="1")
        self.wait_for_record()
        self.assertEqual(self.cli_runs(), [f"{self.root}/cli run start /d {WINDOWS_DIR.replace('steamuser', 'pilot')} {WINDOWS_EXE.replace('steamuser', 'pilot')}"])


class NestCloseTest(NestCase):
    def test_nothing_running_means_nothing_to_close(self):
        result = self.nest("close")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "BBS Nest is not running"))

    def test_with_the_simulator_running_the_window_is_asked_to_close_inside_the_container(self):
        process = self.stand_in(WINDOWS_EXE)
        self.stubs(after=f"kill {process.pid}\n")
        result = self.nest("close", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded()[0], f"{self.root}/cli run taskkill /im bbs_nest.exe")

    def test_with_the_simulator_closed_the_window_is_asked_to_close_through_the_prefix(self):
        process = self.stand_in(WINDOWS_EXE)
        self.stubs(after=f"kill {process.pid}\n")
        result = self.nest("close")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded()[0], f"{self.proton}/files/bin/wine taskkill /im bbs_nest.exe")

    def test_a_nest_that_stays_is_reported_with_the_kill_command_and_never_killed_by_the_script(self):
        process = self.stand_in(WINDOWS_EXE)
        result = self.nest("close")
        self.assertEqual(result.returncode, 1)
        self.assertIn("still open", result.stderr)
        self.assertIn(r"pkill -9 -f 'NEST\\bbs_nest\.ex[e]'", result.stderr)
        self.assertIsNone(process.poll())

    def test_the_request_never_carries_the_force_flag(self):
        process = self.stand_in(WINDOWS_EXE)
        self.stubs(after=f"kill {process.pid}\n")
        self.nest("close")
        self.assertNotIn("/f", " ".join(self.recorded()).split())


def pe_with_stamp(stamp):
    header = bytearray(0x100)
    header[0:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, 0x80)
    header[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<I", header, 0x88, stamp)
    return bytes(header)


class FakeNestProcess:
    BASE = 0x10000
    FIRST = 0x40000
    STEP = 0x2000

    def __init__(self, folder, stamp=connections.MEASURED_STAMP, cmdline=WINDOWS_EXE, maps=True):
        self.proc = Path(folder) / "proc"
        self.pid_dir = self.proc / "4321"
        write(self.pid_dir / "cmdline", cmdline + "\0")
        self.dll = Path(folder) / "BlueBird Simulations" / "NEST" / "SubSystem.dll"
        self.dll.parent.mkdir(parents=True, exist_ok=True)
        self.dll.write_bytes(pe_with_stamp(stamp))
        if maps:
            write(self.pid_dir / "maps", f"00400000-00410000 r-xp 00000000 08:01 1 /unrelated/other.dll\n{self.BASE:08x}-{self.BASE + 0x1000:08x} r-xp 00000000 08:01 2 {self.dll}\n")
        self.memory = bytearray(0x80000)

    def connection(self, number, sequence, queued, following):
        address = self.FIRST + number * self.STEP
        struct.pack_into("<I", self.memory, address + connections.SEQUENCE, sequence)
        struct.pack_into("<I", self.memory, address + connections.INBOUND_COUNT, queued)
        struct.pack_into("<Q", self.memory, address + connections.NEXT, following)
        return address

    def head(self, address):
        struct.pack_into("<Q", self.memory, self.BASE + connections.LIST_HEAD, address)

    def publish(self):
        (self.pid_dir / "mem").write_bytes(bytes(self.memory))


class ConnectionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.nest = FakeNestProcess(self.temp.name)

    def run_main(self):
        import contextlib
        import io

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = connections.main(self.nest.proc)
        return code, output.getvalue().splitlines()

    def chain(self, *queued):
        addresses = [FakeNestProcess.FIRST + number * FakeNestProcess.STEP for number in range(len(queued))]
        for number, count in enumerate(queued):
            self.nest.connection(number, number + 1, count, addresses[number + 1] if number + 1 < len(addresses) else 0)
        self.nest.head(addresses[0])
        self.nest.publish()

    def test_one_connection_is_listed_with_its_queue_and_is_fine(self):
        self.chain(0)
        code, lines = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(lines, ["connection 1: 0 messages waiting", "One connection: the orphan SimConnect connection is not there."])

    def test_two_connections_mean_the_leak_and_exit_with_2(self):
        self.chain(0, 399621)
        code, lines = self.run_main()
        self.assertEqual(code, 2)
        self.assertEqual(lines[:2], ["connection 1: 0 messages waiting", "connection 2: 399621 messages waiting"])
        self.assertIn("with the 757 already loaded", lines[-1])

    def test_a_list_that_loops_back_stops_after_one_lap(self):
        address = self.nest.connection(0, 1, 5, FakeNestProcess.FIRST)
        self.nest.head(address)
        self.nest.publish()
        code, lines = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], "connection 1: 5 messages waiting")

    def test_a_list_longer_than_sixteen_stops_at_sixteen(self):
        self.chain(*[0] * 20)
        code, lines = self.run_main()
        self.assertEqual(code, 2)
        self.assertEqual(len([line for line in lines if line.startswith("connection")]), 16)

    def test_no_nest_process_says_so_and_exits_with_1(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.nest = FakeNestProcess(folder.name, cmdline=r"C:\windows\system32\notepad.exe")
        self.assertEqual(self.run_main(), (1, ["BBS Nest is not open."]))

    def test_a_nest_that_has_not_loaded_the_library_yet_exits_with_1(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.nest = FakeNestProcess(folder.name, maps=False)
        write(self.nest.pid_dir / "maps", "00400000-00410000 r-xp 00000000 08:01 1 /unrelated/other.dll\n")
        code, lines = self.run_main()
        self.assertEqual((code, lines), (1, ["Nest 4321 has not loaded SubSystem.dll yet."]))

    def test_unreadable_memory_exits_with_3_and_says_why(self):
        code, lines = self.run_main()
        self.assertEqual(code, 3)
        self.assertIn("Cannot read the connection list of Nest 4321", lines[-1])

    def test_a_library_build_other_than_the_measured_one_is_flagged(self):
        self.nest.dll.write_bytes(pe_with_stamp(0x12345678))
        self.chain(0)
        code, lines = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("TimeDateStamp 0x12345678", lines[0])
        self.assertIn(hex(connections.MEASURED_STAMP), lines[0])

    def test_the_measured_build_prints_no_warning(self):
        self.chain(0)
        self.assertFalse(any("TimeDateStamp" in line for line in self.run_main()[1]))

    def test_the_script_hands_over_to_the_python_part_in_isolated_mode(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        record = Path(folder.name) / "record"
        stub(Path(folder.name) / "bin" / "python3", recording(record))
        result = subprocess.run([str(NEST / "connections")], env={"PATH": f"{Path(folder.name) / 'bin'}:{os.environ['PATH']}"}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(record.read_text().splitlines()[0], f"{Path(folder.name) / 'bin'}/python3 -I {NEST}/parts/connections.py")


class AircraftCase(MachineCase):
    def setUp(self):
        super().setUp()
        self.stubs()
        write(self.prefix / NEST_DIR / "bbs_nest.exe", "MZ")
        self.make_package(PACKAGE, "0.1.0", {"SimObjects/a.cfg": "aircraft", "panel/panel.cfg": "panel"})
        self.make_package(LIVERIES, "1.0.0", {"liveries/one.dds": "texture"})

    def make_package(self, name, version, files):
        package = self.community / name
        content = []
        for path, data in files.items():
            write(package / path, data)
            content.append({"path": path, "size": len(data.encode()), "date": 1})
        write(package / "manifest.json", json.dumps({"title": name, "package_version": version}))
        write(package / "layout.json", json.dumps({"content": content}))

    def aircraft(self, name, **extra):
        return self.script(AIRCRAFT, name, **extra)


class AircraftTest(AircraftCase):
    def assert_only_failure(self, word):
        result = self.aircraft("check")
        lines = result.stdout.splitlines()
        self.assertEqual(result.returncode, 1, lines)
        failures = [line for line in lines if line.startswith("FAIL")]
        self.assertEqual(len(failures), 1, lines)
        self.assertIn(word, failures[0])
        self.assertEqual(len(lines), 3)

    def test_the_manifest_is_complete(self):
        addon = addons.load(AIRCRAFT)
        self.assertEqual((addon.id, addon.needs, addon.tested_version, addon.tested_on), ("bbs-757", ["proton-fs2024"], "0.1.0", "2026-10-28"))
        self.assertEqual(addon.install["kind"], "store")
        self.assertFalse(addon.can_install)
        self.assertEqual(addon.shortcuts, [])
        self.assertIsNone(addon.watcher)
        self.assertTrue(addons.is_installed(addon, self.found))

    def test_a_machine_with_both_packages_and_nest_passes_with_three_ok_lines_that_name_the_versions(self):
        result = self.aircraft("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        lines = result.stdout.splitlines()
        self.assertEqual([line.split()[0] for line in lines], ["ok"] * 3)
        self.assertIn("0.1.0", lines[0])
        self.assertIn("1.0.0", lines[1])

    def test_a_missing_aircraft_package_fails(self):
        shutil.rmtree(self.community / PACKAGE)
        self.assert_only_failure("install it with BBS Nest")

    def test_a_missing_liveries_package_fails(self):
        shutil.rmtree(self.community / LIVERIES)
        self.assert_only_failure(LIVERIES)

    def test_a_file_missing_from_disk_fails_and_is_named(self):
        (self.community / PACKAGE / "panel/panel.cfg").unlink()
        self.assert_only_failure("panel/panel.cfg")

    def test_a_file_whose_size_differs_from_the_layout_fails(self):
        (self.community / LIVERIES / "liveries/one.dds").write_text("a longer texture")
        self.assert_only_failure("1 of the 1 files")

    def test_a_layout_that_cannot_be_read_fails(self):
        (self.community / PACKAGE / "layout.json").write_text("{not json")
        self.assert_only_failure("layout.json")

    def test_no_nest_in_the_prefix_fails(self):
        (self.prefix / NEST_DIR / "bbs_nest.exe").unlink()
        self.assert_only_failure("addon install bbs-nest")

    def test_no_folders_found_fails_every_line(self):
        result = self.aircraft("check", FSOL_COMMUNITY="", FSOL_PREFIX="")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(all(line.startswith("FAIL no ") for line in result.stdout.splitlines()), result.stdout)

    def test_apply_and_undo_say_there_is_nothing_to_do_and_change_nothing(self):
        before = sorted(str(path) for path in self.root.rglob("*"))
        for name in ("apply", "undo"):
            result = self.aircraft(name)
            self.assertEqual(result.returncode, 0)
            self.assertIn("already nothing to", result.stdout)
        self.assertEqual(sorted(str(path) for path in self.root.rglob("*")), before)


ORIGINAL_PANEL = "[Color]\nhtmlgauge00=WasmInstrument/WasmInstrument.html?wasm_module=BBS752.wasm&wasm_gauge=pfd_l, 21,8,697,640\nhtmlgauge01=WasmInstrument/WasmInstrument.html?wasm_module=BBS752.wasm&wasm_gauge=nd_l, 21,735,704,859\n"
MODDED_PANEL = ORIGINAL_PANEL + "htmlgauge02=WasmInstrument/WasmInstrument.html?wasm_module=BBS757WX.wasm&wasm_gauge=wx_l, 21,735,704,859\nhtmlgauge07=WasmInstrument/WasmInstrument.html?wasm_module=BBS757WX.wasm&wasm_gauge=wx_r, 1630,735,704,859\n"
MOD_STUB = """#!/bin/sh
printf '%s\\n' "$0 $*" >> "$RECORD"
for folder in "$FSOL_COMPATDATA"/flightsim-on-linux-backup/bbs-757-weather-radar; do
  printf 'backup:%s\\n' "$(ls "$folder" | tr '\\n' ' ')" >> "$RECORD"
done
panel="$FSOL_COMMUNITY/bluebirdsim-aircraft-757-200/SimObjects/Airplanes/bbs-757-200-base/panel"
[ "${MOD_FAIL:-0}" = 1 ] && exit 1
printf 'htmlgauge02=WasmInstrument/WasmInstrument.html?wasm_module=BBS757WX.wasm&wasm_gauge=wx_l, 1,1,1,1\\nhtmlgauge07=WasmInstrument/WasmInstrument.html?wasm_module=BBS757WX.wasm&wasm_gauge=wx_r, 1,1,1,1\\n' >> "$panel/panel.cfg"
printf 'patched wasm' > "$panel/BBS752.wasm"
printf 'radar module' > "$panel/BBS757WX.wasm"
python3 - "$FSOL_COMMUNITY/bluebirdsim-aircraft-757-200/layout.json" <<'PY'
import json, sys
file = sys.argv[1]
data = json.load(open(file))
data["content"].append({"path": "SimObjects/Airplanes/bbs-757-200-base/panel/BBS757WX.wasm", "size": 12, "date": 2})
json.dump(data, open(file, "w"))
PY
"""


class RadarCase(MachineCase):
    def setUp(self):
        super().setUp()
        self.package = self.community / PACKAGE
        self.panel = self.package / PANEL
        write(self.panel / "panel.cfg", ORIGINAL_PANEL)
        (self.panel / "BBS752.wasm").write_bytes(b"original wasm")
        write(self.package / "layout.json", json.dumps({"content": [{"path": f"{PANEL}/panel.cfg", "size": 1, "date": 1}, {"path": f"{PANEL}/BBS752.wasm", "size": 13, "date": 1}]}))
        self.backup = self.found.compatdata / "flightsim-on-linux-backup" / "bbs-757-weather-radar"
        self.bundle = self.root / "downloads" / "radar-bundle"
        write(self.bundle / "scripts" / "Install.ps1", "# stands in for the mod's own installer\n")
        write(self.bundle / "scripts" / "Support.ps1", "# stands in for the mod's own helpers\n")
        self.stubs()
        stub(self.root / "bin" / "pwsh", MOD_STUB)

    def install(self, given=None, **extra):
        env = {"RECORD": str(self.record), **extra}
        if given is not None:
            env["FSOL_INSTALLER"] = str(given)
        return self.script(RADAR, "install", **env)

    def modded(self):
        return "BBS757WX.wasm" in (self.panel / "panel.cfg").read_text()


class RadarShapeTest(RadarCase):
    def test_the_manifest_is_complete(self):
        addon = addons.load(RADAR)
        self.assertEqual((addon.id, addon.needs, addon.tested_version, addon.tested_on), ("bbs-757-weather-radar", ["simulator-closed"], "1.0.3", "2026-10-28"))
        self.assertTrue(addon.touches_addon_files)
        self.assertEqual(addon.install["kind"], "installer")
        self.assertTrue(addon.can_install)
        self.assertFalse(addons.is_installed(addon, self.found))

    def test_it_counts_as_installed_once_the_radar_module_is_there(self):
        write(self.panel / "BBS757WX.wasm", "x")
        self.assertTrue(addons.is_installed(addons.load(RADAR), self.found))

    def test_apply_says_the_install_does_the_work_and_changes_nothing(self):
        before = sorted(str(path) for path in self.root.rglob("*"))
        result = self.script(RADAR, "apply")
        self.assertEqual(result.returncode, 0)
        self.assertIn("addon install bbs-757-weather-radar <the mod's zip>", result.stdout)
        self.assertIn("addon launch bbs-757-weather-radar install -- <folder>", result.stdout)
        self.assertNotIn("zip or", result.stdout)
        self.assertEqual(sorted(str(path) for path in self.root.rglob("*")), before)

    def test_the_ported_files_hold_no_personal_path_and_none_of_the_mods_own_files(self):
        parts = sorted(path.name for path in (RADAR / "parts").iterdir() if path.is_file())
        self.assertEqual(parts, ["JavaScriptSerializer.cs", "install-weather-radar.ps1", "radar.py"])
        for file in RADAR.rglob("*"):
            if file.is_file() and "__pycache__" not in file.parts:
                text = file.read_text(errors="replace")
                for word in ("/home/", "Jogos", "brunom", "$HOME"):
                    self.assertNotIn(word, text, f"{file.name}: {word}")

    def test_the_ported_installer_asks_for_the_community_folder_instead_of_assuming_one(self):
        text = (RADAR / "parts" / "install-weather-radar.ps1").read_text()
        self.assertIn("[Parameter(Mandatory)][string]$Community", text)


class RadarInstallTest(RadarCase):
    def test_the_originals_are_backed_up_before_the_mods_installer_runs_and_it_gets_the_bundle_and_community(self):
        result = self.install(self.bundle)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.recorded()
        self.assertEqual(calls[0], f"{self.root}/bin/pwsh -NoProfile -File {RADAR}/parts/install-weather-radar.ps1 -Bundle {self.bundle} -Community {self.community}")
        self.assertIn("backup:BBS752.wasm layout.json panel.cfg ", calls)
        self.assertEqual((self.backup / "panel.cfg").read_text(), ORIGINAL_PANEL)
        self.assertEqual((self.backup / "BBS752.wasm").read_bytes(), b"original wasm")
        self.assertTrue(self.modded())

    def test_a_zip_of_the_mod_is_unpacked_to_a_temporary_folder_that_is_removed_afterwards(self):
        archive = self.root / "downloads" / "B757 Weather Radar Installer 1.0.zip"
        shutil.make_archive(str(archive.with_suffix("")), "zip", self.bundle.parent, self.bundle.name)
        result = self.install(archive)
        self.assertEqual(result.returncode, 0, result.stderr)
        bundle = self.recorded()[0].split(" -Bundle ")[1].split(" -Community ")[0]
        self.assertTrue(bundle.endswith("/radar-bundle"), bundle)
        self.assertFalse(Path(bundle).exists())
        self.assertTrue(self.modded())

    def test_the_bundle_may_be_given_as_an_argument_instead_of_the_installer_variable(self):
        result = self.script(RADAR, "install", str(self.bundle), RECORD=str(self.record))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.modded())

    def test_a_running_simulator_stops_it_before_anything_is_touched(self):
        result = self.install(self.bundle, FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("close the simulator first", result.stderr)
        self.assertFalse(self.backup.exists())
        self.assertFalse(self.modded())

    def test_nothing_given_says_how_to_give_the_mod(self):
        result = self.install()
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install bbs-757-weather-radar <the mod's zip>", result.stderr)
        self.assertIn("addon launch bbs-757-weather-radar install -- <folder>", result.stderr)

    def test_a_folder_without_the_mods_scripts_is_refused(self):
        empty = self.root / "empty"
        empty.mkdir()
        result = self.install(empty)
        self.assertEqual(result.returncode, 1)
        self.assertIn("scripts/Install.ps1", result.stderr)
        self.assertFalse(self.backup.exists())

    def test_something_that_is_neither_a_folder_nor_a_zip_is_refused(self):
        junk = self.root / "downloads" / "junk.zip"
        write(junk, "not a zip")
        result = self.install(junk)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.backup.exists())

    def test_without_pwsh_it_says_so_and_touches_nothing(self):
        tools = self.root / "tools-only"
        tools.mkdir()
        for name in ("bash", "env", "python3", "find", "sed", "mktemp", "rm", "dirname", "readlink", "cat", "printf", "tr", "ls", "head", "pgrep", "sleep"):
            found = shutil.which(name)
            if found:
                os.symlink(found, tools / name)
        result = self.install(self.bundle, PATH=str(tools))
        self.assertEqual(result.returncode, 1)
        self.assertIn("pwsh (PowerShell 7) is not installed", result.stderr)
        self.assertFalse(self.backup.exists())

    def test_a_mod_installer_that_fails_leaves_the_backup_and_points_at_undo(self):
        result = self.install(self.bundle, MOD_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon undo bbs-757-weather-radar", result.stderr)
        self.assertTrue((self.backup / "panel.cfg").is_file())

    def test_a_second_install_keeps_the_first_backup_because_the_aircraft_files_are_no_longer_the_originals(self):
        self.install(self.bundle)
        (self.backup / "panel.cfg").write_text("the first backup")
        result = self.install(self.bundle)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.backup / "panel.cfg").read_text(), "the first backup")

    def test_after_a_nest_update_put_the_originals_back_the_backup_is_taken_again(self):
        self.install(self.bundle)
        (self.panel / "panel.cfg").write_text(ORIGINAL_PANEL + "; new nest version\n")
        (self.panel / "BBS752.wasm").write_bytes(b"newer original wasm")
        (self.panel / "BBS757WX.wasm").unlink()
        self.install(self.bundle)
        self.assertEqual((self.backup / "BBS752.wasm").read_bytes(), b"newer original wasm")
        self.assertIn("new nest version", (self.backup / "panel.cfg").read_text())

    def test_an_aircraft_that_holds_the_radar_with_no_backup_is_not_backed_up_over_and_pwsh_does_not_run(self):
        (self.panel / "panel.cfg").write_text(MODDED_PANEL)
        result = self.install(self.bundle)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no backup", result.stderr)
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.recorded(), [])

    def test_no_757_package_is_said_and_pwsh_does_not_run(self):
        shutil.rmtree(self.package)
        result = self.install(self.bundle)
        self.assertEqual(result.returncode, 2)
        self.assertIn("install the aircraft with BBS Nest first", result.stderr)
        self.assertEqual(self.recorded(), [])


class RadarUndoTest(RadarCase):
    def test_undo_puts_the_two_aircraft_files_and_the_index_back_and_removes_the_radar_module(self):
        self.install(self.bundle)
        layout_before = json.loads((self.package / "layout.json").read_text())
        self.assertEqual(len(layout_before["content"]), 3)
        result = self.script(RADAR, "undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.panel / "panel.cfg").read_text(), ORIGINAL_PANEL)
        self.assertEqual((self.panel / "BBS752.wasm").read_bytes(), b"original wasm")
        self.assertFalse((self.panel / "BBS757WX.wasm").exists())
        self.assertEqual(len(json.loads((self.package / "layout.json").read_text())["content"]), 2)
        self.assertFalse(self.backup.exists())

    def test_undo_without_a_backup_on_an_untouched_aircraft_says_so(self):
        result = self.script(RADAR, "undo")
        self.assertEqual(result.returncode, 0)
        self.assertIn("already no backup to restore", result.stdout)

    def test_undo_without_a_backup_on_a_modified_aircraft_fails_and_leaves_it(self):
        (self.panel / "panel.cfg").write_text(MODDED_PANEL)
        result = self.script(RADAR, "undo")
        self.assertEqual(result.returncode, 1)
        self.assertIn("reinstall the aircraft with BBS Nest", result.stderr)
        self.assertEqual((self.panel / "panel.cfg").read_text(), MODDED_PANEL)

    def test_a_second_undo_changes_nothing(self):
        self.install(self.bundle)
        self.script(RADAR, "undo")
        again = self.script(RADAR, "undo")
        self.assertEqual(again.returncode, 0)
        self.assertEqual((self.panel / "panel.cfg").read_text(), ORIGINAL_PANEL)


class RadarCheckTest(RadarCase):
    def installed(self):
        self.install(self.bundle)

    def assert_failures(self, *words):
        result = self.script(RADAR, "check")
        lines = result.stdout.splitlines()
        self.assertEqual(result.returncode, 1, lines)
        self.assertEqual(len(lines), 3)
        failures = [line for line in lines if line.startswith("FAIL")]
        self.assertEqual(len(failures), len(words), lines)
        for failure, word in zip(failures, words):
            self.assertIn(word, failure)

    def test_an_aircraft_with_the_radar_passes_with_three_ok_lines(self):
        self.installed()
        result = self.script(RADAR, "check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok"] * 3)

    def test_an_aircraft_without_the_radar_fails_every_line(self):
        self.assert_failures("no BBS757WX.wasm gauge for wx_l, wx_r", "missing or empty", "does not list")

    def test_the_failing_panel_line_gives_the_folder_command_that_the_cli_install_cannot_take(self):
        result = self.script(RADAR, "check")
        panel = next(line for line in result.stdout.splitlines() if "no BBS757WX.wasm gauge" in line)
        self.assertIn("addon install bbs-757-weather-radar <the mod's zip>", panel)
        self.assertIn("addon launch bbs-757-weather-radar install -- <folder>", panel)

    def test_a_panel_with_only_the_left_gauge_fails_naming_the_right_one(self):
        self.installed()
        lines = [line for line in (self.panel / "panel.cfg").read_text().splitlines() if "wx_r" not in line]
        (self.panel / "panel.cfg").write_text("\n".join(lines) + "\n")
        self.assert_failures("wx_r")

    def test_a_missing_radar_module_fails_its_line_and_the_index_line(self):
        self.installed()
        (self.panel / "BBS757WX.wasm").unlink()
        self.assert_failures("missing or empty", "does not list")

    def test_an_empty_radar_module_fails(self):
        self.installed()
        (self.panel / "BBS757WX.wasm").write_bytes(b"")
        self.assert_failures("missing or empty", "does not list")

    def test_a_layout_without_the_radar_module_fails(self):
        self.installed()
        layout = json.loads((self.package / "layout.json").read_text())
        layout["content"] = [entry for entry in layout["content"] if "BBS757WX" not in entry["path"]]
        (self.package / "layout.json").write_text(json.dumps(layout))
        self.assert_failures("layout.json does not list")

    def test_a_layout_with_the_wrong_size_fails(self):
        self.installed()
        (self.panel / "BBS757WX.wasm").write_bytes(b"a different size entirely")
        self.assert_failures("layout.json does not list")

    def test_a_missing_package_fails_with_one_line(self):
        shutil.rmtree(self.package)
        result = self.script(RADAR, "check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertIn("install the aircraft with BBS Nest first", result.stdout)

    def test_no_community_folder_fails_instead_of_reading_the_current_folder(self):
        result = self.script(RADAR, "check", FSOL_COMMUNITY="")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.splitlines(), ["FAIL no Community folder found; run: flightsim-on-linux machine"])

    def test_check_changes_nothing(self):
        self.installed()
        before = sorted((str(path), path.stat().st_mtime_ns) for path in self.root.rglob("*") if path.is_file())
        self.script(RADAR, "check")
        self.assertEqual(sorted((str(path), path.stat().st_mtime_ns) for path in self.root.rglob("*") if path.is_file()), before)


FAKE_INSTALL = """param(
    [Parameter(Mandatory)][string]$AircraftRoot,
    [Parameter(Mandatory)][string]$Community,
    [switch]$NonInteractive,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Support.ps1')
Add-Type -ReferencedAssemblies System.Web.Extensions -Path (Join-Path $PSScriptRoot 'Helper.cs')
$serializer = New-Object System.Web.Script.Serialization.JavaScriptSerializer
$gauge = $serializer.Deserialize[System.Collections.Generic.Dictionary[string,string]]('{"gauge":"wx_l"}')['gauge']
$panel = Join-Path $AircraftRoot 'SimObjects/Airplanes/bbs-757-200-base/panel'
if (-not (Test-Within $AircraftRoot $panel)) { throw 'the panel is outside the aircraft' }
if ($NonInteractive -and -not $DryRun) {
    Add-Content -LiteralPath (Join-Path $panel 'panel.cfg') -Value "htmlgauge02=WasmInstrument/WasmInstrument.html?wasm_module=BBS757WX.wasm&wasm_gauge=$gauge"
    Set-Content -LiteralPath (Join-Path $panel 'BBS757WX.wasm') -Value ([Radar.Helper]::Name())
    Write-Output ("community=" + $Community)
}
"""
FAKE_SUPPORT = """function Get-FullPath([string]$Path) { return 'replaced by the launcher' }
function Test-Within([string]$Root, [string]$Path) { return $false }
"""
FAKE_HELPER = """namespace Radar { public static class Helper { public static string Name() { return "radar module"; } } }
"""


@unittest.skipUnless(shutil.which("pwsh"), "needs pwsh")
class RadarWithRealPwshTest(RadarCase):
    def setUp(self):
        super().setUp()
        (self.root / "bin" / "pwsh").unlink()
        write(self.bundle / "scripts" / "Install.ps1", FAKE_INSTALL)
        write(self.bundle / "scripts" / "Support.ps1", FAKE_SUPPORT)
        write(self.bundle / "scripts" / "Helper.cs", FAKE_HELPER)

    def test_the_ported_launcher_runs_a_mod_installer_that_uses_the_windows_only_serializer_and_path_helpers(self):
        result = self.install(self.bundle)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"community={self.community}", result.stdout)
        self.assertTrue(self.modded())
        self.assertIn("wx_l", (self.panel / "panel.cfg").read_text())
        self.assertEqual((self.panel / "BBS757WX.wasm").read_text().strip(), "radar module")

    def test_the_launcher_replaces_the_windows_path_helpers_of_the_mods_support_script(self):
        result = self.install(self.bundle)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("outside the aircraft", result.stderr)

    def test_a_mod_installer_that_no_longer_dot_sources_its_support_script_is_refused(self):
        write(self.bundle / "scripts" / "Install.ps1", "param([string]$AircraftRoot, [string]$Community, [switch]$NonInteractive, [switch]$DryRun)\n")
        result = self.install(self.bundle)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no longer dot-sources Support.ps1", result.stdout + result.stderr)
        self.assertIn("addon undo bbs-757-weather-radar", result.stderr)


class PagesTest(unittest.TestCase):
    def test_the_three_pages_follow_the_template(self):
        for name in ("bbs-nest", "bbs-757", "bbs-757-weather-radar"):
            with self.subTest(page=name):
                self.assertEqual(page_problems(name), [])

    def test_the_nest_page_says_what_the_cards_say(self):
        text = (PAGES / "bbs-nest.html").read_text()
        for needed in (
            "start /d",
            "winewayland-pace-hidden-gl-swaps",
            "21.3%",
            "11.3%",
            "MSFS2024CommunityPath",
            "addon install bbs-nest",
            "addon launch bbs-nest connections",
            "pkill -9",
            "90 MB a minute",
            "43 MB a minute",
            "with the 757 already loaded",
            "bbs-757.html",
        ):
            self.assertIn(needed, text)
        self.assertNotIn("/home/", text)

    def test_the_aircraft_page_links_the_nest_page_and_says_the_weather_radar_is_not_a_linux_fault(self):
        text = (PAGES / "bbs-757.html").read_text()
        for needed in ("bbs-nest.html", "bbs-757-weather-radar.html", "not a Linux fault", "BBS|NEST is not running"):
            self.assertIn(needed, text)

    def test_the_radar_page_says_to_back_up_and_that_the_mods_files_are_not_shipped(self):
        text = (PAGES / "bbs-757-weather-radar.html").read_text()
        for needed in ("touches add-on files", "addon undo bbs-757-weather-radar", "pwsh", "no backup of its own", "not part of this project", "Nest update"):
            self.assertIn(needed, text)


if __name__ == "__main__":
    unittest.main()
