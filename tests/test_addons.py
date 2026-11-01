import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fsol import addons, cli, desktop, watcher
from tests.test_machine import FakeMachine, write

APPLY = '#!/bin/sh\necho "writing the fix"\ntouch "$FSOL_PREFIX/fixed"\n'
CHECK = '#!/bin/sh\nif [ -e "$FSOL_PREFIX/fixed" ]; then echo "ok   fix in place"; else echo "FAIL fix missing"; exit 1; fi\n'
UNDO = '#!/bin/sh\nrm -f "$FSOL_PREFIX/fixed"\necho "removed"\n'
SPEC = {
    "name": "Demo Add-on",
    "tested_version": "1.2.3",
    "tested_on": "2026-10-17",
    "needs": ["simulator-closed"],
    "touches_addon_files": False,
    "installed": {"community": ["demo-addon-*"]},
}


class AddonTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.fake = FakeMachine(root).build()
        self.root = root / "addons"
        self.state = root / "state"
        environ = mock.patch.dict(os.environ, {"FSOL_ADDONS": str(self.root), "XDG_STATE_HOME": str(self.state)})
        environ.start()
        self.addCleanup(environ.stop)

    def tearDown(self):
        self.temp.cleanup()

    def addon(self, name="demo", spec=None, apply=APPLY, check=CHECK, undo=UNDO):
        folder = self.root / name
        write(folder / "addon.json", json.dumps(SPEC if spec is None else spec))
        for action, text in (("apply", apply), ("check", check), ("undo", undo)):
            if text is not None:
                write(folder / action, text)
                (folder / action).chmod(0o755)
        return folder

    def install(self):
        (self.fake.packages / "Community" / "demo-addon-v1").mkdir()

    def cli(self, *args):
        output = io.StringIO()
        with mock.patch.object(cli.machine, "discover", return_value=self.fake.discover()), contextlib.redirect_stdout(output):
            code = cli.main(list(args))
        return code, output.getvalue()


class LoadTest(AddonTestCase):
    def test_a_valid_folder_loads_with_its_folder_name_as_id(self):
        addon = addons.load(self.addon())
        self.assertEqual((addon.id, addon.name, addon.tested_version), ("demo", "Demo Add-on", "1.2.3"))

    def test_every_problem_is_named(self):
        spec = dict(SPEC, tested_on="17/10/2026", needs=["coffee"], installed={"community": ["/etc/passwd"]}, extra=1)
        with self.assertRaises(addons.AddonError) as raised:
            addons.load(self.addon(spec=spec, undo=None))
        message = str(raised.exception)
        for expected in ("tested_on must be a date", "unknown need 'coffee'", "'/etc/passwd' must be a relative glob", "undo is missing", "unknown field 'extra'"):
            self.assertIn(expected, message)

    def test_a_script_that_is_not_executable_is_refused(self):
        folder = self.addon()
        (folder / "check").chmod(0o644)
        with self.assertRaisesRegex(addons.AddonError, "check is missing or not executable"):
            addons.load(folder)

    def test_a_path_that_climbs_out_of_its_place_is_refused(self):
        with self.assertRaisesRegex(addons.AddonError, "relative glob"):
            addons.load(self.addon(spec=dict(SPEC, installed={"prefix": ["../other/pfx"]})))

    def test_an_empty_installed_list_is_refused(self):
        with self.assertRaisesRegex(addons.AddonError, "at least one path"):
            addons.load(self.addon(spec=dict(SPEC, installed={"prefix": []})))


class StatusTest(AddonTestCase):
    def test_an_addon_missing_from_community_is_not_installed(self):
        self.addon()
        code, output = self.cli("addon", "list")
        self.assertEqual(code, 0)
        self.assertIn("not installed", output)

    def test_list_as_json_says_installed_and_not_fixed(self):
        self.addon()
        self.install()
        _, output = self.cli("addon", "list", "--json")
        row = json.loads(output)[0]
        self.assertEqual((row["id"], row["status"], row["installed"], row["fixed_on"]), ("demo", "installed, not fixed", True, None))


class ApplyTest(AddonTestCase):
    def test_apply_runs_the_fix_then_its_check_and_remembers_the_day(self):
        self.addon()
        self.install()
        code, output = self.cli("addon", "apply", "demo")
        self.assertEqual(code, 0)
        self.assertEqual(output.splitlines(), ["Demo Add-on", "  writing the fix", "  ok   fix in place"])
        self.assertTrue((self.fake.prefix / "fixed").exists())
        _, listing = self.cli("addon", "list")
        self.assertIn(f"fixed on {date.today().isoformat()}", listing)

    def test_a_fix_whose_check_fails_is_not_remembered(self):
        self.addon(apply="#!/bin/sh\necho 'did nothing'\n")
        code, output = self.cli("addon", "apply", "demo")
        self.assertEqual(code, 1)
        self.assertIn("FAIL the fix ran, but its check did not pass", output)
        self.assertFalse((self.state / "flightsim-on-linux/addons/demo.json").exists())

    def test_a_second_apply_whose_check_fails_forgets_the_first(self):
        folder = self.addon()
        self.install()
        self.cli("addon", "apply", "demo")
        (folder / "apply").write_text("#!/bin/sh\nrm -f \"$FSOL_PREFIX/fixed\"\n")
        self.cli("addon", "apply", "demo")
        _, listing = self.cli("addon", "list")
        self.assertIn("installed, not fixed", listing)

    def test_a_failing_fix_stops_before_the_check(self):
        self.addon(apply="#!/bin/sh\nexit 3\n")
        code, output = self.cli("addon", "apply", "demo")
        self.assertEqual(code, 1)
        self.assertIn("FAIL the fix stopped with exit code 3", output)
        self.assertNotIn("fix missing", output)

    def test_an_addon_that_needs_the_simulator_closed_waits_for_it(self):
        self.addon()
        self.fake.run_sim()
        code, output = self.cli("addon", "apply", "demo")
        self.assertEqual(code, 1)
        self.assertIn("close the simulator", output)
        self.assertFalse((self.fake.prefix / "fixed").exists())

    def test_an_addon_that_needs_proton_fs2024_refuses_another_proton(self):
        (self.fake.tool / "user_settings.py").write_text("user_settings = {}\n")
        self.addon(spec=dict(SPEC, needs=["proton-fs2024"]))
        code, output = self.cli("addon", "apply", "demo")
        self.assertEqual(code, 1)
        self.assertIn("Proton FS2024", output)

    def test_a_fix_that_touches_addon_files_says_so_and_how_to_undo(self):
        self.addon(spec=dict(SPEC, touches_addon_files=True))
        _, output = self.cli("addon", "apply", "demo")
        self.assertIn("changes files of the add-on itself; flightsim-on-linux addon undo demo restores them", output)

    def test_scripts_get_the_machine_in_fsol_variables(self):
        self.addon(apply='#!/bin/sh\necho "$FSOL_PREFIX|$FSOL_COMMUNITY|$FSOL_BASE|$FSOL_PROTON|$FSOL_SIM_RUNNING|$FSOL_ADDON"\ntouch "$FSOL_PREFIX/fixed"\n')
        _, output = self.cli("addon", "apply", "demo")
        found = self.fake.discover()
        self.assertIn(f"  {found.prefix}|{found.community}|{found.base}|{found.proton}|0|demo", output)

    def test_scripts_get_the_cache_folder_from_fsol_cache_or_the_xdg_cache_home(self):
        self.addon(apply='#!/bin/sh\necho "cache=$FSOL_CACHE"\ntouch "$FSOL_PREFIX/fixed"\n')
        with mock.patch.dict(os.environ, {"FSOL_CACHE": "/somewhere/cache", "XDG_CACHE_HOME": "/xdg"}):
            _, own = self.cli("addon", "apply", "demo")
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": "/xdg"}):
            os.environ.pop("FSOL_CACHE", None)
            _, xdg = self.cli("addon", "apply", "demo")
        with mock.patch.dict(os.environ, {"HOME": "/home/someone"}):
            os.environ.pop("FSOL_CACHE", None)
            os.environ.pop("XDG_CACHE_HOME", None)
            _, home = self.cli("addon", "apply", "demo")
        self.assertIn("  cache=/somewhere/cache", own)
        self.assertIn("  cache=/xdg/flightsim-on-linux", xdg)
        self.assertIn("  cache=/home/someone/.cache/flightsim-on-linux", home)

    def test_an_unknown_addon_is_an_error(self):
        code, _ = self.cli("addon", "apply", "nothing")
        self.assertEqual(code, 2)


class CheckAndUndoTest(AddonTestCase):
    def test_check_without_names_checks_only_installed_addons(self):
        self.addon("demo")
        self.addon("other", spec=dict(SPEC, name="Other", installed={"community": ["other-*"]}))
        self.install()
        code, output = self.cli("addon", "check")
        self.assertEqual(code, 1)
        self.assertIn("Demo Add-on", output)
        self.assertNotIn("Other", output)

    def test_a_failing_check_forgets_an_earlier_fix(self):
        self.addon()
        self.install()
        self.cli("addon", "apply", "demo")
        (self.fake.prefix / "fixed").unlink()
        self.cli("addon", "check", "demo")
        _, listing = self.cli("addon", "list")
        self.assertIn("installed, not fixed", listing)

    def test_undo_runs_the_undo_script_and_forgets_the_fix(self):
        self.addon()
        self.install()
        self.cli("addon", "apply", "demo")
        code, output = self.cli("addon", "undo", "demo")
        self.assertEqual(code, 0)
        self.assertIn("  removed", output)
        self.assertFalse((self.fake.prefix / "fixed").exists())
        _, listing = self.cli("addon", "list")
        self.assertIn("installed, not fixed", listing)


INSTALL = '#!/bin/sh\necho "installer=$FSOL_INSTALLER"\necho "env=${NoDefaultCurrentDirectoryInExePath-unset}"\ntouch "$FSOL_PREFIX/installed"\n'
LAUNCH = '#!/bin/sh\necho "$FSOL_ADDON|$FSOL_SIM_RUNNING|${NoDefaultCurrentDirectoryInExePath-unset}|$*" > "$FSOL_PREFIX/launched"\n'
FULL = dict(
    SPEC,
    install={"from": "https://example.org/demo", "paid": False, "kind": "installer"},
    shortcuts=[{"title": "Demo", "script": "launch", "args": ["--minimized"], "close": "close", "mime": ["x-scheme-handler/demo"], "icon": {"place": "prefix", "glob": "drive_c/demo.exe"}}],
    watcher={"app": r"^C:.*\\Demo\.exe", "args": ["-autostart"], "grace": 20, "poll": 8, "stop": "with_simulator", "wait_for": {"process": "couatl", "timeout": 300}, "health": {"listening": 8651}},
)


class RecordingTools(desktop.Tools):
    def __init__(self):
        self.calls = []

    def which(self, name):
        return None if name in ("7z", "magick") else f"/usr/bin/{name}"

    def run(self, argv):
        self.calls.append(argv)
        return 0


class OptionalCase(AddonTestCase):
    def setUp(self):
        super().setUp()
        root = Path(self.temp.name)
        self.data = root / "data"
        self.config = root / "config"
        environ = mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.data), "XDG_CONFIG_HOME": str(self.config), "NoDefaultCurrentDirectoryInExePath": "1"})
        environ.start()
        self.addCleanup(environ.stop)
        self.tools = RecordingTools()
        self.systemctl = []
        for patcher in (
            mock.patch.object(desktop, "Tools", return_value=self.tools),
            mock.patch.object(watcher, "systemctl_user", side_effect=lambda *args: self.systemctl.append(args) or 0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def full(self, spec=FULL, install=INSTALL, launch=LAUNCH, extra=("close", "restart")):
        folder = self.addon(spec=spec)
        for name, text in (("install", install), ("launch", launch), *((name, "#!/bin/sh\n") for name in extra)):
            if text is not None:
                write(folder / name, text)
                (folder / name).chmod(0o755)
        return folder

    def cli_all(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.machine, "discover", return_value=self.fake.discover()), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()


class OptionalFieldsTest(OptionalCase):
    def test_an_addon_without_the_new_fields_loads_with_defaults(self):
        addon = addons.load(self.addon())
        self.assertEqual((addon.install, addon.shortcuts, addon.watcher, addon.can_install), (None, [], None, False))

    def test_a_complete_addon_loads(self):
        addon = addons.load(self.full())
        self.assertEqual((addon.install["kind"], addon.shortcuts[0]["title"], addon.watcher["grace"], addon.can_install, addon.install_needs_file), ("installer", "Demo", 20, True, True))

    def test_native_and_manual_installs_need_no_file(self):
        for kind, needs in (("installer", True), ("store", True), ("native", False), ("manual", False)):
            spec = dict(SPEC, install={"from": "somewhere", "paid": None, "kind": kind})
            self.assertEqual(addons.load(self.addon(spec=spec)).install_needs_file, needs, kind)

    def test_every_problem_in_the_new_fields_is_named(self):
        spec = dict(
            SPEC,
            install={"from": "", "kind": "magic", "paid": "yes", "extra": 1},
            shortcuts=[{"title": "", "script": "ghost", "args": "x", "icon": {"place": "moon", "glob": "/etc"}, "close": "gone", "mime": [1], "extra": 1}, "text"],
            watcher={"app": "(", "script": "ghost", "args": [1], "grace": -1, "poll": 0, "stop": "sometimes", "trigger": "moon", "wait_for": {"process": "x"}, "health": {"listening": 99999}, "extra": 1},
        )
        with self.assertRaises(addons.AddonError) as raised:
            addons.load(self.addon(spec=spec))
        message = str(raised.exception)
        for expected in (
            "install.from must say where", "install.kind must be one of", "install.paid must be true, false or null", "unknown install field 'extra'",
            "shortcuts[0].title must be a one-line string", "shortcuts[0].script 'ghost' is missing or not executable", "shortcuts[0].args must be a list of strings",
            "shortcuts[0].icon must be", "shortcuts[0].close 'gone' is missing or not executable", "shortcuts[0].mime must be a list of strings", "unknown field 'extra' in shortcuts[0]",
            "shortcuts[1] must be an object",
            "watcher.app must be a regex", "watcher.script 'ghost' is missing or not executable", "watcher.args must be a list of strings", "watcher.grace must be a number",
            "watcher.poll must be a number", "watcher.stop must be one of", "watcher.trigger must be one of", "watcher.wait_for must be", "watcher.health.listening must be a port number",
            "unknown field 'extra' in watcher",
        ):
            self.assertIn(expected, message)

    def test_a_wrong_type_for_a_whole_field_is_named(self):
        spec = dict(SPEC, install=[], shortcuts={}, watcher="always")
        with self.assertRaises(addons.AddonError) as raised:
            addons.load(self.addon(spec=spec))
        for expected in ("install must be a dict", "shortcuts must be a list", "watcher must be a dict"):
            self.assertIn(expected, str(raised.exception))

    def test_a_shortcut_naming_a_script_that_is_not_executable_is_refused(self):
        folder = self.full()
        (folder / "launch").chmod(0o644)
        with self.assertRaisesRegex(addons.AddonError, "shortcuts\\[0\\].script 'launch' is missing or not executable"):
            addons.load(folder)

    def test_a_watcher_naming_a_script_that_is_not_executable_is_refused(self):
        folder = self.full()
        (folder / "launch").chmod(0o644)
        with self.assertRaisesRegex(addons.AddonError, "watcher.script 'launch' is missing or not executable"):
            addons.load(folder)

    def test_a_loop_watcher_names_one_script_and_nothing_else(self):
        addon = addons.load(self.full(spec=dict(SPEC, watcher={"loop": "launch"})))
        self.assertEqual(addon.watcher, {"loop": "launch"})

    def test_a_loop_watcher_refuses_every_other_option(self):
        with self.assertRaisesRegex(addons.AddonError, "watcher.loop takes no other option, but app, poll is set"):
            addons.load(self.full(spec=dict(SPEC, watcher={"loop": "launch", "app": "x", "poll": 5})))

    def test_a_loop_watcher_needs_a_script_that_exists_in_the_folder(self):
        for loop, message in (("ghost", "watcher.loop 'ghost' is missing or not executable"), ("../apply", "watcher.loop '../apply' is not a plain script name"), (3, "watcher.loop 3 is not a plain script name")):
            with self.assertRaisesRegex(addons.AddonError, message):
                addons.load(self.full(spec=dict(SPEC, watcher={"loop": loop})))

    def test_a_script_name_cannot_climb_out_of_the_folder(self):
        spec = dict(FULL, shortcuts=[{"title": "Demo", "script": "../apply"}])
        with self.assertRaisesRegex(addons.AddonError, "is not a plain script name"):
            addons.load(self.full(spec=spec))

    def test_an_install_launch_or_close_script_that_is_not_executable_is_refused(self):
        for name in ("install", "launch", "close"):
            folder = self.full(spec=dict(SPEC), extra=("close",))
            (folder / name).chmod(0o644)
            with self.assertRaisesRegex(addons.AddonError, f"{name} is present but not executable"):
                addons.load(folder)

    def test_the_connected_health_option_needs_a_wait_and_a_restart_script(self):
        health = {"connected_to": 5111, "restart": "ghost"}
        with self.assertRaises(addons.AddonError) as raised:
            addons.load(self.full(spec=dict(SPEC, watcher={"app": "x", "trigger": "none", "health": health})))
        self.assertIn("watcher.health.lost_for must be a number", str(raised.exception))
        self.assertIn("watcher.health.restart 'ghost' is missing or not executable", str(raised.exception))

    def test_health_takes_one_option_only(self):
        for health in ({}, {"listening": 80, "connected_to": 81, "lost_for": 5, "restart": "launch"}):
            with self.assertRaisesRegex(addons.AddonError, "either listening or connected_to"):
                addons.load(self.full(spec=dict(SPEC, watcher={"app": "x", "health": health})))

    def test_on_other_aircraft_needs_an_aircraft_trigger(self):
        with self.assertRaisesRegex(addons.AddonError, "on_other_aircraft needs an aircraft trigger"):
            addons.load(self.full(spec=dict(SPEC, watcher={"app": "x", "stop": "on_other_aircraft"})))

    def test_an_aircraft_trigger_and_the_none_trigger_are_valid(self):
        for trigger in ({"aircraft": "maddog"}, "none", "simulator"):
            addons.load(self.full(spec=dict(SPEC, watcher={"app": "x", "trigger": trigger})))

    def test_a_trigger_regex_that_does_not_compile_is_refused(self):
        with self.assertRaisesRegex(addons.AddonError, "watcher.trigger must be one of"):
            addons.load(self.full(spec=dict(SPEC, watcher={"app": "x", "trigger": {"aircraft": "("}})))


class ListMarksTest(OptionalCase):
    def test_the_text_columns_stay_the_same_and_carry_no_mark_when_nothing_is_set_up(self):
        self.addon()
        _, output = self.cli("addon", "list")
        self.assertEqual(output, f"{'demo':24} {'Demo Add-on':32} not installed\n")

    def test_json_rows_say_what_can_be_installed_and_what_is_written(self):
        self.full()
        _, output = self.cli("addon", "list", "--json")
        row = json.loads(output)[0]
        self.assertEqual((row["can_install"], row["shortcut"], row["watcher"]), (True, False, False))

    def test_marks_appear_only_for_what_is_true(self):
        self.full()
        _, before = self.cli("addon", "list")
        self.assertTrue(before.rstrip().endswith("not installed  [install]"), before)
        self.cli("addon", "shortcut", "demo")
        self.cli("addon", "watcher", "demo")
        _, after = self.cli("addon", "list")
        self.assertTrue(after.rstrip().endswith("not installed  [install]  [menu]  [watcher]"), after)
        _, as_json = self.cli("addon", "list", "--json")
        row = json.loads(as_json)[0]
        self.assertEqual((row["can_install"], row["shortcut"], row["watcher"]), (True, True, True))

    def test_removing_them_takes_the_marks_off(self):
        self.full()
        self.cli("addon", "shortcut", "demo")
        self.cli("addon", "watcher", "demo")
        self.cli("addon", "shortcut", "demo", "--remove")
        self.cli("addon", "watcher", "demo", "--off")
        _, output = self.cli("addon", "list")
        self.assertTrue(output.rstrip().endswith("not installed  [install]"), output)


class CommunityPlaceTest(OptionalCase):
    def test_a_package_in_community2024_counts_as_installed(self):
        self.full(spec=dict(FULL, installed={"community": ["demo-package/manifest.json"]}))
        addon = addons.find(self.root, "demo")
        with tempfile.TemporaryDirectory() as packages:
            community = Path(packages) / "Community"
            community.mkdir()
            found = SimpleNamespace(prefix=None, base=None, community=community, sim_install=None)
            self.assertFalse(addons.is_installed(addon, found))
            write(Path(packages) / "Community2024" / "demo-package" / "manifest.json", "{}")
            self.assertTrue(addons.is_installed(addon, found))


class DataPlaceTest(OptionalCase):
    def test_an_add_on_with_its_own_prefix_counts_as_installed_from_the_data_folder(self):
        with tempfile.TemporaryDirectory() as data, mock.patch.dict(os.environ, {"XDG_DATA_HOME": data}):
            program = Path(data) / "flightsim-on-linux" / "demo" / "demo.exe"
            self.full(spec=dict(FULL, installed={"data": ["demo/demo.exe"]}))
            addon = addons.find(self.root, "demo")
            found = SimpleNamespace(prefix=None, base=None, community=None, sim_install=None)
            self.assertFalse(addons.is_installed(addon, found))
            write(program, "MZ")
            self.assertTrue(addons.is_installed(addon, found))


class CommandPlaceTest(OptionalCase):
    def test_a_native_program_counts_as_installed_when_it_is_on_the_path(self):
        self.full(spec=dict(FULL, installed={"command": ["sh"]}))
        self.assertTrue(addons.is_installed(addons.find(self.root, "demo"), SimpleNamespace()))

    def test_a_native_program_that_is_not_on_the_path_is_not_installed(self):
        self.full(spec=dict(FULL, installed={"command": ["no-such-program-here"]}))
        self.assertFalse(addons.is_installed(addons.find(self.root, "demo"), SimpleNamespace()))


class InstallTest(OptionalCase):
    def test_without_a_file_it_says_where_to_get_it_and_what_to_run_and_runs_nothing(self):
        self.full()
        code, output = self.cli("addon", "install", "demo")
        self.assertEqual(code, 0)
        self.assertEqual(output.splitlines(), ["Demo Add-on", "  get it from: https://example.org/demo (free)", "  kind: installer", "  then run: flightsim-on-linux addon install demo <the file you downloaded>"])
        self.assertFalse((self.fake.prefix / "installed").exists())

    def test_the_price_is_said_only_when_known(self):
        for paid, text in ((True, "(paid)"), (None, "")):
            self.full(spec=dict(FULL, install={"from": "https://example.org/demo", "paid": paid, "kind": "installer"}))
            _, output = self.cli("addon", "install", "demo")
            self.assertIn(f"  get it from: https://example.org/demo{' ' + text if text else ''}\n", output)

    def test_an_addon_with_no_install_script_just_says_where_to_get_it(self):
        self.full(spec=dict(SPEC, install={"from": "the store", "paid": True, "kind": "store"}), install=None)
        code, output = self.cli("addon", "install", "demo")
        self.assertEqual((code, output.splitlines()[1:]), (0, ["  get it from: the store (paid)", "  kind: store"]))

    def test_a_file_goes_to_the_install_script_as_an_absolute_path_with_the_wine_trap_removed(self):
        self.full()
        setup = Path(self.temp.name) / "downloads" / "Setup.exe"
        write(setup, "MZ")
        previous = os.getcwd()
        os.chdir(setup.parent)
        try:
            code, output = self.cli("addon", "install", "demo", "Setup.exe")
        finally:
            os.chdir(previous)
        self.assertEqual(code, 0)
        self.assertIn(f"  installer={setup.resolve()}", output)
        self.assertIn("  env=unset", output)
        self.assertTrue((self.fake.prefix / "installed").exists())

    def test_the_next_step_after_an_installer_is_apply(self):
        self.full()
        setup = Path(self.temp.name) / "Setup.exe"
        write(setup, "MZ")
        _, output = self.cli("addon", "install", "demo", str(setup))
        self.assertEqual(output.splitlines()[-1], "  next: flightsim-on-linux addon apply demo")

    def test_a_running_simulator_refuses_the_installer(self):
        self.full()
        setup = Path(self.temp.name) / "Setup.exe"
        write(setup, "MZ")
        self.fake.run_sim()
        code, output = self.cli("addon", "install", "demo", str(setup))
        self.assertEqual(code, 1)
        self.assertIn("FAIL Demo Add-on: close the simulator and every program of its prefix first", output)
        self.assertFalse((self.fake.prefix / "installed").exists())

    def test_an_add_on_with_a_prefix_of_its_own_installs_while_the_simulators_prefix_is_busy(self):
        self.full(spec=dict(FULL, install={"from": "https://example.org/demo", "paid": None, "kind": "installer", "own_prefix": True}))
        setup = Path(self.temp.name) / "Setup.exe"
        write(setup, "MZ")
        self.fake.run_wineserver(self.fake.prefix, 0)
        self.fake.run_program(5001, "C:\\users\\steamuser\\AppData\\Local\\TriTriSim\\tritrisim-hub.exe", self.fake.prefix)
        code, output = self.cli("addon", "install", "demo", str(setup))
        self.assertEqual(code, 0, output)
        self.assertTrue((self.fake.prefix / "installed").exists())

    def test_programs_open_in_the_prefix_refuse_the_installer_and_are_named(self):
        self.full()
        setup = Path(self.temp.name) / "Setup.exe"
        write(setup, "MZ")
        self.fake.run_wineserver(self.fake.prefix, 0)
        self.fake.run_program(5001, "C:\\users\\steamuser\\AppData\\Local\\TriTriSim\\tritrisim-hub.exe", self.fake.prefix)
        code, output = self.cli("addon", "install", "demo", str(setup))
        self.assertEqual(code, 1)
        self.assertIn("programs are still open in the simulator's prefix: tritrisim-hub.exe", output)
        self.assertFalse((self.fake.prefix / "installed").exists())

    def test_an_idle_wineserver_alone_is_still_refused_without_naming_programs(self):
        self.full()
        setup = Path(self.temp.name) / "Setup.exe"
        write(setup, "MZ")
        self.fake.run_wineserver(self.fake.prefix, 0)
        code, output = self.cli("addon", "install", "demo", str(setup))
        self.assertEqual(code, 1)
        self.assertIn("programs are still open in the simulator's prefix; close them first", output)

    def test_without_the_file_nothing_is_refused_even_with_the_simulator_running(self):
        self.full()
        self.fake.run_sim()
        code, _ = self.cli("addon", "install", "demo")
        self.assertEqual(code, 0)

    def test_a_failing_installer_is_reported_and_gets_no_next_step(self):
        self.full(install="#!/bin/sh\nexit 4\n")
        setup = Path(self.temp.name) / "Setup.exe"
        write(setup, "MZ")
        code, output = self.cli("addon", "install", "demo", str(setup))
        self.assertEqual(code, 1)
        self.assertIn("FAIL the installer stopped with exit code 4", output)
        self.assertNotIn("next:", output)

    def test_a_native_install_script_runs_without_a_file(self):
        self.full(spec=dict(FULL, install={"from": "pip", "paid": False, "kind": "native"}), install='#!/bin/sh\necho "file=[${FSOL_INSTALLER-none}]"\n')
        code, output = self.cli_all("addon", "install", "demo")[:2]
        self.assertEqual(code, 0)
        self.assertIn("  file=[none]", output)
        self.assertIn("  next: flightsim-on-linux addon apply demo", output)

    def test_bad_requests_give_a_clean_message_and_exit_2(self):
        self.full()
        self.addon("plain")
        write(Path(self.temp.name) / "Setup.exe", "MZ")
        self.assertEqual(self.cli_all("addon", "install", "nothing")[0], 2)
        code, _, err = self.cli_all("addon", "install", "plain")
        self.assertEqual((code, "nothing to install" in err), (2, True))
        self.assertEqual(self.cli_all("addon", "install", "plain", str(Path(self.temp.name) / "Setup.exe"))[0], 2)
        code, _, err = self.cli_all("addon", "install", "demo", str(Path(self.temp.name) / "missing.exe"))
        self.assertEqual((code, "is not a file" in err), (2, True))

    def test_a_file_for_an_addon_with_no_install_script_is_refused(self):
        self.full(spec=dict(SPEC, install={"from": "the store", "paid": True, "kind": "store"}), install=None)
        code, _, err = self.cli_all("addon", "install", "demo", str(Path(self.temp.name) / "Setup.exe"))
        self.assertEqual((code, "has no install script" in err), (2, True))


class LaunchTest(OptionalCase):
    def test_launch_runs_the_default_script_with_the_machine_and_a_clean_environment(self):
        self.full()
        code = self.cli_all("addon", "launch", "demo")[0]
        self.assertEqual(code, 0)
        self.assertEqual((self.fake.prefix / "launched").read_text().strip(), "demo|0|unset|")

    def test_arguments_after_the_double_dash_reach_the_script(self):
        self.full()
        self.cli_all("addon", "launch", "demo", "launch", "--", "--minimized", "two words")
        self.assertEqual((self.fake.prefix / "launched").read_text().strip(), "demo|0|unset|--minimized two words")

    def test_another_script_of_the_addon_can_be_named(self):
        folder = self.full()
        write(folder / "close", '#!/bin/sh\necho closed > "$FSOL_PREFIX/closed"\n')
        self.cli_all("addon", "launch", "demo", "close")
        self.assertEqual((self.fake.prefix / "closed").read_text().strip(), "closed")

    def test_the_exit_code_of_the_script_is_the_exit_code_of_launch(self):
        self.full(launch="#!/bin/sh\nexit 7\n")
        self.assertEqual(self.cli_all("addon", "launch", "demo")[0], 7)

    def test_the_simulator_state_reaches_the_script(self):
        self.full()
        self.fake.run_sim()
        self.cli_all("addon", "launch", "demo")
        self.assertTrue((self.fake.prefix / "launched").read_text().startswith("demo|1|"))

    def test_unknown_addons_and_scripts_give_a_clean_message_and_exit_2(self):
        self.full()
        self.assertEqual(self.cli_all("addon", "launch", "nothing")[0], 2)
        code, _, err = self.cli_all("addon", "launch", "demo", "ghost")
        self.assertEqual((code, "has no script called ghost" in err), (2, True))
        self.assertEqual(self.cli_all("addon", "launch", "demo", "../apply")[0], 2)

    def test_an_addon_without_a_launch_script_is_refused(self):
        self.addon()
        code, _, err = self.cli_all("addon", "launch", "demo")
        self.assertEqual((code, "has no script called launch" in err), (2, True))


class ShortcutCommandTest(OptionalCase):
    def test_shortcut_writes_the_menu_entry_under_the_data_home_and_says_how_to_remove_it(self):
        self.full()
        code, output, _ = self.cli_all("addon", "shortcut", "demo")
        entry = self.data / "applications" / "flightsim-on-linux-demo.desktop"
        self.assertEqual(code, 0)
        self.assertTrue(entry.is_file())
        self.assertIn(f"  wrote {entry}", output)
        self.assertIn("flightsim-on-linux addon shortcut demo --remove takes it out again", output)
        self.assertIn(f'Exec="{cli.CLI}" addon launch demo launch -- "--minimized" %u', entry.read_text())

    def test_shortcut_for_an_addon_that_is_not_installed_says_so(self):
        self.full()
        _, output, _ = self.cli_all("addon", "shortcut", "demo")
        self.assertIn("is not installed yet", output)

    def test_an_icon_that_cannot_be_made_is_one_line_and_not_a_failure(self):
        self.full()
        code, output, _ = self.cli_all("addon", "shortcut", "demo")
        self.assertEqual(code, 0)
        self.assertEqual(len([line for line in output.splitlines() if "no icon for Demo" in line]), 1)

    def test_remove_deletes_what_was_written(self):
        self.full()
        self.cli_all("addon", "shortcut", "demo")
        code, output, _ = self.cli_all("addon", "shortcut", "demo", "--remove")
        self.assertEqual(code, 0)
        self.assertIn("removed", output)
        self.assertEqual(list((self.data / "applications").iterdir()), [])

    def test_remove_with_nothing_written_says_so(self):
        self.full()
        _, output, _ = self.cli_all("addon", "shortcut", "demo", "--remove")
        self.assertIn("no menu entry was written", output)

    def test_several_addons_at_once(self):
        self.full()
        folder = self.root / "other"
        write(folder / "addon.json", json.dumps(dict(FULL, name="Other")))
        for name in ("apply", "check", "undo", "launch", "close", "restart", "install"):
            write(folder / name, "#!/bin/sh\n")
            (folder / name).chmod(0o755)
        code = self.cli_all("addon", "shortcut", "demo", "other")[0]
        self.assertEqual(code, 0)
        self.assertEqual(sorted(path.name for path in (self.data / "applications").iterdir()), ["flightsim-on-linux-demo.desktop", "flightsim-on-linux-other.desktop"])

    def test_unknown_addons_and_addons_without_shortcuts_give_a_clean_message_and_exit_2(self):
        self.addon()
        self.assertEqual(self.cli_all("addon", "shortcut", "nothing")[0], 2)
        code, _, err = self.cli_all("addon", "shortcut", "demo")
        self.assertEqual((code, "demo has no shortcuts" in err), (2, True))
        self.assertFalse(self.data.exists())


class WatcherCommandTest(OptionalCase):
    def test_watcher_writes_the_unit_and_switches_it_on(self):
        self.full()
        code, output, _ = self.cli_all("addon", "watcher", "demo")
        unit = self.config / "systemd" / "user" / "flightsim-on-linux-demo.service"
        self.assertEqual(code, 0)
        self.assertTrue(unit.is_file())
        self.assertIn(f"ExecStart=\"{cli.CLI}\" addon watch demo", unit.read_text())
        self.assertEqual(self.systemctl, [("enable", "--now", "flightsim-on-linux-demo.service")])
        self.assertIn("flightsim-on-linux addon watcher demo --off takes it out again", output)

    def test_a_loop_watcher_writes_a_unit_that_launches_its_script(self):
        self.full(spec=dict(SPEC, watcher={"loop": "launch"}))
        code, _, _ = self.cli_all("addon", "watcher", "demo")
        unit = self.config / "systemd" / "user" / "flightsim-on-linux-demo.service"
        self.assertEqual(code, 0)
        lines = unit.read_text().splitlines()
        for expected in (f'ExecStart="{cli.CLI}" addon launch demo launch', "Restart=on-failure"):
            self.assertIn(expected, lines)
        self.assertNotIn("KillMode=process", lines)
        self.assertEqual(self.systemctl, [("enable", "--now", "flightsim-on-linux-demo.service")])

    def test_watch_refuses_a_loop_watcher_with_a_clean_message_and_exit_2(self):
        self.full(spec=dict(SPEC, watcher={"loop": "launch"}))
        with mock.patch.object(cli.watcher, "run", return_value=0) as run:
            code, _, err = self.cli_all("addon", "watch", "demo")
        self.assertEqual(code, 2)
        self.assertIn("the watcher of demo is its own script, launch", err)
        self.assertIn("`addon watcher demo`", err)
        run.assert_not_called()

    def test_off_disables_it_and_deletes_the_file(self):
        self.full()
        self.cli_all("addon", "watcher", "demo")
        self.systemctl.clear()
        code = self.cli_all("addon", "watcher", "demo", "--off")[0]
        self.assertEqual(code, 0)
        self.assertEqual(self.systemctl, [("disable", "--now", "flightsim-on-linux-demo.service"), ("daemon-reload",)])
        self.assertFalse((self.config / "systemd" / "user" / "flightsim-on-linux-demo.service").exists())

    def test_a_failing_systemctl_fails_the_command(self):
        self.full()
        with mock.patch.object(watcher, "systemctl_user", return_value=1):
            code, output, _ = self.cli_all("addon", "watcher", "demo")
        self.assertEqual(code, 1)
        self.assertIn("FAIL systemctl --user enable --now stopped with exit code 1", output)

    def test_unknown_addons_and_addons_without_a_watcher_give_a_clean_message_and_exit_2(self):
        self.addon()
        self.assertEqual(self.cli_all("addon", "watcher", "nothing")[0], 2)
        code, _, err = self.cli_all("addon", "watcher", "demo")
        self.assertEqual((code, "demo has no watcher" in err), (2, True))
        self.assertEqual(self.systemctl, [])
        self.assertFalse(self.config.exists())

    def test_watch_runs_the_loop_for_the_addon(self):
        self.full()
        with mock.patch.object(cli.watcher, "run", return_value=0) as run, mock.patch.object(cli.signal, "signal") as handler:
            code = self.cli_all("addon", "watch", "demo")[0]
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.args[0].id, "demo")
        self.assertEqual(handler.call_args.args[0], cli.signal.SIGTERM)

    def test_watch_for_an_addon_without_a_watcher_exits_2(self):
        self.addon()
        code, _, err = self.cli_all("addon", "watch", "demo")
        self.assertEqual((code, "demo has no watcher" in err), (2, True))


if __name__ == "__main__":
    unittest.main()
