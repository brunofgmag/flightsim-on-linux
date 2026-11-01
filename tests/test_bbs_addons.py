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


class PagesTest(unittest.TestCase):
    def test_the_three_pages_follow_the_template(self):
        for name in ("bbs-nest", "bbs-757"):
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

    def test_the_aircraft_page_links_the_nest_page_and_says_the_missing_weather_is_not_a_linux_fault(self):
        text = (PAGES / "bbs-757.html").read_text()
        for needed in ("bbs-nest.html", "not a Linux fault", "BBS|NEST is not running"):
            self.assertIn(needed, text)


if __name__ == "__main__":
    unittest.main()
