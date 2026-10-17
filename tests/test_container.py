import os
import stat
import tempfile
import unittest
from pathlib import Path

from fsol import container
from tests.test_machine import FakeMachine, write

SIM_ENV = {
    "WINEPREFIX": "/pfx",
    "WINEDLLOVERRIDES": "d3d9=n",
    "WINESERVERSOCKET": "/tmp/socket",
    "LD_PRELOAD": "/overlay.so",
    "DISPLAY": ":1",
    "XAUTHORITY": "/run/sim-cookie",
    "STEAM_COMPAT_DATA_PATH": "/compat",
}


class ContainerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fake = FakeMachine(Path(self.temp.name)).build()
        self.root = self.fake.proc / "4242" / "root"

    def tearDown(self):
        self.temp.cleanup()

    def start_sim(self, env=SIM_ENV):
        self.fake.run_sim()
        write(self.fake.proc / "4242" / "environ", "".join(f"{key}={value}\0" for key, value in env.items()))
        self.root.mkdir(parents=True, exist_ok=True)

    def show_in_container(self, path):
        inside = self.root / str(path).lstrip("/")
        inside.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(path, inside)

    def plan(self, args, caller=None):
        return container.plan(self.fake.discover(), args, caller or {}, proc=self.fake.proc)

    def test_a_closed_simulator_is_an_error(self):
        with self.assertRaisesRegex(container.ContainerError, "not running"):
            self.plan(["C:\\Tools\\App.exe"])

    def test_the_program_runs_with_the_simulators_wine_inside_its_namespaces(self):
        self.start_sim()
        command, _ = self.plan(["C:\\Tools\\App.exe", "-quiet"])
        self.assertEqual(command, ["nsenter", "-t", "4242", "-U", "-m", "--preserve-credentials", str(self.fake.tool / "files/bin/wine"), "C:\\Tools\\App.exe", "-quiet"])

    def test_the_simulators_environment_is_kept_without_its_wineserver_socket_and_preload(self):
        self.start_sim()
        _, env = self.plan(["App.exe"])
        self.assertEqual(env["WINEPREFIX"], "/pfx")
        self.assertNotIn("WINESERVERSOCKET", env)
        self.assertNotIn("LD_PRELOAD", env)
        self.assertEqual(env["WINEDEBUG"], "-all")

    def test_leading_assignments_set_the_environment_and_a_windows_path_is_not_one(self):
        self.start_sim()
        command, env = self.plan(["WINEDLLOVERRIDES=d3d9=d", "WINEDEBUG=+seh", "C:\\a=b\\App.exe"], {"WINEDEBUG": "+loaddll"})
        self.assertEqual(env["WINEDLLOVERRIDES"], "d3d9=d")
        self.assertEqual(env["WINEDEBUG"], "+seh")
        self.assertEqual(command[-1], "C:\\a=b\\App.exe")

    def test_the_callers_winedebug_and_extra_overrides_are_added(self):
        self.start_sim()
        _, env = self.plan(["App.exe"], {"WINEDEBUG": "+loaddll", "EXTRA_WINEDLLOVERRIDES": "httpapi=d"})
        self.assertEqual(env["WINEDEBUG"], "+loaddll")
        self.assertEqual(env["WINEDLLOVERRIDES"], "d3d9=n;httpapi=d")

    def test_voice_files_come_from_any_steam_library(self):
        self.start_sim()
        (self.fake.games / "steamapps/common/Proton Voice Files").mkdir(parents=True)
        _, env = self.plan(["App.exe"])
        self.assertEqual(env["PROTON_VOICE_FILES"], str(self.fake.games / "steamapps/common/Proton Voice Files"))

    def test_the_same_display_keeps_the_simulators_cookie(self):
        self.start_sim()
        _, env = self.plan(["App.exe"], {"DISPLAY": ":1", "XAUTHORITY": "/home/pilot/.Xauthority"})
        self.assertEqual(env["XAUTHORITY"], "/run/sim-cookie")

    def test_a_desktop_cookie_the_container_sees_is_used_as_it_is(self):
        self.start_sim()
        cookie = Path(self.temp.name) / "cookie"
        write(cookie, "secret")
        self.show_in_container(cookie)
        _, env = self.plan(["App.exe"], {"DISPLAY": ":0", "XAUTHORITY": str(cookie)})
        self.assertEqual((env["DISPLAY"], env["XAUTHORITY"]), (":0", str(cookie)))

    def test_a_hidden_desktop_cookie_is_copied_into_compatdata_for_the_owner_only(self):
        self.start_sim()
        cookie = Path(self.temp.name) / "hidden-cookie"
        write(cookie, "secret")
        self.show_in_container(self.fake.compatdata)
        _, env = self.plan(["App.exe"], {"DISPLAY": ":0", "XAUTHORITY": str(cookie)})
        copy = self.fake.compatdata / container.XAUTHORITY_COPY
        self.assertEqual(env["XAUTHORITY"], str(copy))
        self.assertEqual(copy.read_text(), "secret")
        self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o600)

    def test_a_cookie_the_container_cannot_see_at_all_is_an_error(self):
        self.start_sim()
        cookie = Path(self.temp.name) / "hidden-cookie"
        write(cookie, "secret")
        with self.assertRaisesRegex(container.ContainerError, "sees neither"):
            self.plan(["App.exe"], {"DISPLAY": ":0", "XAUTHORITY": str(cookie)})

    def test_a_display_without_a_cookie_drops_the_simulators_cookie(self):
        self.start_sim()
        _, env = self.plan(["App.exe"], {"DISPLAY": ":0"})
        self.assertNotIn("XAUTHORITY", env)

    def test_no_program_is_an_error(self):
        self.start_sim()
        with self.assertRaisesRegex(container.ContainerError, "no program"):
            self.plan(["WINEDEBUG=+seh"])


if __name__ == "__main__":
    unittest.main()
