import json
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fsol import doctor, machine, registry
from tests.test_machine import LOCALCONFIG, FakeMachine, write

USER_REG = """WINE REGISTRY Version 2
;; All keys relative to REGISTRY\\\\User\\\\S-1-5-21-0-0-0-1000

#arch=win64

{sections}
"""

WPF_ON = '[Software\\\\Microsoft\\\\Avalon.Graphics] 1792067652\n#time=1dd5ca17bb9d9c8\n"DisableHWAcceleration"=dword:00000001\n'

GOOD_FONTS = """[Software\\\\Wine\\\\Fonts\\\\Replacements] 1792079457
#time=1dd5cbcf82f89d8
"Consolas"="DejaVu Sans Mono"
"Palatino Linotype"="Noto Serif"
"Segoe UI"="Noto Sans"
"Segoe UI Bold"="Noto Sans"
"Segoe UI Semibold"="Noto Sans Medium"
"Verdana"="DejaVu Sans"
"""

FAMILIES = {"noto sans", "noto sans medium", "noto serif", "dejavu sans", "dejavu sans mono", "times new roman"}

CONTENT = """<Packages>
\t<Package name="communityfs20-a" active="{a}"/>
\t<Package name="communityfs24-b" active="{b}"/>
\t<Package name="communityfs24-c" active="UserDisabled"/>
\t<Package name="fs24-asobo-bushtrip-australia" active="SystemDisabled"/>
</Packages>
"""

WAYLAND = LOCALCONFIG.replace("PROTON_NO_NTSYNC=1", "PROTON_NO_NTSYNC=1 PROTON_ENABLE_WAYLAND=1")


class RegistryTest(unittest.TestCase):
    def test_keys_and_value_names_are_found_without_regard_to_case(self):
        keys = registry.parse(USER_REG.format(sections=WPF_ON))
        self.assertEqual(registry.value(keys, r"software\microsoft\avalon.graphics", "disablehwacceleration"), 1)

    def test_strings_are_unescaped_and_keep_the_name_case(self):
        keys = registry.parse('[Software\\\\A] 1\n"Path"="C:\\\\Program Files\\\\X"\n"Quote"="a\\"b"\n@="default"\n')
        self.assertEqual(keys[r"software\a"], {"Path": r"C:\Program Files\X", "Quote": 'a"b', "": "default"})

    def test_continued_hex_lines_do_not_leak_into_the_next_value(self):
        keys = registry.parse('[A] 1\n"Bin"=hex:01,02,\\\n  03,04\n"After"=dword:0000000a\n')
        self.assertEqual(keys["a"]["Bin"], "hex:01,02,03,04")
        self.assertEqual(keys["a"]["After"], 10)


class RuleTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fake = FakeMachine(Path(self.temp.name))
        families = mock.patch.object(doctor, "installed_font_families", return_value=set(FAMILIES))
        self.families = families.start()
        self.addCleanup(families.stop)

    def tearDown(self):
        self.temp.cleanup()

    def registry_with(self, *sections):
        write(self.fake.prefix / "user.reg", USER_REG.format(sections="\n".join(sections)))

    def check(self, name, found=None):
        found = found or self.fake.discover()
        return next((check for check in doctor.run(found) if check.name == name), None)


class WpfTest(RuleTestCase):
    def test_the_software_key_is_ok(self):
        self.fake.build()
        self.registry_with(WPF_ON, GOOD_FONTS)
        self.assertEqual(self.check("wpf").status, "ok")

    def test_without_the_key_the_wayland_driver_warns_with_the_command(self):
        self.fake.build(launch_options=WAYLAND)
        self.registry_with(GOOD_FONTS)
        check = self.check("wpf")
        self.assertEqual(check.status, "warn")
        self.assertIn('reg add "HKCU\\Software\\Microsoft\\Avalon.Graphics" /v DisableHWAcceleration /t REG_DWORD /d 1 /f', check.command)

    def test_without_the_key_and_without_wayland_it_is_only_information(self):
        self.fake.build()
        self.registry_with(GOOD_FONTS)
        self.assertEqual(self.check("wpf").status, "info")

    def test_the_key_set_to_zero_is_not_software(self):
        self.fake.build(launch_options=WAYLAND)
        self.registry_with(WPF_ON.replace("00000001", "00000000"))
        self.assertEqual(self.check("wpf").status, "warn")

    def test_a_prefix_without_user_reg_has_no_wpf_line(self):
        self.fake.build()
        self.assertIsNone(self.check("wpf"))


class FontTest(RuleTestCase):
    def test_every_replacement_present_and_installed_is_ok(self):
        self.fake.build()
        self.registry_with(WPF_ON, GOOD_FONTS)
        self.assertEqual(self.check("fonts").status, "ok")

    def test_times_new_roman_and_a_missing_replacement_warn_and_only_they_are_fixed(self):
        self.fake.build()
        self.registry_with(GOOD_FONTS.replace('"Verdana"="DejaVu Sans"', '"Verdana"="Times New Roman"').replace('"Consolas"="DejaVu Sans Mono"\n', ""))
        check = self.check("fonts")
        self.assertEqual(check.status, "warn")
        self.assertIn("Verdana is replaced by Times New Roman", check.detail)
        self.assertIn("Consolas has no replacement", check.detail)
        self.assertIn('/v "Verdana" /d "DejaVu Sans"', check.command)
        self.assertIn('/v "Consolas" /d "DejaVu Sans Mono"', check.command)
        self.assertNotIn('"Segoe UI"', check.command)

    def test_a_replacement_the_system_lacks_warns_and_asks_to_install_it(self):
        self.fake.build()
        self.registry_with(GOOD_FONTS.replace("Noto Sans Medium", "Noto Sans SemiBold"))
        self.families.return_value = FAMILIES - {"noto sans medium"}
        check = self.check("fonts")
        self.assertIn("Segoe UI Semibold is replaced by Noto Sans SemiBold, which this system doesn't have", check.detail)
        self.assertIn("Install Noto Sans Medium first.", check.fix)

    def test_without_fc_list_only_the_registry_is_judged(self):
        self.fake.build()
        self.registry_with(GOOD_FONTS.replace("Noto Sans Medium", "Some Font"))
        self.families.return_value = None
        self.assertEqual(self.check("fonts").status, "ok")

    def test_the_fix_is_one_protontricks_command_for_the_simulator(self):
        self.fake.build()
        self.registry_with(GOOD_FONTS.replace('"Segoe UI"="Noto Sans"', '"Segoe UI"="Times New Roman"'))
        command = shlex.split(self.check("fonts").command)
        self.assertEqual(command[:2], ["protontricks", "-c"])
        self.assertEqual(command[3], "2537590")
        fake_wine = 'wine() { printf "%s\\n" "$@"; }; '
        written = subprocess.run(["sh", "-c", fake_wine + command[2]], capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(written, ["reg", "add", "HKCU\\Software\\Wine\\Fonts\\Replacements", "/v", "Segoe UI", "/d", "Noto Sans", "/f"])


class CommunityDisabledTest(RuleTestCase):
    def content(self, a="Activated", b="Activated"):
        path = self.fake.prefix / machine.USERCFG
        content = path.parent / "Pilot" / "Content.xml"
        write(content, CONTENT.format(a=a, b=b))
        return content

    def test_the_base_folder_is_where_usercfg_lives(self):
        self.fake.build()
        self.assertEqual(self.fake.discover().base, (self.fake.prefix / machine.USERCFG).parent)

    def test_only_system_disabled_community_packages_count(self):
        self.fake.build()
        self.content()
        self.assertEqual(self.check("community-disabled").status, "ok")

    def test_switched_off_community_packages_warn_with_their_number(self):
        self.fake.build()
        self.content(a="SystemDisabled", b="SystemDisabled")
        check = self.check("community-disabled")
        self.assertEqual(check.status, "warn")
        self.assertIn("switched off 2 Community packages", check.detail)

    def test_the_printed_fix_turns_on_only_what_the_simulator_switched_off(self):
        self.fake.build()
        content = self.content(a="SystemDisabled")
        subprocess.run(shlex.split(self.check("community-disabled").command), check=True)
        text = content.read_text()
        self.assertIn('"communityfs20-a" active="Activated"', text)
        self.assertIn('"communityfs24-c" active="UserDisabled"', text)
        self.assertIn('"fs24-asobo-bushtrip-australia" active="SystemDisabled"', text)
        self.assertEqual(self.check("community-disabled").status, "ok")

    def test_no_content_file_has_no_line(self):
        self.fake.build()
        self.assertIsNone(self.check("community-disabled"))


class ScreenTest(RuleTestCase):
    def test_the_sockets_and_the_shell_display_are_listed(self):
        self.fake.build()
        found = machine.discover(home=self.fake.home, steam_override="", proc=self.fake.proc, dev=self.fake.dev, runtime_dir=self.fake.runtime, drm=self.fake.drm, env={"WAYLAND_DISPLAY": "wayland-1"})
        check = self.check("wayland", found)
        self.assertEqual(check.status, "info")
        self.assertEqual(check.detail, "Wayland sockets in this session: wayland-1. This shell uses wayland-1")

    def test_the_simulator_display_is_added_when_it_runs(self):
        self.fake.build()
        self.fake.run_sim(environ={"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":1"})
        detail = self.check("wayland").detail
        self.assertEqual(detail, "Wayland sockets in this session: wayland-1. This shell uses none, the simulator wayland-1")

    def test_a_running_simulator_without_a_wayland_display_says_none(self):
        self.fake.build()
        self.fake.run_sim(environ={"DISPLAY": ":1"})
        self.assertTrue(self.check("wayland").detail.endswith("the simulator none"))

    def test_no_socket_with_the_wayland_driver_in_the_options_warns(self):
        self.fake.build(launch_options=WAYLAND)
        (self.fake.runtime / "wayland-1").unlink()
        check = self.check("wayland")
        self.assertEqual(check.status, "warn")
        self.assertIn("no Wayland socket", check.detail)
        self.assertIn("PROTON_ENABLE_WAYLAND=1", check.fix)

    def test_no_socket_without_the_wayland_driver_in_the_options_is_information(self):
        self.fake.build()
        (self.fake.runtime / "wayland-1").unlink()
        check = self.check("wayland")
        self.assertEqual(check.status, "info")
        self.assertEqual(check.fix, "")

    def test_a_running_simulator_with_the_wayland_driver_is_named(self):
        self.fake.build()
        self.fake.run_sim(maps=["x86_64-windows/winewayland.drv"])
        check = self.check("graphics-driver")
        self.assertEqual(check.status, "info")
        self.assertIn("Wayland driver (winewayland.drv)", check.detail)
        self.assertIn("Your screen setup changes what works", check.fix)

    def test_a_running_simulator_with_the_x11_driver_is_named(self):
        self.fake.build()
        self.fake.run_sim(maps=["x86_64-windows/winex11.drv"])
        self.assertIn("X11 driver (winex11.drv)", self.check("graphics-driver").detail)

    def test_a_running_simulator_with_both_drivers_says_both(self):
        self.fake.build()
        self.fake.run_sim(maps=["x86_64-windows/winewayland.drv", "x86_64-windows/winex11.drv"])
        detail = self.check("graphics-driver").detail
        self.assertIn("both", detail)
        self.assertIn("winewayland.drv", detail)
        self.assertIn("winex11.drv", detail)

    def test_a_running_simulator_without_a_driver_says_so(self):
        self.fake.build()
        self.fake.run_sim(maps=["x86_64-windows/ntdll.dll"])
        check = self.check("graphics-driver")
        self.assertIn("has not loaded a Wine display driver yet", check.detail)
        self.assertEqual(check.fix, "")

    def test_a_stopped_simulator_with_the_wayland_option_is_predicted_on_wayland(self):
        self.fake.build(launch_options=WAYLAND)
        check = self.check("graphics-driver")
        self.assertEqual(check.status, "info")
        self.assertIn("will start with Wine's Wayland driver", check.detail)

    def test_a_stopped_simulator_without_it_is_predicted_on_x11(self):
        self.fake.build()
        check = self.check("graphics-driver")
        self.assertEqual(check.status, "info")
        self.assertIn("will start with Wine's X11 driver", check.detail)

    def test_monitors_are_counted(self):
        self.fake.build()
        self.fake.connector("card0-DP-4", "connected", "enabled")
        self.assertEqual(self.check("monitors").detail, "1 monitor is on")
        self.assertEqual(self.check("monitors").fix, "")
        self.fake.connector("card1-HDMI-A-1", "connected", "enabled")
        check = self.check("monitors")
        self.assertEqual(check.detail, "2 monitors are on")
        self.assertEqual(check.status, "info")
        self.assertIn("start it on the monitor you play on", check.fix)
        self.assertNotIn("driver", check.fix)
        self.assertIn("Your screen setup changes what works", check.fix)

    def test_without_a_drm_folder_there_is_no_monitors_line(self):
        self.fake.build()
        shutil.rmtree(self.fake.drm)
        self.assertIsNone(self.check("monitors"))


class MachineJsonTest(unittest.TestCase):
    def test_machine_as_json_has_plain_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = FakeMachine(Path(temp)).build()
            fake.run_sim()
            found = json.loads(json.dumps(fake.discover().as_dict()))
        self.assertEqual(found["libraries"], [str(fake.steam), str(fake.games)])
        self.assertEqual(found["sim_pid"], 4242)


if __name__ == "__main__":
    unittest.main()
