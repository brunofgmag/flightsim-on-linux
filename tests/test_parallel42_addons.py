import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fsol import addons
from tests.support import process_stubs, start_stand_in, stub
from tests.test_gsx_addon import sha256
from tests.test_machine import FakeMachine, write

ROOT = Path(__file__).resolve().parent.parent
CHASEPLANE = ROOT / "addons" / "chaseplane"
FLOW = ROOT / "addons" / "flow"
PARTS = CHASEPLANE / "parts"

RESOLVER = "file:///C:/appresolver/AppResolverShim.dll"
TOAST_PATH = "C:\\appresolver\\toaststub.dll"
BRIDGE_PATTERN = r"^[A-Za-z]:.*\\CP MSFS Bridge\.exe"
BRIDGE_REL = "p42-util-chaseplane/Modules/ChasePlane/CP MSFS Bridge.exe"
MANAGER_REL = "p42-util-chaseplane/ChasePlane for MSFS2024.exe"
FLOW_REL = "p42-util-flow-pro/Flow for MSFS2024.exe"
POINTER_MODULE = "lib/wine/x86_64-unix/winewayland.so"
WINESERVER = "bin/wineserver"
FIXES = {
    "server-process-vm-eperm-per-call.patch": WINESERVER,
    "winewayland-hidden-cursor-window-clip-confines.patch": POINTER_MODULE,
    "winewayland-commit-pointer-constraint-changes.patch": POINTER_MODULE,
    "winewayland-small-clip-warps-pointer-inside.patch": POINTER_MODULE,
    "winewayland-clip-at-least-one-pixel.patch": POINTER_MODULE,
    "winewayland-warp-before-confining.patch": POINTER_MODULE,
}
LAUNCH_OPTIONS = '"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" { "2537590" { "LaunchOptions" "DXVK_CONFIG=\\"a=1\\" STEAM_COMPAT_LIBRARY_PATHS=%s PROTON_NO_NTSYNC=1 %%command%%" } } } } } }'
SYSTEM_REG = (
    "WINE REGISTRY Version 2\n\n"
    "[Software\\\\Classes\\\\CLSID\\\\{660B90C8-73A9-4B58-8CAE-355B7F55341B}\\\\InprocServer32] 1784729231\n"
    '@="mscoree.dll"\n"CodeBase"="file:///C:/appresolver/AppResolverShim.dll"\n\n'
    "[Software\\\\Microsoft\\\\WindowsRuntime\\\\ActivatableClassId\\\\Windows.UI.Notifications.ToastNotificationManager] 1784730558\n"
    '"DllPath"="C:\\\\appresolver\\\\toaststub.dll"\n'
)
USER_REG = 'WINE REGISTRY Version 2\n\n[Software\\\\Wine\\\\AppDefaults\\\\CP MSFS Bridge.exe\\\\DllOverrides] 1792255451\n"httpapi"=""\n'


class P42Case(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.token = str(self.root)
        self.fake = FakeMachine(self.root)
        self.fake.build()
        self.library = self.fake.games
        write(self.fake.steam / "userdata/1234/config/localconfig.vdf", LAUNCH_OPTIONS % self.library)
        self.found = self.fake.discover()
        self.prefix = self.found.prefix
        self.proton = self.fake.tool
        self.community = self.found.community
        self.record = self.root / "record"
        self.cache = self.root / "cache"
        for name in ("AppResolverShim.dll", "toaststub.dll"):
            built = self.cache / "shims" / name
            write(built, f"built {name}")
            os.utime(built, (4102444800, 4102444800))
        (self.prefix / "dosdevices/s:").symlink_to(self.library)
        stub(self.root / "bin/notify-send", f'#!/bin/sh\nprintf "notice %s\\n" "$*" >> "{self.record}"\n')
        stub(self.proton / "files/bin/wine", self.wine_stub())
        stub(self.proton / "files/bin/wineserver", "#!/bin/sh\nexit 0\n")
        process_stubs(self.root / "bin", self.token)
        self.write_build()
        stub(self.root / "bin/protontricks", f'#!/bin/sh\nprintf "protontricks %s\\n" "$*" >> "{self.record}"\n')
        stub(self.root / "cli", f'#!/bin/sh\nprintf "cli %s\\n" "$*" >> "{self.record}"\nprintf "%s\\n" "$EXTRA_WINEDLLOVERRIDES" >> "{self.record}.env"\n')

    def tearDown(self):
        self.temp.cleanup()

    def wine_stub(self):
        return (
            "#!/bin/sh\n"
            f'printf "wine %s\\n" "$*" >> "{self.record}"\n'
            'if [ "$1" = regedit ]; then\n'
            '  file=$(printf "%s" "$3" | sed "s/^Z://; s/\\\\\\\\/\\//g")\n'
            f'  printf "%s\\n" "$file" >> "{self.record}.imports"\n'
            "fi\n"
        )

    def write_build(self, without=None):
        lines = ["release cachyos-11.0-20261005-slr x86_64"]
        modules = {}
        for patch, module in FIXES.items():
            if patch == without:
                continue
            lines.append(f"patch {sha256((ROOT / 'patches/wine' / patch).read_bytes())} {patch}")
            modules[module] = None
        for module in sorted(set(FIXES.values())):
            if not (self.proton / "files" / module).exists():
                write(self.proton / "files" / module, module)
            lines.append(f"module {sha256((self.proton / 'files' / module).read_bytes())} {module}")
        (self.proton / "fs2024-build.txt").write_text("\n".join(lines) + "\n")

    def environment(self, addon, **extra):
        env = {
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "FSOL_PREFIX": str(self.prefix),
            "FSOL_COMPATDATA": str(self.found.compatdata),
            "FSOL_PROTON": str(self.proton),
            "FSOL_BASE": str(self.found.base),
            "FSOL_COMMUNITY": str(self.community),
            "FSOL_STEAM": str(self.fake.steam),
            "FSOL_SIM_INSTALL": str(self.found.sim_install),
            "FSOL_CACHE": str(self.cache),
            "FSOL_CLI": str(self.root / "cli"),
            "FSOL_ADDON": addon,
            "FSOL_TEST_TOKEN": self.token,
            "FSOL_SIM_RUNNING": "0",
        }
        env.update(extra)
        return env

    def run_script(self, folder, name, *args, **extra):
        return subprocess.run([str(folder / name), *args], env=self.environment(folder.name, **extra), capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def chaseplane(self, name, *args, **extra):
        return self.run_script(CHASEPLANE, name, *args, **extra)

    def flow(self, name, *args, **extra):
        return self.run_script(FLOW, name, *args, **extra)

    def recorded(self):
        return self.record.read_text().splitlines() if self.record.exists() else []

    def imports(self):
        path = Path(f"{self.record}.imports")
        return [Path(line).name for line in path.read_text().splitlines()] if path.exists() else []

    def appresolver(self, name):
        return self.prefix / "drive_c/appresolver" / name

    def install_package(self, relative):
        write(self.community / relative, "MZ")

    def snapshot(self):
        return {str(path.relative_to(self.root)): sha256(path.read_bytes()) for path in sorted(self.root.rglob("*")) if path.is_file() and not path.is_symlink() and not path.name.startswith("record") and "cache" not in path.parts}


class ShapeTest(P42Case):
    def test_the_chaseplane_manifest_is_complete(self):
        addon = addons.load(CHASEPLANE)
        self.assertEqual((addon.id, addon.needs, addon.tested_version, addon.tested_on), ("chaseplane", ["proton-fs2024", "simulator-closed"], "2026.39.1.16", "2026-10-17"))
        self.assertFalse(addon.touches_addon_files)
        self.assertTrue(addon.can_install)
        self.assertEqual([shortcut["title"] for shortcut in addon.shortcuts], ["ChasePlane"])

    def test_the_bridge_watcher_starts_the_bridge_only_when_port_8651_is_free_and_stops_it_with_the_simulator(self):
        configured = addons.load(CHASEPLANE).watcher
        self.assertEqual(configured["script"], "launch-bridge")
        self.assertEqual(configured["health"], {"listening": 8651})
        self.assertEqual(configured["stop"], "with_simulator")
        self.assertEqual(configured["app"], BRIDGE_PATTERN)
        self.assertEqual(configured.get("trigger", "simulator"), "simulator")

    def test_the_bridge_pattern_matches_a_c_path_and_a_z_path_but_not_the_manager(self):
        pattern = addons.load(CHASEPLANE).watcher["app"]
        self.assertTrue(re.search(pattern, r"C:\users\steamuser\AppData\Roaming\Packages\Community\p42-util-chaseplane\Modules\ChasePlane\CP MSFS Bridge.exe chaseplanemsfs24://"))
        self.assertTrue(re.search(pattern, r"Z:\home\pilot\Community\p42-util-chaseplane\Modules\ChasePlane\CP MSFS Bridge.exe"))
        self.assertFalse(re.search(pattern, r"Z:\home\pilot\Community\p42-util-chaseplane\ChasePlane for MSFS2024.exe"))

    def test_the_flow_manifest_is_complete_and_has_no_watcher(self):
        addon = addons.load(FLOW)
        self.assertEqual((addon.id, addon.needs, addon.tested_version, addon.tested_on), ("flow", ["proton-fs2024", "simulator-closed"], "2026.44.2.7", "2026-10-28"))
        self.assertIsNone(addon.watcher)
        self.assertTrue(addon.can_install)
        self.assertEqual([shortcut["title"] for shortcut in addon.shortcuts], ["Flow Pro"])

    def test_the_registry_text_names_the_files_the_check_looks_for(self):
        text = (PARTS / "p42-install.reg").read_text()
        self.assertIn(RESOLVER, text)
        self.assertIn(TOAST_PATH.replace("\\", "\\\\"), text)
        removal = (PARTS / "p42-remove.reg").read_text()
        self.assertIn("[-HKEY_CLASSES_ROOT\\CLSID\\{660B90C8-73A9-4B58-8CAE-355B7F55341B}]", removal)
        self.assertIn("ToastNotificationManager]", removal)


class ApplyTest(P42Case):
    def test_chaseplane_apply_puts_the_two_stand_ins_in_the_prefix_and_registers_them(self):
        result = self.chaseplane("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.appresolver("AppResolverShim.dll").read_text(), "built AppResolverShim.dll")
        self.assertEqual(self.appresolver("toaststub.dll").read_text(), "built toaststub.dll")
        self.assertEqual(self.imports(), ["p42-install.reg", "bridge-install.reg"])

    def test_chaseplane_apply_turns_httpapi_off_for_the_bridge_alone(self):
        self.chaseplane("apply")
        text = (PARTS / "bridge-install.reg").read_text()
        self.assertIn("[HKEY_CURRENT_USER\\Software\\Wine\\AppDefaults\\CP MSFS Bridge.exe\\DllOverrides]", text)
        self.assertIn('"httpapi"=""', text)
        self.assertEqual(text.count("["), 1)

    def test_chaseplane_apply_prints_the_launch_option_to_add_with_the_library_of_this_machine(self):
        write(self.fake.steam / "userdata/1234/config/localconfig.vdf", LAUNCH_OPTIONS.replace("STEAM_COMPAT_LIBRARY_PATHS=%s ", ""))
        result = self.chaseplane("apply")
        self.assertIn(f"put STEAM_COMPAT_LIBRARY_PATHS={self.library} before %command%", result.stdout)

    def test_the_launch_option_quotes_a_library_with_a_space(self):
        code = "import chaseplane; print(chaseplane.option_text('/mnt/My Games/steamapps/common/MSFS2024'))"
        result = subprocess.run([sys.executable, "-c", code], cwd=PARTS, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "STEAM_COMPAT_LIBRARY_PATHS='/mnt/My Games'", result.stderr)

    def test_chaseplane_apply_does_not_edit_steams_files_and_says_when_the_option_is_there(self):
        before = (self.fake.steam / "userdata/1234/config/localconfig.vdf").read_bytes()
        result = self.chaseplane("apply")
        self.assertEqual((self.fake.steam / "userdata/1234/config/localconfig.vdf").read_bytes(), before)
        self.assertIn(f"already hold STEAM_COMPAT_LIBRARY_PATHS={self.library}", result.stdout)

    def test_flow_apply_installs_the_stand_ins_and_nothing_for_the_bridge(self):
        result = self.flow("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.appresolver("AppResolverShim.dll").is_file())
        self.assertEqual(self.imports(), ["p42-install.reg"])

    def test_the_two_add_ons_share_one_set_of_stand_ins(self):
        self.chaseplane("apply")
        self.flow("apply")
        users = self.found.compatdata / "flightsim-on-linux-backup/parallel42/users"
        self.assertEqual(sorted(path.name for path in users.iterdir()), ["chaseplane", "flow"])

    def test_a_stand_in_that_was_there_before_is_kept_under_the_backup_folder(self):
        write(self.appresolver("toaststub.dll"), "older toast stub")
        self.chaseplane("apply")
        self.assertEqual((self.found.compatdata / "flightsim-on-linux-backup/parallel42/files/toaststub.dll").read_text(), "older toast stub")

    def test_a_second_apply_changes_nothing(self):
        self.chaseplane("apply")
        before = self.snapshot()
        self.chaseplane("apply")
        self.assertEqual(self.snapshot(), before)

    def test_apply_with_a_shim_that_cannot_be_built_stops_with_the_builders_message(self):
        (self.cache / "shims/toaststub.dll").unlink()
        stub(self.root / "bin/x86_64-w64-mingw32-gcc", "#!/bin/sh\nexit 1\n")
        result = self.flow("apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.appresolver("toaststub.dll").exists())


class UndoTest(P42Case):
    def test_undo_returns_every_file_to_what_it_was(self):
        write(self.appresolver("toaststub.dll"), "older toast stub")
        before = self.snapshot()
        self.chaseplane("apply")
        result = self.chaseplane("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.appresolver("AppResolverShim.dll").exists())
        self.assertFalse((self.found.compatdata / "flightsim-on-linux-backup").exists() and any((self.found.compatdata / "flightsim-on-linux-backup").iterdir()))

    def test_undo_removes_the_bridge_override_and_the_classes_when_nothing_else_uses_them(self):
        self.chaseplane("apply")
        self.chaseplane("undo")
        self.assertEqual(self.imports()[2:], ["bridge-remove.reg", "p42-remove.reg"])

    def test_undoing_one_of_two_leaves_the_stand_ins_for_the_other(self):
        self.chaseplane("apply")
        self.flow("apply")
        result = self.chaseplane("undo")
        self.assertIn("flow still uses them", result.stdout)
        self.assertTrue(self.appresolver("AppResolverShim.dll").is_file())
        self.assertNotIn("p42-remove.reg", self.imports())
        self.flow("undo")
        self.assertIn("p42-remove.reg", self.imports())
        self.assertFalse(self.appresolver("AppResolverShim.dll").exists())

    def test_undo_without_apply_changes_nothing(self):
        before = self.snapshot()
        result = self.flow("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.recorded(), [])

    def test_undo_says_the_steam_launch_option_is_left_to_the_reader(self):
        self.chaseplane("apply")
        result = self.chaseplane("undo")
        self.assertIn("left alone: the Steam launch options", result.stdout)


class ChaseplaneCheckTest(P42Case):
    def fixed(self):
        write(self.appresolver("AppResolverShim.dll"), "x")
        write(self.appresolver("toaststub.dll"), "x")
        write(self.prefix / "system.reg", SYSTEM_REG)
        write(self.prefix / "user.reg", USER_REG)

    def lines(self, result):
        return [line for line in result.stdout.splitlines() if line.startswith(("ok ", "FAIL "))]

    def fails_with(self, mutate, expected):
        self.fixed()
        mutate()
        result = self.chaseplane("check")
        failed = [line for line in self.lines(result) if line.startswith("FAIL ")]
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(len(failed), 1, result.stdout)
        self.assertIn(expected, failed[0])

    def test_a_fixed_machine_passes_all_eleven_lines(self):
        self.fixed()
        result = self.chaseplane("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(self.lines(result)), 11)

    def test_a_machine_nothing_was_applied_to_fails(self):
        result = self.chaseplane("check")
        self.assertEqual(result.returncode, 1)
        self.assertGreaterEqual(len([line for line in self.lines(result) if line.startswith("FAIL ")]), 3)

    def test_a_missing_resolver_dll_fails(self):
        self.fails_with(lambda: self.appresolver("AppResolverShim.dll").unlink(), "AppResolverShim.dll does not exist")

    def test_a_resolver_registered_somewhere_else_fails(self):
        self.fails_with(lambda: write(self.prefix / "system.reg", SYSTEM_REG.replace("C:/appresolver", "C:/elsewhere")), "CodeBase")

    def test_a_missing_toast_registration_fails(self):
        self.fails_with(lambda: write(self.prefix / "system.reg", SYSTEM_REG.split("[Software\\\\Microsoft")[0]), "DllPath")

    def test_a_bridge_with_httpapi_on_fails(self):
        self.fails_with(lambda: write(self.prefix / "user.reg", "WINE REGISTRY Version 2\n"), "httpapi for CP MSFS Bridge.exe is None")

    def test_a_missing_launch_option_fails_with_the_value_to_add(self):
        def mutate():
            write(self.fake.steam / "userdata/1234/config/localconfig.vdf", LAUNCH_OPTIONS.replace("STEAM_COMPAT_LIBRARY_PATHS=%s ", ""))
        self.fails_with(mutate, f"STEAM_COMPAT_LIBRARY_PATHS={self.library}")

    def test_a_launch_option_for_another_library_fails(self):
        self.fails_with(lambda: write(self.fake.steam / "userdata/1234/config/localconfig.vdf", LAUNCH_OPTIONS % "/somewhere/else"), "do not hold")

    def test_a_drive_s_that_points_at_steamapps_fails(self):
        def mutate():
            (self.prefix / "dosdevices/s:").unlink()
            (self.prefix / "dosdevices/s:").symlink_to(self.library / "steamapps")
        self.fails_with(mutate, "drive S: of the prefix is")

    def test_each_proton_patch_that_is_missing_fails_by_name(self):
        for patch in FIXES:
            with self.subTest(patch=patch):
                self.fixed()
                self.write_build(without=patch)
                result = self.chaseplane("check")
                failed = [line for line in self.lines(result) if line.startswith("FAIL ")]
                self.assertEqual(len(failed), 1, result.stdout)
                self.assertIn(patch, failed[0])

    def test_the_check_changes_nothing(self):
        self.fixed()
        before = self.snapshot()
        self.chaseplane("check")
        self.assertEqual(self.snapshot(), before)


class FlowCheckTest(P42Case):
    def test_a_fixed_prefix_passes_the_two_lines(self):
        write(self.appresolver("AppResolverShim.dll"), "x")
        write(self.appresolver("toaststub.dll"), "x")
        write(self.prefix / "system.reg", SYSTEM_REG)
        result = self.flow("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len([line for line in result.stdout.splitlines() if line.startswith("ok ")]), 2)

    def test_the_failures_name_flow_in_the_command_to_run(self):
        result = self.flow("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon apply flow", result.stdout)


class InstallTest(P42Case):
    def test_the_installer_runs_alone_in_the_prefix(self):
        setup = self.root / "ChasePlane - Experimental.exe"
        setup.write_text("MZ")
        result = self.chaseplane("install", FSOL_INSTALLER=str(setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"wine {setup}"])

    def test_the_flow_installer_does_the_same(self):
        setup = self.root / "Flow Pro.exe"
        setup.write_text("MZ")
        self.flow("install", FSOL_INSTALLER=str(setup))
        self.assertEqual(self.recorded(), [f"wine {setup}"])

    def test_without_a_file_it_says_how_to_give_one(self):
        result = self.chaseplane("install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("flightsim-on-linux addon install chaseplane", result.stderr)


class ManagerLaunchTest(P42Case):
    def wait_for(self, condition, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not condition():
            time.sleep(0.05)
        return condition()

    def test_a_missing_manager_says_how_to_install_it(self):
        result = self.chaseplane("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install chaseplane", result.stderr)

    def test_with_the_simulator_closed_the_manager_opens_through_protontricks_and_waits_for_the_wineserver(self):
        self.install_package(MANAGER_REL)
        result = self.chaseplane("launch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.wait_for(lambda: any(line.startswith("protontricks") for line in self.recorded())))
        exe = self.community / MANAGER_REL
        self.assertIn(f"protontricks --no-term -c wine {exe.as_posix().replace(' ', chr(92) + ' ')}; wineserver -w 2537590", self.recorded())

    def test_with_the_simulator_running_the_manager_opens_in_its_container(self):
        self.install_package(MANAGER_REL)
        self.chaseplane("launch", FSOL_SIM_RUNNING="1")
        self.assertTrue(self.wait_for(lambda: any(line.startswith("cli ") for line in self.recorded())))
        self.assertEqual([line for line in self.recorded() if line.startswith("cli")], [f"cli run Z:{str(self.community / MANAGER_REL).replace('/', chr(92))}"])
        self.assertFalse(any(line.startswith("protontricks") for line in self.recorded()))

    def test_without_protontricks_it_says_so_and_starts_nothing(self):
        self.install_package(MANAGER_REL)
        (self.root / "bin/protontricks").unlink()
        result = self.chaseplane("launch", PATH=f"{self.root / 'bin'}:/usr/bin:/bin")
        if Path("/usr/bin/protontricks").exists() or Path("/bin/protontricks").exists():
            self.skipTest("protontricks is installed on this machine")
        self.assertEqual(result.returncode, 1)
        self.assertIn("protontricks is missing", result.stderr)

    def test_the_flow_manager_opens_the_same_way(self):
        self.install_package(FLOW_REL)
        self.flow("launch")
        self.assertTrue(self.wait_for(lambda: any(line.startswith("protontricks") for line in self.recorded())))
        self.assertIn("wineserver -w 2537590", self.recorded()[0])

    def test_the_log_is_private(self):
        self.install_package(MANAGER_REL)
        self.chaseplane("launch")
        log = self.cache / "logs/chaseplane.log"
        self.assertTrue(self.wait_for(log.exists))
        self.assertEqual(oct(log.stat().st_mode & 0o777), "0o600")


SIM_ENVIRONMENT = {
    "WINEPREFIX": "/sim/pfx",
    "WINEESYNC": "1",
    "WINEFSYNC": "1",
    "PROTON_NO_NTSYNC": "1",
    "WAYLAND_DISPLAY": "wayland-7",
    "DISPLAY": ":3",
    "XAUTHORITY": "/run/sim-cookie",
    "PROTON_ENABLE_WAYLAND": "1",
    "WINEDLLPATH": "/sim/proton/files/lib/nvml:/sim/proton/files/lib/wine",
    "WINEDLLOVERRIDES": "d3d11=n,b;nvapi=n",
    "LD_PRELOAD": "/overlay.so",
}
CLI_STUB = """#!/usr/bin/env python3
import functools
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, %(root)r)
from fsol import cli, container, machine

with open(%(record)r, "a") as record:
    record.write("cli " + " ".join(sys.argv[1:]) + "\\n")
proc = Path(%(proc)r)
found = machine.discover(home=Path(%(home)r), steam_override="", proc=proc, dev=Path(%(dev)r), runtime_dir=Path(%(runtime)r), drm=Path(%(drm)r), env={})
with mock.patch.object(cli.machine, "discover", return_value=found), mock.patch.object(container, "simulator_values", functools.partial(container.simulator_values, proc=proc)):
    sys.exit(cli.main(sys.argv[1:]))
"""
WINE_ENV_PATTERN = "^(WINE|PROTON|DISPLAY|WAYLAND|XAUTHORITY|XDG_RUNTIME|NoDefault|LD_PRELOAD)"


class BridgeLaunchTest(P42Case):
    def setUp(self):
        super().setUp()
        stub(self.root / "cli", CLI_STUB % {"root": str(ROOT), "record": str(self.record), "proc": str(self.fake.proc), "home": str(self.fake.home), "dev": str(self.fake.dev), "runtime": str(self.fake.runtime), "drm": str(self.fake.drm)})

    def wine_stub(self):
        return super().wine_stub() + f'env | grep -E "{WINE_ENV_PATTERN}" | sort > "{self.record}.wine-env"\n'

    def wait_for(self, condition, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not condition():
            time.sleep(0.05)
        return condition()

    def run_simulator(self, **changes):
        environ = {**SIM_ENVIRONMENT, **changes}
        self.fake.run_sim(environ={name: value for name, value in environ.items() if value is not None})

    def start_bridge(self, **changes):
        self.install_package(BRIDGE_REL)
        self.run_simulator(**changes)
        result = self.chaseplane("launch-bridge", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.wait_for(Path(f"{self.record}.wine-env").exists))
        lines = Path(f"{self.record}.wine-env").read_text().splitlines()
        return dict(line.split("=", 1) for line in lines)

    def calls(self, prefix):
        return [line for line in self.recorded() if line.startswith(prefix)]

    def test_a_closed_simulator_stops_it_with_a_notice(self):
        self.install_package(BRIDGE_REL)
        result = self.chaseplane("launch-bridge")
        self.assertEqual(result.returncode, 1)
        self.assertIn("start Microsoft Flight Simulator 2024 first", result.stderr)
        self.assertEqual(self.calls("cli"), [])

    def test_a_missing_bridge_says_how_to_install_it(self):
        result = self.chaseplane("launch-bridge", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install chaseplane", result.stderr)

    def test_it_starts_the_bridge_with_the_hosts_wine_and_the_uri_argument_not_in_the_container(self):
        self.start_bridge()
        exe = self.community / BRIDGE_REL
        self.assertEqual(self.calls("wine"), [f"wine {exe} chaseplanemsfs24://"])
        self.assertEqual(self.calls("cli"), ["cli sim-env WINEPREFIX WINEESYNC WINEFSYNC PROTON_NO_NTSYNC WAYLAND_DISPLAY DISPLAY XAUTHORITY PROTON_ENABLE_WAYLAND WINEDLLPATH WINEDLLOVERRIDES"])

    def test_it_turns_httpapi_off_after_the_overrides_the_simulator_has(self):
        started = self.start_bridge()
        self.assertEqual(started["WINEDLLOVERRIDES"], "d3d11=n,b;nvapi=n;winex11.drv=d;winewayland.drv=b;httpapi=d")

    def test_it_copies_the_prefix_the_sync_mode_and_the_display_from_the_simulator(self):
        started = self.start_bridge()
        for name in ("WINEPREFIX", "WINEESYNC", "WINEFSYNC", "PROTON_NO_NTSYNC", "WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY"):
            self.assertEqual(started[name], SIM_ENVIRONMENT[name], name)

    def test_it_copies_the_library_path_of_the_simulator(self):
        started = self.start_bridge()
        self.assertEqual(started["WINEDLLPATH"], SIM_ENVIRONMENT["WINEDLLPATH"])

    def test_it_does_not_carry_the_preload_over(self):
        started = self.start_bridge()
        self.assertNotIn("LD_PRELOAD", started)

    def test_the_drivers_are_swapped_when_the_simulator_is_not_on_wayland(self):
        started = self.start_bridge(PROTON_ENABLE_WAYLAND=None)
        self.assertTrue(started["WINEDLLOVERRIDES"].endswith("winewayland.drv=d;winex11.drv=b;httpapi=d"))

    def test_a_value_the_simulator_lacks_is_not_set_at_all(self):
        started = self.start_bridge(DISPLAY=None, XAUTHORITY=None, WINEDLLPATH=None)
        self.assertNotIn("DISPLAY", started)
        self.assertNotIn("XAUTHORITY", started)
        self.assertEqual(started["WINEDLLPATH"], f"{self.proton}/files/lib/vkd3d:{self.proton}/files/lib/wine")

    def test_without_a_prefix_in_the_simulator_environment_the_one_found_is_used(self):
        started = self.start_bridge(WINEPREFIX=None)
        self.assertEqual(started["WINEPREFIX"], str(self.prefix))

    def test_a_simulator_that_is_gone_starts_nothing(self):
        self.install_package(BRIDGE_REL)
        result = self.chaseplane("launch-bridge", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("the simulator is not running", result.stderr)
        time.sleep(0.3)
        self.assertEqual(self.calls("wine"), [])

    def test_it_never_names_a_display_value(self):
        text = (CHASEPLANE / "launch-bridge").read_text()
        self.assertNotRegex(text, r"(WAYLAND_DISPLAY|DISPLAY|XAUTHORITY)=")
        self.assertNotRegex(text, r"wayland-\d")

    def test_the_log_is_private(self):
        self.start_bridge()
        log = self.cache / "logs/chaseplane-bridge.log"
        self.assertEqual(oct(log.stat().st_mode & 0o777), "0o600")

    def test_a_bridge_already_running_is_left_alone(self):
        self.install_package(BRIDGE_REL)
        self.run_simulator()
        process = start_stand_in(self.root, self.token, "Q:\\pilot\\Community\\p42-util-chaseplane\\Modules\\ChasePlane\\CP MSFS Bridge.exe")
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        time.sleep(0.2)
        result = self.chaseplane("launch-bridge", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0)
        time.sleep(0.3)
        self.assertEqual(self.calls("cli") + self.calls("wine"), [])


if __name__ == "__main__":
    unittest.main()
