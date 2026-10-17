import os
import tempfile
import unittest
from pathlib import Path

from fsol import doctor, machine, vdf

LIBRARYFOLDERS = """
"libraryfolders"
{
\t"0"
\t{
\t\t"path"\t\t"{steam}"
\t\t"apps" { "228980" "0" }
\t}
\t"1"
\t{
\t\t"path"\t\t"{games}"
\t\t"apps" { "2537590" "1" }
\t}
}
"""

APPMANIFEST = '"AppState"\n{\n\t"appid"\t\t"2537590"\n\t"installdir"\t\t"MSFS2024"\n}\n'

CONFIG = """
"InstallConfigStore" { "Software" { "Valve" { "Steam" { "CompatToolMapping" {
  "2537590" { "name" "Proton-Fenix-A320-test" "config" "" "priority" "250" }
} } } } }
"""

LOCALCONFIG = """
"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" {
  "2537590" { "LaunchOptions" "PROTON_NO_NTSYNC=1 %command%" }
} } } } }
"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class FakeMachine:
    def __init__(self, root):
        self.home = root / "home"
        self.steam = self.home / ".local/share/Steam"
        self.games = root / "games"
        self.tool = self.steam / "compatibilitytools.d/Proton-Fenix-A320-test"
        self.compatdata = self.games / "steamapps/compatdata/2537590"
        self.prefix = self.compatdata / "pfx"
        self.packages = root / "packages"
        self.proc = root / "proc"
        self.dev = root / "dev"

    def build(self, launch_options=LOCALCONFIG, fenix=True):
        write(self.steam / "steamapps/libraryfolders.vdf", LIBRARYFOLDERS.replace("{steam}", str(self.steam)).replace("{games}", str(self.games)))
        write(self.games / "steamapps/appmanifest_2537590.acf", APPMANIFEST)
        (self.games / "steamapps/common/MSFS2024").mkdir(parents=True)
        write(self.steam / "config/config.vdf", CONFIG)
        write(self.steam / "userdata/1234/config/localconfig.vdf", launch_options)
        write(self.tool / "files/bin/wine", "")
        write(self.tool / "user_settings.py", 'user_settings = {"WINE_FENIX_HELPER_WINDOWS": "1"}\n' if fenix else "user_settings = {}\n")
        write(self.compatdata / "config_info", f"CachyOS-11.0-100\n{self.tool}/files/share/fonts/\n{self.tool}/files/lib/\n")
        (self.prefix / "dosdevices").mkdir(parents=True)
        os.symlink("/", self.prefix / "dosdevices/z:")
        write(self.prefix / machine.USERCFG, f'Version 1\nInstalledPackagesPath "Z:{str(self.packages).replace("/", chr(92))}"\n')
        (self.packages / "Community").mkdir(parents=True)
        self.proc.mkdir()
        self.dev.mkdir()
        write(self.dev / "ntsync", "")
        return self

    def run_sim(self):
        write(self.proc / "4242/cmdline", "S:\\steamapps\\common\\MSFS2024\\FlightSimulator2024.exe\0-FastLaunch\0")

    def run_wineserver(self, prefix, handles):
        server = self.proc / "4300"
        write(server / "comm", "wineserver\n")
        write(server / "environ", f"HOME=/home/pilot\0WINEPREFIX={prefix}/\0")
        (server / "fd").mkdir()
        os.symlink("/dev/null", server / "fd/0")
        for number in range(handles):
            os.symlink("/dev/ntsync", server / f"fd/{number + 10}")

    def discover(self):
        return machine.discover(home=self.home, steam_override="", proc=self.proc, dev=self.dev)


class VdfTest(unittest.TestCase):
    def test_nested_keys_are_found_without_regard_to_case(self):
        tree = vdf.parse('"A" { "b" { "Key" "va\\"lue" } }')
        self.assertEqual(vdf.find(tree, "a", "B", "key"), 'va"lue')

    def test_comments_are_ignored(self):
        tree = vdf.parse('// header\n"A" { "k" "v" // note\n }')
        self.assertEqual(vdf.find(tree, "A", "k"), "v")


class DiscoverTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fake = FakeMachine(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_the_simulator_is_found_in_a_second_library(self):
        found = self.fake.build().discover()
        self.assertEqual(found.sim_library, self.fake.games)
        self.assertEqual(found.sim_install, self.fake.games / "steamapps/common/MSFS2024")
        self.assertEqual(found.prefix, self.fake.prefix)

    def test_proton_comes_from_config_info_and_its_name_from_config_vdf(self):
        found = self.fake.build().discover()
        self.assertEqual(found.proton, self.fake.tool)
        self.assertEqual(found.compat_tool, "Proton-Fenix-A320-test")
        self.assertTrue(found.proton_is_fenix)

    def test_community_follows_installed_packages_path_through_the_drive_letter(self):
        found = self.fake.build().discover()
        self.assertEqual(found.community, self.fake.packages.resolve() / "Community")

    def test_launch_options_are_read_per_steam_user(self):
        found = self.fake.build().discover()
        self.assertEqual(found.launch_options, {"1234": "PROTON_NO_NTSYNC=1 %command%"})

    def test_a_running_simulator_is_seen_in_proc(self):
        self.fake.build()
        self.assertFalse(self.fake.discover().sim_running)
        self.fake.run_sim()
        self.assertTrue(self.fake.discover().sim_running)

    def test_no_steam_gives_an_empty_machine(self):
        found = machine.discover(home=Path(self.temp.name) / "nobody", steam_override="", proc=self.fake.proc, dev=self.fake.proc)
        self.assertIsNone(found.steam)


class DoctorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fake = FakeMachine(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def statuses(self, found):
        return {check.name: check.status for check in doctor.run(found)}

    def ntsync(self, found):
        return next(check for check in doctor.run(found) if check.name == "ntsync")

    def test_a_complete_setup_has_no_warning_or_failure(self):
        statuses = self.statuses(self.fake.build().discover())
        self.assertNotIn("fail", statuses.values())
        self.assertNotIn("warn", statuses.values())
        self.assertEqual(statuses["proton"], "ok")

    def test_ntsync_is_only_information(self):
        found = self.fake.build(launch_options=LOCALCONFIG.replace("PROTON_NO_NTSYNC=1 ", "")).discover()
        self.assertEqual(self.ntsync(found).status, "info")

    def test_ntsync_off_by_launch_options_needs_no_remedy(self):
        check = self.ntsync(self.fake.build().discover())
        self.assertIn("off", check.detail)
        self.assertEqual(check.fix, "")

    def test_ntsync_on_by_default_offers_the_remedy(self):
        check = self.ntsync(self.fake.build(launch_options=LOCALCONFIG.replace("PROTON_NO_NTSYNC=1 ", "")).discover())
        self.assertIn("will be on", check.detail)
        self.assertIn("PROTON_NO_NTSYNC=1", check.fix)

    def test_ntsync_zero_in_launch_options_does_not_turn_it_off(self):
        check = self.ntsync(self.fake.build(launch_options=LOCALCONFIG.replace("PROTON_NO_NTSYNC=1", "PROTON_NO_NTSYNC=0")).discover())
        self.assertIn("will be on", check.detail)

    def test_without_the_device_ntsync_cannot_be_on(self):
        self.fake.build(launch_options=LOCALCONFIG.replace("PROTON_NO_NTSYNC=1 ", ""))
        os.remove(self.fake.dev / "ntsync")
        check = self.ntsync(self.fake.discover())
        self.assertIn("no /dev/ntsync", check.detail)

    def test_a_running_simulator_is_judged_by_its_wineserver_handles(self):
        self.fake.build()
        self.fake.run_sim()
        self.fake.run_wineserver(self.fake.prefix, 3)
        found = self.fake.discover()
        self.assertEqual(found.ntsync_handles, 3)
        self.assertIn("holds 3 /dev/ntsync handles", self.ntsync(found).detail)

    def test_a_running_simulator_without_handles_has_ntsync_off(self):
        self.fake.build(launch_options=LOCALCONFIG.replace("PROTON_NO_NTSYNC=1 ", ""))
        self.fake.run_sim()
        self.fake.run_wineserver(self.fake.prefix, 0)
        check = self.ntsync(self.fake.discover())
        self.assertIn("off in the running simulator", check.detail)

    def test_a_wineserver_of_another_prefix_is_ignored(self):
        self.fake.build()
        self.fake.run_sim()
        self.fake.run_wineserver(self.fake.home / "other-prefix", 5)
        self.assertIsNone(self.fake.discover().ntsync_handles)

    def test_a_proton_that_is_not_fenix_warns(self):
        found = self.fake.build(fenix=False).discover()
        self.assertEqual(self.statuses(found)["proton"], "warn")

    def test_no_steam_stops_at_the_first_check(self):
        found = machine.discover(home=Path(self.temp.name) / "nobody", steam_override="", proc=self.fake.proc, dev=self.fake.proc)
        self.assertEqual([check.name for check in doctor.run(found)], ["steam"])
        self.assertEqual(doctor.run(found)[0].status, "fail")


if __name__ == "__main__":
    unittest.main()
