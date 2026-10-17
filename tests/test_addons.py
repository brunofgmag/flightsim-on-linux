import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from fsol import addons, cli
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

    def test_an_addon_that_needs_proton_fenix_refuses_another_proton(self):
        (self.fake.tool / "user_settings.py").write_text("user_settings = {}\n")
        self.addon(spec=dict(SPEC, needs=["proton-fenix"]))
        code, output = self.cli("addon", "apply", "demo")
        self.assertEqual(code, 1)
        self.assertIn("Proton Fenix A320", output)

    def test_a_fix_that_touches_addon_files_says_so_and_how_to_undo(self):
        self.addon(spec=dict(SPEC, touches_addon_files=True))
        _, output = self.cli("addon", "apply", "demo")
        self.assertIn("changes files of the add-on itself; flightsim-on-linux addon undo demo restores them", output)

    def test_scripts_get_the_machine_in_fsol_variables(self):
        self.addon(apply='#!/bin/sh\necho "$FSOL_PREFIX|$FSOL_COMMUNITY|$FSOL_BASE|$FSOL_PROTON|$FSOL_SIM_RUNNING|$FSOL_ADDON"\ntouch "$FSOL_PREFIX/fixed"\n')
        _, output = self.cli("addon", "apply", "demo")
        found = self.fake.discover()
        self.assertIn(f"  {found.prefix}|{found.community}|{found.base}|{found.proton}|0|demo", output)

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


if __name__ == "__main__":
    unittest.main()
