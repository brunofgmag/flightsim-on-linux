import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
  "2537590" { "name" "Proton-FS2024-test" "config" "" "priority" "250" }
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
        self.tool = self.steam / "compatibilitytools.d/Proton-FS2024-test"
        self.compatdata = self.games / "steamapps/compatdata/2537590"
        self.prefix = self.compatdata / "pfx"
        self.packages = root / "packages"
        self.proc = root / "proc"
        self.dev = root / "dev"
        self.runtime = root / "run"
        self.drm = root / "drm"

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
        self.runtime.mkdir()
        self.drm.mkdir()
        self.listen("wayland-1")
        return self

    def listen(self, name):
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(self.runtime / name))
        listener.listen()
        unittest.addModuleCleanup(listener.close)

    def connector(self, name, status, enabled):
        write(self.drm / name / "status", status + "\n")
        write(self.drm / name / "enabled", enabled + "\n")

    def run_sim(self, environ=None, maps=None):
        write(self.proc / "4242/cmdline", "S:\\steamapps\\common\\MSFS2024\\FlightSimulator2024.exe\0-FastLaunch\0")
        if environ is not None:
            write(self.proc / "4242/environ", "".join(f"{key}={value}\0" for key, value in environ.items()))
        if maps is not None:
            write(self.proc / "4242/maps", "".join(f"7f00-7f01 r-xp 0 00:00 0 /opt/proton/files/lib/wine/{name}\n" for name in maps))

    def run_wineserver(self, prefix, handles):
        server = self.proc / "4300"
        write(server / "comm", "wineserver\n")
        write(server / "environ", f"HOME=/home/pilot\0WINEPREFIX={prefix}/\0")
        (server / "fd").mkdir()
        os.symlink("/dev/null", server / "fd/0")
        for number in range(handles):
            os.symlink("/dev/ntsync", server / f"fd/{number + 10}")

    def discover(self):
        return machine.discover(home=self.home, steam_override="", proc=self.proc, dev=self.dev, runtime_dir=self.runtime, drm=self.drm, env={})


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
        self.assertEqual(found.compat_tool, "Proton-FS2024-test")
        self.assertTrue(found.proton_is_fs2024)

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

    def test_only_sockets_count_as_wayland_sockets(self):
        self.fake.build()
        write(self.fake.runtime / "wayland-1.lock", "")
        write(self.fake.runtime / "wayland-0", "")
        self.assertEqual(machine.wayland_sockets(self.fake.runtime), ["wayland-1"])
        self.assertEqual(self.fake.discover().wayland_sockets, ["wayland-1"])

    def test_sockets_are_sorted_and_a_missing_runtime_folder_has_none(self):
        self.fake.build()
        self.fake.listen("wayland-0")
        self.assertEqual(machine.wayland_sockets(self.fake.runtime), ["wayland-0", "wayland-1"])
        self.assertEqual(machine.wayland_sockets(self.fake.runtime / "nowhere"), [])

    def test_the_session_display_comes_from_the_environment_passed_in(self):
        self.fake.build()
        found = machine.discover(home=self.fake.home, steam_override="", proc=self.fake.proc, dev=self.fake.dev, runtime_dir=self.fake.runtime, drm=self.fake.drm, env={"WAYLAND_DISPLAY": "wayland-1"})
        self.assertEqual(found.session_wayland_display, "wayland-1")
        self.assertIsNone(self.fake.discover().session_wayland_display)

    def test_only_connected_and_enabled_connectors_are_monitors(self):
        self.fake.build()
        self.fake.connector("card0-DP-4", "connected", "enabled")
        self.fake.connector("card1-HDMI-A-1", "connected", "enabled")
        self.fake.connector("card1-DP-2", "connected", "disabled")
        self.fake.connector("card1-DP-3", "disconnected", "disabled")
        self.fake.connector("card0-Writeback-1", "unknown", "disabled")
        write(self.fake.drm / "version", "")
        self.assertEqual(machine.connected_monitors(self.fake.drm), 2)
        self.assertEqual(self.fake.discover().monitors, 2)

    def test_without_a_drm_folder_the_monitors_are_unknown(self):
        self.assertIsNone(machine.connected_monitors(Path(self.temp.name) / "nowhere"))

    def test_the_real_drm_folder_gives_a_count(self):
        drm = Path("/sys/class/drm")
        if not drm.is_dir():
            self.skipTest("no /sys/class/drm here")
        count = machine.connected_monitors(drm)
        self.assertIsInstance(count, int)
        self.assertGreaterEqual(count, 0)

    def test_display_drivers_come_from_the_simulator_maps(self):
        self.fake.build()
        sim = self.fake.proc / "4242"
        cases = {
            ("x86_64-windows/winewayland.drv",): ["winewayland"],
            ("x86_64-windows/winex11.drv",): ["winex11"],
            ("x86_64-windows/winex11.drv", "x86_64-windows/winewayland.drv"): ["winewayland", "winex11"],
            ("x86_64-windows/ntdll.dll",): [],
        }
        for maps, expected in cases.items():
            with self.subTest(maps=maps):
                self.fake.run_sim(maps=maps)
                self.assertEqual(machine.display_drivers(sim), expected)
                self.assertEqual(self.fake.discover().sim_display_drivers, expected)

    def test_a_simulator_without_readable_maps_has_no_drivers(self):
        self.assertEqual(machine.display_drivers(self.fake.proc / "4242"), [])

    def test_the_simulator_displays_come_from_its_environment_only_while_it_runs(self):
        self.fake.build()
        found = self.fake.discover()
        self.assertIsNone(found.sim_wayland_display)
        self.assertIsNone(found.sim_x_display)
        self.fake.run_sim(environ={"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":1"})
        found = self.fake.discover()
        self.assertEqual(found.sim_wayland_display, "wayland-1")
        self.assertEqual(found.sim_x_display, ":1")


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


class BuildToolsTest(unittest.TestCase):
    EVERY_TOOL = ("git", "curl", "patch", "perl", "autoreconf", "make", "gcc", "flex", "bison", "x86_64-w64-mingw32-gcc",
                  "x86_64-w64-mingw32-g++", "meson", "ninja", "glslangValidator", "widl")

    def check_with(self, installed):
        with mock.patch.object(doctor.shutil, "which", lambda tool: f"/usr/bin/{tool}" if tool in installed else None):
            return doctor.check_build_tools(None)

    def test_every_tool_installed_is_ok(self):
        self.assertEqual(self.check_with(self.EVERY_TOOL).status, "ok")

    def test_the_vkd3d_proton_tools_are_needed(self):
        for tool in ("x86_64-w64-mingw32-g++", "meson", "ninja", "glslangValidator"):
            check = self.check_with(set(self.EVERY_TOOL) - {tool})
            self.assertEqual(check.status, "info")
            self.assertIn(tool, check.detail)

    def test_the_mingw_tools_widl_is_enough(self):
        installed = (set(self.EVERY_TOOL) - {"widl"}) | {"x86_64-w64-mingw32-widl"}
        self.assertEqual(self.check_with(installed).status, "ok")

    def test_without_any_widl_every_name_meson_accepts_is_named(self):
        check = self.check_with(set(self.EVERY_TOOL) - {"widl"})
        self.assertIn("widl, widl-stable or x86_64-w64-mingw32-widl", check.detail)


if __name__ == "__main__":
    unittest.main()
