import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests.support import process_stubs, start_stand_in

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


def recording(file):
    return f'#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "{file}"\nenv >> "{file}.env"\n'


def script(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


class ShellCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.home = self.root / "home"
        self.home.mkdir()
        self.record = self.root / "record"
        self.processes = []
        script(self.bin / "notify-send", recording(self.record))
        self.proton = self.root / "proton"
        self.prefix = self.root / "pfx"
        (self.prefix / "drive_c").mkdir(parents=True)
        self.cli = script(self.root / "cli", recording(self.record))
        self.env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "XDG_DATA_HOME": str(self.root / "data"),
            "FSOL_CACHE": str(self.root / "cache"),
            "FSOL_PREFIX": str(self.prefix),
            "FSOL_PROTON": str(self.proton),
            "FSOL_CLI": str(self.cli),
            "FSOL_ADDON": "demo",
            "FSOL_SIM_RUNNING": "0",
        }

    def tearDown(self):
        for process in self.processes:
            process.kill()
            process.wait()
        self.temp.cleanup()

    def bash(self, text, **env):
        merged = dict(self.env, **env)
        return subprocess.run(["bash", "-c", text], env=merged, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def recorded(self):
        return self.record.read_text().splitlines() if self.record.exists() else []

    def recorded_env(self):
        path = Path(f"{self.record}.env")
        return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line) if path.exists() else {}

    def stand_in(self, name, argv0, folder=None):
        copy = self.root / "stand-in" / name
        copy.parent.mkdir(exist_ok=True)
        if not copy.exists():
            shutil.copy(shutil.which("sleep"), copy)
        process = subprocess.Popen([argv0, "60"], executable=copy, cwd=folder)
        self.processes.append(process)
        return process

    def fake_proton(self, wine_exit=0, server_exit=0):
        script(self.proton / "files/bin/wine", recording(self.record) + f"exit {wine_exit}\n")
        script(self.proton / "files/bin/wineserver", recording(f"{self.record}.server") + f"exit {server_exit}\n")


class LaunchHelpersTest(ShellCase):
    def source(self, body, **env):
        return self.bash(f'set -euo pipefail\n. "{TOOLS}/addon-launch.sh"\n{body}', **env)

    def test_a_second_copy_is_refused_with_a_notice_and_exit_zero(self):
        token = str(self.root)
        process_stubs(self.bin, token)
        self.processes.append(start_stand_in(self.root, token, "Q:\\Program Files\\SecondDemo.exe"))
        result = self.source("addon_refuse_second '^[A-Za-z]:.*\\\\SecondDemo\\.exe'\necho carried-on")
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertIn("demo: it is already running", result.stderr)
        self.assertIn("it is already running", "".join(self.recorded()))

    def test_without_a_copy_running_the_script_carries_on(self):
        result = self.source("addon_refuse_second '^[A-Za-z]:.*\\\\NoSuchDemo\\.exe'\necho carried-on")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "carried-on"))
        self.assertEqual(self.recorded(), [])

    def test_a_closed_simulator_stops_the_script_with_a_message_and_a_notice(self):
        result = self.source("addon_need_simulator\necho carried-on")
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("start Microsoft Flight Simulator 2024 first", result.stderr)
        self.assertIn("start Microsoft Flight Simulator 2024 first", "".join(self.recorded()))

    def test_a_running_simulator_lets_the_script_carry_on(self):
        result = self.source("addon_need_simulator\necho carried-on", FSOL_SIM_RUNNING="1")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "carried-on"))

    def test_the_notice_is_skipped_when_a_terminal_is_attached_and_the_message_is_not(self):
        result = subprocess.run(["script", "-qec", f'bash -c \'. "{TOOLS}/addon-launch.sh"; addon_need_simulator\'', "/dev/null"], env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL) if shutil.which("script") else None
        if result is None:
            self.skipTest("needs script(1)")
        self.assertIn("start Microsoft Flight Simulator 2024 first", result.stdout)
        self.assertEqual(self.recorded(), [])

    def test_paths_inside_the_prefix_become_c_paths_and_the_rest_z_paths(self):
        result = self.source(f'addon_windows_path "{self.prefix}/drive_c/users/steamuser/AppData/App/app.exe"\naddon_windows_path /home/pilot/Setup.exe')
        self.assertEqual(result.stdout.splitlines(), ["C:\\users\\steamuser\\AppData\\App\\app.exe", "Z:\\home\\pilot\\Setup.exe"])

    def test_beside_a_running_simulator_the_program_goes_through_the_cli_run(self):
        result = self.source("EXTRA_WINEDLLOVERRIDES=d3d9=d addon_beside_simulator DXVK_CONFIG= 'C:\\App\\app.exe' --flag", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.cli} run DXVK_CONFIG= C:\\App\\app.exe --flag"])
        self.assertEqual(self.recorded_env()["EXTRA_WINEDLLOVERRIDES"], "d3d9=d")

    def test_with_the_simulator_closed_the_program_goes_to_the_prefix_through_proton(self):
        self.fake_proton()
        result = self.source("EXTRA_WINEDLLOVERRIDES=d3d9=d addon_beside_simulator DXVK_CONFIG= 'C:\\App\\app.exe' --flag")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine C:\\App\\app.exe --flag"])
        env = self.recorded_env()
        self.assertEqual((env["WINEPREFIX"], env["WINEDLLOVERRIDES"], env["DXVK_CONFIG"]), (str(self.prefix), "d3d9=d", ""))

    def test_the_prefix_path_silences_wine_unless_the_caller_asks_for_a_channel(self):
        self.fake_proton()
        self.source("addon_beside_simulator 'C:\\App\\app.exe'")
        self.assertEqual(self.recorded_env()["WINEDEBUG"], "-all")
        Path(f"{self.record}.env").unlink()
        self.source("WINEDEBUG=err+all addon_beside_simulator 'C:\\App\\app.exe'")
        self.assertEqual(self.recorded_env()["WINEDEBUG"], "err+all")

    def test_the_prefix_path_drops_the_variable_that_breaks_wine(self):
        self.fake_proton()
        self.source("addon_beside_simulator 'C:\\App\\app.exe'", NoDefaultCurrentDirectoryInExePath="1")
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", self.recorded_env())

    def test_an_installer_runs_alone_with_the_assignments_and_the_exit_code_comes_back(self):
        self.fake_proton(wine_exit=7)
        result = self.source("code=0\naddon_alone WINEDLLOVERRIDES=d3d9=d /tmp/Setup.exe /S || code=$?\necho \"code=$code\"")
        self.assertEqual(result.stdout.strip(), "code=7")
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine /tmp/Setup.exe /S"])
        self.assertEqual(self.recorded_env()["WINEDLLOVERRIDES"], "d3d9=d")
        self.assertEqual(Path(f"{self.record}.server").read_text().splitlines(), [f"{self.proton}/files/bin/wineserver -w"])

    def test_the_installer_waits_for_the_wineserver_of_the_prefix(self):
        self.fake_proton()
        self.source("addon_alone /tmp/Setup.exe")
        env = dict(line.split("=", 1) for line in Path(f"{self.record}.server.env").read_text().splitlines() if "=" in line)
        self.assertEqual(env["WINEPREFIX"], str(self.prefix))

    def test_programs_left_open_after_the_installer_are_said(self):
        self.fake_proton(server_exit=1)
        result = self.source("addon_alone /tmp/Setup.exe")
        self.assertEqual(result.returncode, 0)
        self.assertIn("still open in the prefix", result.stderr)

    def test_a_script_that_asks_for_the_log_gets_a_private_file_under_the_cache(self):
        result = self.source('file="$(addon_log_file demo)"\necho "line" >> "$file"\ncat "$file"\nfile="$(addon_log_file demo)"\ncat "$file"\necho "$file"')
        log = self.root / "cache" / "logs" / "demo.log"
        self.assertEqual(result.stdout.splitlines(), ["line", str(log)])
        self.assertEqual(oct(log.stat().st_mode & 0o777), "0o600")

    def test_a_missing_cache_variable_is_an_error_not_a_log_in_the_current_folder(self):
        result = self.bash(f'. "{TOOLS}/addon-launch.sh"; unset FSOL_CACHE; addon_log_file demo')
        self.assertNotEqual(result.returncode, 0)


class SharedHelpersTest(ShellCase):
    def source(self, body, **env):
        return self.bash(f'set -euo pipefail\n. "{TOOLS}/addon-launch.sh"\n{body}', **env)

    def stubs(self):
        self.token = str(self.root)
        process_stubs(self.bin, self.token)

    def stand_in_that_ignores_termination(self, argv0):
        link = self.root / "stand-in" / "deaf"
        link.parent.mkdir(exist_ok=True)
        if not link.exists():
            link.symlink_to(shutil.which("sleep"))
        process = subprocess.Popen([argv0, "30"], executable=str(link), env={**os.environ, "FSOL_TEST_TOKEN": self.token}, preexec_fn=lambda: signal.signal(signal.SIGTERM, signal.SIG_IGN))
        self.processes.append(process)
        return process

    def test_the_newest_match_is_the_highest_version_and_a_miss_is_empty_and_not_an_error(self):
        for name in ("app-1.9.0", "app-1.10.0", "app-1.2.0"):
            (self.root / "apps" / name).mkdir(parents=True)
        result = self.source(f'addon_newest "{self.root}/apps/app-*"\necho "none=$(addon_newest "{self.root}/nowhere/*")"\necho done')
        self.assertEqual(result.stdout.splitlines(), [f"{self.root}/apps/app-1.10.0", "none=", "done"])

    def test_a_match_with_spaces_in_its_path_comes_back_whole(self):
        target = self.root / "Program Files" / "Some App" / "app.exe"
        target.parent.mkdir(parents=True)
        target.write_text("")
        result = self.source(f'addon_newest "{self.root}/Program Files/*/app.exe"')
        self.assertEqual(result.stdout.strip(), str(target))

    def test_closing_a_program_that_is_not_running_says_so_and_succeeds(self):
        self.stubs()
        result = self.source("addon_close_by_pattern 'the Demo Hub' '^Q:.*\\\\NoSuchHub\\.exe'\necho carried-on")
        self.assertEqual((result.returncode, result.stdout.splitlines()), (0, ["the Demo Hub is not running", "carried-on"]))

    def test_closing_a_running_program_ends_it_with_termination_and_waits_for_it(self):
        self.stubs()
        process = start_stand_in(self.root, self.token, "Q:\\pilot\\Hub\\Hub.exe")
        self.processes.append(process)
        time.sleep(0.2)
        result = self.source("addon_close_by_pattern 'the Demo Hub' '^Q:.*\\\\Hub\\.exe'")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(process.wait(5), -signal.SIGTERM)

    def test_a_program_that_stays_after_the_request_is_reported_with_the_add_on_id_and_never_killed(self):
        self.stubs()
        process = self.stand_in_that_ignores_termination("Q:\\pilot\\Hub\\Hub.exe")
        time.sleep(0.2)
        result = self.source("addon_close_by_pattern 'the Demo Hub' '^Q:.*\\\\Hub\\.exe'", FSOL_CLOSE_WAIT="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("demo: the Demo Hub is still running after the request to end it", result.stderr)
        self.assertIsNone(process.poll())

    def machine_json(self, text):
        script(self.cli, f"#!/bin/sh\ncat <<'JSON'\n{text}\nJSON\n")

    def test_a_busy_prefix_names_the_programs_open_in_it_and_stops_the_script(self):
        self.machine_json('{"prefix_in_use": true, "prefix_programs": ["PMDG OC3.exe", "Hub.exe"]}')
        result = self.source("addon_prefix_free_or_say 'demo'\necho carried-on")
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("close PMDG OC3.exe, Hub.exe first; opened alone, it needs the simulator's prefix to itself", result.stderr)

    def test_a_busy_prefix_with_no_known_program_says_to_wait(self):
        self.machine_json('{"prefix_in_use": true, "prefix_programs": []}')
        result = self.source("addon_prefix_free_or_say 'demo'")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Wine is still running in the prefix; wait a few seconds and try again", result.stderr)

    def test_a_free_prefix_lets_the_script_carry_on(self):
        self.machine_json('{"prefix_in_use": false, "prefix_programs": []}')
        result = self.source("addon_prefix_free_or_say 'demo'\necho carried-on")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "carried-on"))

    def test_a_machine_report_that_cannot_be_read_lets_the_script_carry_on(self):
        script(self.cli, "#!/bin/sh\nexit 1\n")
        result = self.source("addon_prefix_free_or_say 'demo'\necho carried-on")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "carried-on"))

    def test_a_stashed_file_comes_back_as_it_was_and_the_backup_goes(self):
        target = self.root / "folder" / "File.dll"
        target.parent.mkdir()
        target.write_text("original")
        result = self.source(f'addon_stash "{target}" "{self.root}/backup/deep"\necho changed > "{target}"\naddon_unstash "{target}" "{self.root}/backup/deep"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_text(), "original")
        self.assertEqual(sorted(path.name for path in (self.root / "backup/deep").iterdir()), [])

    def test_the_backup_is_named_after_the_file_inside_the_folder_it_is_given(self):
        target = self.root / "folder" / "File.dll"
        target.parent.mkdir()
        target.write_text("original")
        self.source(f'addon_stash "{target}" "{self.root}/backup"')
        self.assertEqual((self.root / "backup/File.dll").read_text(), "original")

    def test_a_file_that_was_not_there_is_removed_again_by_the_undo(self):
        target = self.root / "folder" / "New.dll"
        target.parent.mkdir()
        result = self.source(f'addon_stash "{target}" "{self.root}/backup"\necho ours > "{target}"\ntest -e "{self.root}/backup/New.dll.absent"\naddon_unstash "{target}" "{self.root}/backup"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(target.exists())
        self.assertEqual(sorted(path.name for path in (self.root / "backup").iterdir()), [])

    def test_stashing_twice_keeps_the_first_backup(self):
        target = self.root / "folder" / "File.dll"
        target.parent.mkdir()
        target.write_text("first")
        self.source(f'addon_stash "{target}" "{self.root}/backup"\necho second > "{target}"\naddon_stash "{target}" "{self.root}/backup"')
        self.assertEqual((self.root / "backup/File.dll").read_text(), "first")

    def test_undoing_what_was_never_stashed_changes_nothing(self):
        target = self.root / "folder" / "File.dll"
        target.parent.mkdir()
        target.write_text("kept")
        result = self.source(f'addon_unstash "{target}" "{self.root}/backup"')
        self.assertEqual((result.returncode, target.read_text()), (0, "kept"))


class PrefixHelpersTest(ShellCase):
    def source(self, body, **env):
        return self.bash(f'set -euo pipefail\n. "{TOOLS}/addons-prefix.sh"\n{body}', **env)

    def sim_prefix(self):
        for name in ("system.reg", "user.reg", "userdef.reg"):
            (self.prefix / name).write_text(f"{name} of the simulator\n")
        (self.prefix / "dosdevices").mkdir()
        os.symlink("../drive_c", self.prefix / "dosdevices" / "c:")
        os.symlink("/", self.prefix / "dosdevices" / "z:")
        os.symlink("/games/steam", self.prefix / "dosdevices" / "s:")
        os.symlink("/media/files", self.prefix / "dosdevices" / "f:")

    def sibling(self):
        return self.root / "data" / "flightsim-on-linux" / "addons-prefix"

    def server_folder(self, prefix):
        info = os.stat(prefix)
        folder = Path(f"/tmp/.wine-{os.getuid()}/server-{info.st_dev:x}-{info.st_ino:x}")
        folder.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, folder, True)
        return folder

    def test_the_prefix_lives_under_the_data_home_of_the_tool(self):
        self.assertEqual(self.source("addon_sibling_prefix").stdout.strip(), str(self.sibling()))

    def test_without_xdg_data_home_it_is_under_local_share(self):
        env = {key: value for key, value in self.env.items() if key != "XDG_DATA_HOME"}
        result = subprocess.run(["bash", "-c", f'. "{TOOLS}/addons-prefix.sh"; addon_sibling_prefix'], env=env, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), f"{self.home}/.local/share/flightsim-on-linux/addons-prefix")

    def test_creating_copies_the_registry_blocks_updates_and_shares_drives(self):
        self.sim_prefix()
        result = self.source("addon_sibling_prefix_create")
        self.assertEqual(result.returncode, 0, result.stderr)
        sibling = self.sibling()
        self.assertEqual((sibling / "system.reg").read_text(), "system.reg of the simulator\n")
        self.assertEqual((sibling / ".update-timestamp").read_text(), "disable\n")
        self.assertEqual(os.readlink(sibling / "drive_c"), str(self.prefix / "drive_c"))
        links = {name: os.readlink(sibling / "dosdevices" / name) for name in ("c:", "z:", "s:", "f:")}
        self.assertEqual(links, {"c:": "../drive_c", "z:": "/", "s:": "/games/steam", "f:": "/media/files"})

    def test_creating_again_keeps_the_registry_the_add_ons_changed(self):
        self.sim_prefix()
        self.source("addon_sibling_prefix_create")
        (self.sibling() / "user.reg").write_text("changed by an add-on\n")
        result = self.source("addon_sibling_prefix_create")
        self.assertEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)
        self.assertEqual((self.sibling() / "user.reg").read_text(), "changed by an add-on\n")

    def test_a_reset_copies_the_registry_again(self):
        self.sim_prefix()
        self.source("addon_sibling_prefix_create")
        (self.sibling() / "user.reg").write_text("changed by an add-on\n")
        self.source("addon_sibling_prefix_create", FSOL_ADDONS_PREFIX_RESET="1")
        self.assertEqual((self.sibling() / "user.reg").read_text(), "user.reg of the simulator\n")

    def test_a_wineserver_holding_the_simulator_prefix_refuses_the_copy(self):
        self.sim_prefix()
        self.stand_in("wineserver", "wineserver", self.server_folder(self.prefix))
        time.sleep(0.2)
        result = self.source("addon_sibling_prefix_create")
        self.assertEqual(result.returncode, 1)
        self.assertIn("close the simulator", result.stderr)
        self.assertFalse((self.sibling() / "system.reg").exists())

    def test_a_wineserver_holding_the_add_ons_prefix_refuses_the_copy(self):
        self.sim_prefix()
        self.sibling().mkdir(parents=True)
        self.stand_in("wineserver", "wineserver", self.server_folder(self.sibling()))
        time.sleep(0.2)
        result = self.source("addon_sibling_prefix_create")
        self.assertEqual(result.returncode, 1)
        self.assertIn("close the programs of the add-ons prefix", result.stderr)

    def test_a_wineserver_of_another_prefix_does_not_refuse(self):
        self.sim_prefix()
        other = self.root / "other"
        other.mkdir()
        self.stand_in("wineserver", "wineserver", self.server_folder(other))
        time.sleep(0.2)
        self.assertEqual(self.source("addon_sibling_prefix_create").returncode, 0)

    def test_a_missing_system_registry_is_an_error(self):
        result = self.source("addon_sibling_prefix_create")
        self.assertEqual(result.returncode, 1)
        self.assertIn("system.reg is missing", result.stderr)

    def test_wine_runs_with_the_x11_driver_the_dxvk_overrides_and_no_wine_breaking_variable(self):
        self.fake_proton()
        result = self.source("EXTRA_WINEDLLOVERRIDES='powershell.exe=d' addon_sibling_prefix_wine 'C:\\App\\app.exe' --flag", NoDefaultCurrentDirectoryInExePath="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine C:\\App\\app.exe --flag"])
        env = self.recorded_env()
        overrides = env["WINEDLLOVERRIDES"].split(";")
        for expected in ("d3d11=n", "d3d9=n", "dxgi=n", "d3d12core=n", "winex11.drv=b", "winewayland.drv=d", "winemenubuilder.exe=d", "powershell.exe=d"):
            self.assertIn(expected, overrides)
        self.assertEqual(env["WINEPREFIX"], str(self.sibling()))
        self.assertEqual(env["WINEESYNC"], "1")
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", env)
        self.assertNotIn("WAYLAND_DISPLAY", env)


class ShimHelperTest(ShellCase):
    def setUp(self):
        super().setUp()
        self.tree = self.root / "tree"
        (self.tree / "tools").mkdir(parents=True)
        shutil.copy(TOOLS / "shim.sh", self.tree / "tools" / "shim.sh")
        self.builds = self.root / "builds"
        script(self.tree / "shims" / "demo" / "build.sh", f'#!/usr/bin/env bash\nset -eu\necho built >> "{self.builds}"\nmkdir -p "$1"\necho dll > "$1/demo.dll"\n')
        (self.tree / "shims" / "demo" / "demo.c").write_text("int x;\n")
        self.env["FSOL_CACHE"] = str(self.root / "cache")

    def shim(self, body="shim_path demo"):
        return self.bash(f'. "{self.tree}/tools/shim.sh"\n{body}')

    def builds_done(self):
        return self.builds.read_text().count("built") if self.builds.exists() else 0

    def test_a_missing_shim_is_built_and_its_path_printed_alone_on_stdout(self):
        result = self.shim()
        self.assertEqual((result.returncode, result.stdout.strip()), (0, str(self.root / "cache" / "shims" / "demo.dll")))
        self.assertEqual(self.builds_done(), 1)

    def test_a_shim_that_is_up_to_date_is_not_built_again(self):
        self.shim()
        self.shim()
        self.assertEqual(self.builds_done(), 1)

    def test_a_shim_older_than_its_sources_is_built_again(self):
        self.shim()
        built = self.root / "cache" / "shims" / "demo.dll"
        old = time.time() - 100
        os.utime(built, (old, old))
        self.shim()
        self.assertEqual(self.builds_done(), 2)

    def test_a_shim_with_a_different_file_name_is_named_as_the_second_argument(self):
        script(self.tree / "shims" / "two" / "build.sh", '#!/usr/bin/env bash\nmkdir -p "$1"\necho dll > "$1/other.dll"\n')
        result = self.shim("shim_path two other.dll")
        self.assertEqual(result.stdout.strip(), str(self.root / "cache" / "shims" / "other.dll"))

    def test_an_unknown_shim_is_an_error(self):
        result = self.shim("shim_path nothing")
        self.assertEqual(result.returncode, 1)
        self.assertIn("shim nothing does not exist", result.stderr)

    def test_a_build_that_fails_fails_the_call_and_prints_no_path(self):
        script(self.tree / "shims" / "bad" / "build.sh", "#!/usr/bin/env bash\necho 'no compiler' >&2\nexit 1\n")
        result = self.shim("shim_path bad")
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("no compiler", result.stderr)

    def test_the_build_output_goes_to_stderr_so_the_path_stays_clean(self):
        script(self.tree / "shims" / "loud" / "build.sh", '#!/usr/bin/env bash\necho "compiling"\nmkdir -p "$1"\necho dll > "$1/loud.dll"\n')
        result = self.shim("shim_path loud")
        self.assertEqual(result.stdout.strip(), str(self.root / "cache" / "shims" / "loud.dll"))
        self.assertIn("compiling", result.stderr)


@unittest.skipUnless(shutil.which("x86_64-w64-mingw32-gcc"), "needs mingw")
class ToastStubShimTest(ShellCase):
    def test_the_first_shim_builds_through_shim_path_and_exports_the_activation_factory(self):
        result = self.bash(f'. "{TOOLS}/shim.sh"\nshim_path toaststub')
        dll = self.root / "cache" / "shims" / "toaststub.dll"
        self.assertEqual((result.returncode, result.stdout.strip()), (0, str(dll)))
        exports = subprocess.run(["x86_64-w64-mingw32-objdump", "-p", str(dll)], capture_output=True, text=True).stdout
        self.assertIn("DllGetActivationFactory", exports)

    def test_build_windows_puts_the_shim_in_its_folder_with_a_checksum(self):
        tree = self.root / "tree"
        (tree / "tools").mkdir(parents=True)
        (tree / "checks").mkdir()
        shutil.copy(TOOLS / "build-windows.sh", tree / "tools" / "build-windows.sh")
        shutil.copy(TOOLS / "pack-shims.sh", tree / "tools" / "pack-shims.sh")
        shutil.copytree(ROOT / "shims", tree / "shims")
        script(tree / "checks" / "build-checks.sh", '#!/usr/bin/env bash\nmkdir -p "$1"\n')
        out = self.root / "out"
        result = subprocess.run([str(tree / "tools" / "build-windows.sh"), str(out)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((out / "shims" / "toaststub.dll").is_file())
        self.assertIn("toaststub.dll", (out / "shims" / "SHA256SUMS").read_text())

    def test_without_the_compiler_the_build_says_which_package_to_install_in_one_line(self):
        bare = self.root / "bare"
        bare.mkdir()
        for name in ("dirname", "readlink", "mkdir"):
            os.symlink(shutil.which(name), bare / name)
        result = subprocess.run([shutil.which("bash"), str(ROOT / "shims" / "toaststub" / "build.sh"), str(self.root / "out")], env={"PATH": str(bare)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1)
        self.assertIn("mingw-w64-gcc", result.stderr)


if __name__ == "__main__":
    unittest.main()
