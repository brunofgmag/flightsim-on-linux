import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from fsol import addons, machine, watcher
from tests.support import process_stubs, start_stand_in, stub
from tests.test_machine import FakeMachine, write

ROOT = Path(__file__).resolve().parent.parent
ADDONS = ROOT / "addons"
HOLDER = "Q:\\tools\\shims\\desktop-holder\\desktop-holder.exe"
SENTINEL = "Q:\\tools\\shims\\sentinel\\FlightSimulator2024.exe"
FAKE_COMPILER = '#!/bin/sh\nwhile [ "$1" != -o ]; do shift; done\nprintf MZ > "$2"\n'


class PrefixCase(unittest.TestCase):
    ID = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.fake = FakeMachine(self.root).build()
        self.found = self.fake.discover()
        self.prefix = self.found.prefix
        self.proton = self.fake.tool
        self.record = self.root / "record"
        self.token = str(self.root)
        self.sibling = self.root / "data/flightsim-on-linux/addons-prefix"
        self.processes = []
        for name in ("system.reg", "user.reg"):
            write(self.prefix / name, f"sim {name}\n")
        (self.prefix / "drive_c").mkdir(parents=True, exist_ok=True)
        write(self.found.base / "SimConnect.xml", "<SimConnect.Comm><Protocol>IPv4</Protocol><Port>5111</Port></SimConnect.Comm>")
        process_stubs(self.root / "bin", self.token)
        stub(self.root / "bin/x86_64-w64-mingw32-gcc", FAKE_COMPILER)
        stub(self.root / "bin/notify-send", "#!/bin/sh\nexit 0\n")
        self.wine()
        stub(self.proton / "files/bin/wineserver", f'#!/bin/sh\nprintf "wineserver %s %s\\n" "$WINEPREFIX" "$*" >> "{self.record}"\n')

    def tearDown(self):
        for process in self.processes:
            process.kill()
            process.wait()
        self.temp.cleanup()

    def wine(self, exit_code=0):
        stub(self.proton / "files/bin/wine", f'#!/bin/sh\nprintf "wine %s\\n" "$*" >> "{self.record}"\nprintf "%s|%s|%s\\n" "$WINEPREFIX" "$PWD" "$WINEDEBUG" >> "{self.record}.env"\nexit {exit_code}\n')

    def environment(self, **extra):
        env = {
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "FSOL_PREFIX": str(self.prefix),
            "FSOL_COMPATDATA": str(self.found.compatdata),
            "FSOL_PROTON": str(self.proton),
            "FSOL_BASE": str(self.found.base),
            "FSOL_CACHE": str(self.root / "cache"),
            "FSOL_CLI": str(self.root / "cli"),
            "FSOL_ADDON": self.ID,
            "FSOL_SIM_RUNNING": "0",
        }
        env.update(extra)
        return env

    def script(self, name, *args, **extra):
        return subprocess.run([str(ADDONS / self.ID / name), *args], env=self.environment(**extra), capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def recorded(self):
        return self.record.read_text().splitlines() if self.record.exists() else []

    def recorded_env(self):
        path = Path(f"{self.record}.env")
        return path.read_text().splitlines() if path.exists() else []

    def wait_for_record(self, lines=1, seconds=5):
        deadline = time.monotonic() + seconds
        while (len(self.recorded()) < lines or not self.recorded_env()) and time.monotonic() < deadline:
            time.sleep(0.05)

    def stand_in(self, program):
        self.processes.append(start_stand_in(self.root, self.token, program, 60))
        time.sleep(0.2)

    def ok_or_fail(self, result):
        return [line.split()[0] for line in result.stdout.splitlines()]


class ManifestTest(unittest.TestCase):
    def test_both_load_and_keep_their_programs_up_by_state(self):
        holder = addons.load(ADDONS / "addons-prefix").watcher
        sentinel = addons.load(ADDONS / "sentinel").watcher
        self.assertEqual((holder["trigger"], holder["stop"]), ("always", "never"))
        self.assertEqual((sentinel["trigger"], sentinel["stop"]), ("while_simulator", "with_simulator"))

    def test_the_patterns_match_their_programs_on_any_drive(self):
        for name, program in (("addons-prefix", "Z:\\cache\\shims\\desktop-holder\\desktop-holder.exe"), ("sentinel", "Z:\\cache\\shims\\sentinel\\FlightSimulator2024.exe")):
            self.assertTrue(re.search(addons.load(ADDONS / name).watcher["app"], program), name)

    def test_the_sentinel_pattern_never_takes_the_simulator_for_the_sentinel(self):
        pattern = addons.load(ADDONS / "sentinel").watcher["app"]
        self.assertFalse(re.search(pattern, "D:\\steamapps\\common\\MSFS2024\\FlightSimulator2024.exe"))

    def test_the_simulator_detection_does_not_take_the_sentinel_for_the_simulator(self):
        with tempfile.TemporaryDirectory() as folder:
            write(Path(folder) / "4100/cmdline", "Z:\\cache\\flightsim-on-linux\\shims\\sentinel\\FlightSimulator2024.exe\0")
            self.assertIsNone(machine.sim_pid(Path(folder)))

    def test_their_services_never_take_the_programs_down_with_them(self):
        for name in ("addons-prefix", "sentinel"):
            text = watcher.unit_text(addons.load(ADDONS / name), ROOT / "bin" / "flightsim-on-linux")
            self.assertIn("KillMode=process", text, name)
            self.assertIn(f"addon watch {name}", text, name)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "needs systemd-analyze")
    def test_their_services_pass_systemd_verify(self):
        for name in ("addons-prefix", "sentinel"):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "unit.service"
                path.write_text(watcher.unit_text(addons.load(ADDONS / name), ROOT / "bin" / "flightsim-on-linux"))
                result = subprocess.run(["systemd-analyze", "--user", "verify", str(path)], capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""), name)


class AddonsPrefixCase(PrefixCase):
    ID = "addons-prefix"


class AddonsPrefixApplyTest(AddonsPrefixCase):
    def test_apply_makes_the_prefix_its_cfg_and_the_holder(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.sibling / "system.reg").read_text(), "sim system.reg\n")
        self.assertIn("Port=5111", (self.sibling / "SimConnect.cfg").read_text())
        self.assertTrue((self.root / "cache/shims/desktop-holder/desktop-holder.exe").is_file())
        self.assertIn("addon watcher addons-prefix", result.stdout)

    def test_a_built_machine_passes_with_the_service_off_and_says_how_to_turn_it_on(self):
        self.script("apply")
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(set(self.ok_or_fail(result)), {"ok"})
        self.assertIn("addon watcher addons-prefix", result.stdout)

    def test_a_running_holder_is_reported(self):
        self.script("apply")
        self.stand_in(HOLDER)
        self.assertIn("ok the desktop of the add-ons prefix is being kept up", self.script("check").stdout)

    def test_a_machine_with_nothing_applied_fails_with_the_command(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("run: flightsim-on-linux addon apply addons-prefix", result.stdout)

    def test_an_unbuilt_holder_fails(self):
        self.script("apply")
        (self.root / "cache/shims/desktop-holder/desktop-holder.exe").unlink()
        self.assertIn("is not built", self.script("check").stdout)

    def test_undo_removes_the_cfg_and_leaves_the_prefix(self):
        self.script("apply")
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.sibling / "SimConnect.cfg").exists())
        self.assertTrue((self.sibling / "system.reg").exists())
        self.assertIn("addon watcher addons-prefix --off", result.stdout)


class AddonsPrefixLaunchTest(AddonsPrefixCase):
    def setUp(self):
        super().setUp()
        self.script("apply")

    def test_it_starts_the_holder_in_the_add_ons_prefix(self):
        result = self.script("launch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(len(self.recorded()), 1)
        self.assertIn("desktop-holder.exe", self.recorded()[0])
        self.assertEqual(self.recorded_env()[0], f"{self.sibling}|{self.sibling}|err+all")

    def test_a_holder_that_finds_no_desktop_restarts_the_wine_server_of_that_prefix_only(self):
        self.wine(exit_code=1)
        self.script("launch")
        self.wait_for_record(lines=2)
        self.assertEqual(self.recorded()[1], f"wineserver {self.sibling} -k")

    def test_a_holder_that_ends_any_other_way_leaves_the_wine_server(self):
        self.wine(exit_code=143)
        self.script("launch")
        self.wait_for_record()
        time.sleep(0.3)
        self.assertEqual(len(self.recorded()), 1)

    def test_a_holder_already_running_is_left_alone(self):
        self.stand_in(HOLDER)
        self.assertEqual(self.script("launch").returncode, 0)
        time.sleep(0.2)
        self.assertEqual(self.recorded(), [])

    def test_a_missing_prefix_says_how_to_make_it(self):
        (self.sibling / "system.reg").unlink()
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon apply addons-prefix", result.stderr)


class AddonsPrefixCloseTest(AddonsPrefixCase):
    def test_a_stopped_prefix_means_nothing_to_close(self):
        self.script("apply")
        result = self.script("close")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "the add-ons prefix is not running"))
        self.assertEqual(self.recorded(), [])

    def test_a_running_prefix_loses_its_wine_server(self):
        self.script("apply")
        info = self.sibling.stat()
        folder = Path(f"/tmp/.wine-{os.getuid()}/server-{info.st_dev:x}-{info.st_ino:x}")
        folder.mkdir(parents=True, exist_ok=True)
        self.addCleanup(folder.rmdir)
        link = self.root / "wineserver"
        link.symlink_to(shutil.which("sleep"))
        server = subprocess.Popen(["wineserver", "30"], executable=str(link), cwd=folder, env={**os.environ, "FSOL_TEST_TOKEN": self.token})
        self.processes.append(server)
        time.sleep(0.2)
        result = self.script("close")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"wineserver {self.sibling} -k"])


class SentinelCase(PrefixCase):
    ID = "sentinel"


class SentinelTest(SentinelCase):
    def built(self):
        self.assertEqual(self.script("apply").returncode, 0)

    def test_apply_builds_the_program_under_the_simulators_file_name(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "cache/shims/sentinel/FlightSimulator2024.exe").is_file())
        self.assertIn("addon watcher sentinel", result.stdout)

    def test_check_fails_with_the_command_until_it_is_built(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon apply sentinel", result.stdout)

    def test_a_closed_simulator_passes_without_the_sentinel(self):
        self.built()
        self.assertEqual(self.script("check").returncode, 0)

    def test_a_running_simulator_without_the_sentinel_fails_with_the_command(self):
        self.built()
        result = self.script("check", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon watcher sentinel", result.stdout)

    def test_a_running_sentinel_passes(self):
        self.built()
        self.stand_in(SENTINEL)
        self.assertEqual(self.script("check", FSOL_SIM_RUNNING="1").returncode, 0)

    def test_undo_says_there_is_nothing_to_do(self):
        self.assertIn("already nothing to undo", self.script("undo").stdout)


class SentinelLaunchTest(SentinelCase):
    def setUp(self):
        super().setUp()
        write(self.sibling / "system.reg", "system\n")
        self.script("apply")

    def test_it_starts_in_the_add_ons_prefix(self):
        result = self.script("launch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertRegex(self.recorded()[0], r"sentinel.FlightSimulator2024\.exe$")
        self.assertEqual(self.recorded_env()[0].split("|")[:2], [str(self.sibling), str(self.sibling)])

    def test_a_sentinel_already_running_is_left_alone(self):
        self.stand_in(SENTINEL)
        self.assertEqual(self.script("launch").returncode, 0)
        time.sleep(0.2)
        self.assertEqual(self.recorded(), [])

    def test_a_missing_prefix_says_how_to_make_it(self):
        (self.sibling / "system.reg").unlink()
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon apply addons-prefix", result.stderr)


@unittest.skipUnless(shutil.which("x86_64-w64-mingw32-gcc"), "needs MinGW-w64")
class BuiltProgramsTest(unittest.TestCase):
    def built(self, name, relative):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        subprocess.run([str(ROOT / "shims" / name / "build.sh"), folder.name], check=True)
        return (Path(folder.name) / relative).read_bytes()

    def subsystem(self, data):
        header = int.from_bytes(data[0x3C:0x40], "little")
        return int.from_bytes(data[header + 24 + 68:header + 24 + 70], "little")

    def test_the_sentinel_is_a_windows_program_called_like_the_simulator(self):
        self.assertEqual(self.subsystem(self.built("sentinel", "sentinel/FlightSimulator2024.exe")), 2)

    def test_the_holder_is_a_console_program_like_the_one_that_flew(self):
        self.assertEqual(self.subsystem(self.built("desktop-holder", "desktop-holder/desktop-holder.exe")), 3)


if __name__ == "__main__":
    unittest.main()
