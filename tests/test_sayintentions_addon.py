import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fsol import addons, watcher
from tests.support import process_stubs, start_stand_in, stub
from tests.test_gsx_addon import sha256
from tests.test_machine import FakeMachine, write

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addons" / "sayintentions"
PARTS = ADDON / "parts"
sys.path.insert(0, str(PARTS))

import sayintentions  # noqa: E402

OPTIONS = "--in-process-gpu --disable-gpu --disable-gpu-compositing --disable-software-rasterizer"
HUB_STAND_IN = r"Q:\Program Files\SayIntentions.AI\SayIntentionsUpdater3.exe"
SIMULATOR_STAND_IN = r"Q:\steamapps\common\MSFS2024\FlightSimulator2024.exe"
HUB = "C:\\Program Files\\SayIntentions.AI\\SayIntentionsUpdater3.exe"
HUB_REL = "drive_c/Program Files/SayIntentions.AI/SayIntentionsUpdater3.exe"
APP_REL = "drive_c/Program Files/SayIntentions.AI/SayIntentionsAI"
LOADER = "WebView2Loader.dll"
SI_LINK = "C:\\Program Files\\SayIntentions.AI\\SayIntentionsAI\\efb\\sayintentions-efb".encode("utf-16-le")
GSX_LINK = "C:\\Program Files (x86)\\Addon Manager\\MSFS\\fsdreamteam-gsx-pro".encode("utf-16-le")
CLR_4_8 = b"junk" + "ProductVersion".encode("utf-16-le") + b"\0\0" + "4.8.9037.0".encode("utf-16-le") + b"\0\0"
CLR_4_0 = b"junk" + "ProductVersion".encode("utf-16-le") + b"\0\0" + "4.0.30319.1".encode("utf-16-le") + b"\0\0"
FONT_NAMES = ("Tahoma (TrueType)", "Tahoma Bold (TrueType)", "Marlett (TrueType)", "Symbol (TrueType)", "Webdings (TrueType)", "Wingdings (TrueType)")
BASE_USER_REG = "WINE REGISTRY Version 2\n\n"
BASE_SYSTEM_REG = "WINE REGISTRY Version 2\n\n"
FIXES = {
    "win32u-cross-process-child-windows.patch": "lib/wine/x86_64-unix/win32u.so",
    "winewayland-opaque-xrgb.patch": "lib/wine/x86_64-unix/winewayland.so",
    "ntdll-heap-bounded-free-list-scan.patch": "lib/wine/x86_64-windows/ntdll.dll",
}
MMDEVAPI = "lib/wine/x86_64-windows/mmdevapi.dll"


def reg_section(key, values):
    body = "".join(f'"{name}"="{value}"\n' for name, value in values.items())
    return f"[{key.replace(chr(92), chr(92) * 2)}] 1792018790\n{body}\n"


def fixed_user_reg(telemetry=True):
    text = BASE_USER_REG
    text += reg_section(r"Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments", {"SayIntentionsAI.exe": OPTIONS})
    text += reg_section(r"Software\Wine\AppDefaults\msedgewebview2.exe", {"Version": "win7"})
    if telemetry:
        text += reg_section(r"Software\Wine\AppDefaults\si_telemetry.exe\DllOverrides", {"mscoree": "native"})
    return text


def fixed_system_reg():
    return BASE_SYSTEM_REG + reg_section(sayintentions.FONTS_KEY, {name: file for name, file in sayintentions.FONTS.items()})


class RestoreLinksTest(unittest.TestCase):
    def test_a_link_that_cannot_be_given_back_stays_in_the_stash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stub(root / "bin" / "setfattr", "exit 1\n")
            write(root / "backup/links/fss-link", "dmFsdWU=")
            (root / "community").mkdir()
            script = f'. "{ROOT}/addons/sayintentions/parts/links.sh"; si_restore_links'
            env = {**os.environ, "PATH": f"{root}/bin:{os.environ['PATH']}", "SI_BACKUP": str(root / "backup"), "FSOL_COMMUNITY": str(root / "community")}
            result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            self.assertIn("could not give back fss-link", result.stderr)
            self.assertTrue((root / "backup/links/fss-link").exists())


class RegistryPlanTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.prefix = self.root / "pfx"
        write(self.prefix / "user.reg", BASE_USER_REG)
        write(self.prefix / "system.reg", BASE_SYSTEM_REG)
        self.record = self.root / "record.json"
        self.out = self.root / "out.reg"

    def tearDown(self):
        self.temp.cleanup()

    def put_clr(self, content, folder="Framework"):
        path = self.prefix / "drive_c/windows/Microsoft.NET" / folder / "v4.0.30319/clr.dll"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def apply_plan(self):
        return sayintentions.plan_apply(self.prefix, self.record, self.out)

    def text(self):
        return self.out.read_bytes().decode("latin-1")

    def test_an_empty_prefix_gets_the_policy_the_version_and_the_six_fonts_but_no_telemetry_override(self):
        count = self.apply_plan()
        text = self.text()
        self.assertEqual(count, 8)
        self.assertIn(f'[HKEY_CURRENT_USER\\Software\\Policies\\Microsoft\\Edge\\WebView2\\AdditionalBrowserArguments]\r\n"SayIntentionsAI.exe"="{OPTIONS}"', text)
        self.assertIn('[HKEY_CURRENT_USER\\Software\\Wine\\AppDefaults\\msedgewebview2.exe]\r\n"Version"="win7"', text)
        self.assertNotIn("si_telemetry", text)
        self.assertNotIn("mscoree", text)
        for name, file in sayintentions.FONTS.items():
            self.assertIn(f'"{name}"="{file}"', text)
        self.assertTrue(text.startswith("REGEDIT4\r\n"))

    def test_with_dotnet_4_8_in_the_prefix_the_telemetry_program_gets_the_native_mscoree(self):
        self.put_clr(CLR_4_8)
        self.apply_plan()
        self.assertIn('[HKEY_CURRENT_USER\\Software\\Wine\\AppDefaults\\si_telemetry.exe\\DllOverrides]\r\n"mscoree"="native"', self.text())

    def test_dotnet_4_8_in_the_64_bit_folder_alone_counts(self):
        self.put_clr(CLR_4_8, "Framework64")
        self.assertTrue(sayintentions.net48_present(self.prefix))

    def test_an_older_dotnet_does_not_get_the_override(self):
        self.put_clr(CLR_4_0)
        self.assertFalse(sayintentions.net48_present(self.prefix))
        self.apply_plan()
        self.assertNotIn("si_telemetry", self.text())

    def test_values_that_already_match_are_not_written_again(self):
        write(self.prefix / "user.reg", fixed_user_reg(telemetry=False))
        write(self.prefix / "system.reg", fixed_system_reg())
        self.assertEqual(self.apply_plan(), 0)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.record.exists())

    def test_a_policy_with_other_options_is_overwritten_and_the_old_text_is_remembered(self):
        write(self.prefix / "user.reg", BASE_USER_REG + reg_section(r"Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments", {"SayIntentionsAI.exe": "--remote-debugging-port=9333"}))
        self.apply_plan()
        remembered = json.loads(self.record.read_text())
        self.assertIn({"hive": "HKCU", "key": sayintentions.POLICY.key, "name": "SayIntentionsAI.exe", "previous": "--remote-debugging-port=9333"}, remembered)

    def test_the_undo_plan_gives_back_old_values_and_deletes_the_ones_that_were_absent(self):
        write(self.prefix / "user.reg", BASE_USER_REG + reg_section(r"Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments", {"SayIntentionsAI.exe": "--remote-debugging-port=9333"}))
        self.apply_plan()
        out = self.root / "undo.reg"
        self.assertGreater(sayintentions.plan_undo(self.record, out), 0)
        text = out.read_bytes().decode("latin-1")
        self.assertIn('"SayIntentionsAI.exe"="--remote-debugging-port=9333"', text)
        self.assertIn('"Version"=-', text)
        self.assertIn('"Tahoma (TrueType)"=-', text)

    def test_a_second_plan_keeps_the_first_previous_value(self):
        write(self.prefix / "user.reg", BASE_USER_REG + reg_section(r"Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments", {"SayIntentionsAI.exe": "first"}))
        self.apply_plan()
        write(self.prefix / "user.reg", BASE_USER_REG + reg_section(r"Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments", {"SayIntentionsAI.exe": "second"}))
        self.apply_plan()
        policy = [item for item in json.loads(self.record.read_text()) if item["name"] == "SayIntentionsAI.exe"]
        self.assertEqual([item["previous"] for item in policy], ["first"])

    def test_text_with_backslashes_and_quotes_is_escaped(self):
        self.assertEqual(sayintentions.reg_string('a\\b"c'), '"a\\\\b\\"c"')


class SiCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.token = str(self.root)
        self.fake = FakeMachine(self.root).build()
        self.found = self.fake.discover()
        self.prefix = self.found.prefix
        self.proton = self.fake.tool
        self.community = self.found.community
        self.record = self.root / "record"
        self.processes = []
        self.cache = self.root / "cache"
        write(self.prefix / HUB_REL, "MZ")
        write(self.prefix / APP_REL / "runtimes/win-x64/native" / LOADER, "loader of the package")
        write(self.prefix / "user.reg", BASE_USER_REG)
        write(self.prefix / "system.reg", BASE_SYSTEM_REG)
        (self.fake.games / "steamapps/common/Proton Voice Files").mkdir(parents=True)
        self.write_build()
        stub(self.root / "bin/notify-send", f'#!/bin/sh\nprintf "notice %s\\n" "$*" >> "{self.record}"\n')
        stub(self.proton / "files/bin/wine", self.wine_stub())
        stub(self.proton / "files/bin/wineserver", "#!/bin/sh\nexit 0\n")
        process_stubs(self.root / "bin", self.token)
        stub(self.root / "cli", f'#!/bin/sh\nprintf "cli %s\\n" "$*" >> "{self.record}"\nprintf "%s\\n" "$EXTRA_WINEDLLOVERRIDES|$VK_LOADER_DRIVERS_SELECT" >> "{self.record}.env"\n')

    def tearDown(self):
        for process in self.processes:
            process.kill()
            process.wait()
            if process.stdout:
                process.stdout.close()
        self.temp.cleanup()

    def wine_stub(self):
        return (
            "#!/bin/sh\n"
            f'printf "wine %s\\n" "$*" >> "{self.record}"\n'
            f'printf "%s\\n" "$WINEDLLOVERRIDES" >> "{self.record}.env"\n'
            'if [ "$1" = regedit ]; then\n'
            '  file=$(printf "%s" "$3" | sed "s/^Z://; s/\\\\\\\\/\\//g")\n'
            f'  cp "$file" "{self.record}.reg.$(date +%s%N)"\n'
            "fi\n"
        )

    def write_build(self, without=None, mmdevapi_hash=None, overlay=True):
        lines = ["release cachyos-11.0-20261005-slr x86_64"]
        if overlay:
            lines.append("overlay fenix-a320-linux-patch v0.1.0-preview.7 cachyos-11")
        for patch, module in FIXES.items():
            if patch != without:
                lines.append(f"patch {sha256((ROOT / 'patches/wine' / patch).read_bytes())} {patch}")
        for module in sorted(set(FIXES.values()) | {MMDEVAPI}):
            if not (self.proton / "files" / module).exists():
                write(self.proton / "files" / module, module)
            recorded = mmdevapi_hash if module == MMDEVAPI and mmdevapi_hash else sha256((self.proton / "files" / module).read_bytes())
            lines.append(f"module {recorded} {module}")
        (self.proton / "fs2024-build.txt").write_text("\n".join(lines) + "\n")

    def environment(self, **extra):
        env = {
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "FSOL_PREFIX": str(self.prefix),
            "FSOL_COMPATDATA": str(self.found.compatdata),
            "FSOL_PROTON": str(self.proton),
            "FSOL_BASE": str(self.found.base),
            "FSOL_COMMUNITY": str(self.community),
            "FSOL_STEAM": str(self.fake.steam),
            "FSOL_CACHE": str(self.cache),
            "FSOL_CLI": str(self.root / "cli"),
            "FSOL_ADDON": "sayintentions",
            "FSOL_TEST_TOKEN": self.token,
            "FSOL_SIM_RUNNING": "0",
            "FSOL_LINKS_POLL": "0.1",
        }
        env.update(extra)
        return env

    def script(self, name, *args, **extra):
        return subprocess.run([str(ADDON / name), *args], env=self.environment(**extra), capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def recorded(self):
        return self.record.read_text().splitlines() if self.record.exists() else []

    def imported(self):
        return [path.read_bytes().decode("latin-1") for path in sorted(self.root.glob("record.reg.*"))]

    def app(self, name):
        return self.prefix / APP_REL / name

    def snapshot(self):
        return {str(path.relative_to(self.root)): sha256(path.read_bytes()) for path in sorted(self.root.rglob("*")) if path.is_file() and not path.is_symlink() and not path.name.startswith("record") and "cache" not in path.parts}

    def put_clr(self, content):
        path = self.prefix / "drive_c/windows/Microsoft.NET/Framework/v4.0.30319/clr.dll"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


class ShapeTest(SiCase):
    def test_the_manifest_is_complete(self):
        addon = addons.load(ADDON)
        self.assertEqual((addon.id, addon.needs, addon.tested_version, addon.tested_on), ("sayintentions", ["proton-fs2024", "simulator-closed"], "5.6.5.4", "2026-10-21"))
        self.assertTrue(addon.touches_addon_files)
        self.assertTrue(addon.can_install)
        self.assertEqual(addon.install, {"from": "https://www.SayIntentions.AI", "paid": None, "kind": "installer"})
        self.assertEqual([shortcut["title"] for shortcut in addon.shortcuts], ["SayIntentions.AI"])

    def test_the_manifest_has_a_loop_watcher_that_runs_the_links_script(self):
        addon = addons.load(ADDON)
        self.assertEqual(addon.watcher, {"loop": "links-watcher"})
        self.assertTrue(addon.has_script("links-watcher"))
        self.assertFalse((PARTS / "links-watcher.service.in").exists())

    def test_nothing_of_the_owners_machine_reaches_a_script(self):
        text = "".join(path.read_text() for path in [ADDON / "launch", ADDON / "apply", ADDON / "install", ADDON / "links-watcher"] + sorted(PARTS.glob("*.sh")))
        for forbidden in ("VK_LOADER_DRIVERS_SELECT", "radeon", "patch-mmdevapi", "mmdevapi=n", "game ", "/home/"):
            self.assertNotIn(forbidden, text)

    def test_the_unit_the_cli_writes_makes_a_unit_systemd_accepts(self):
        analyzer = shutil.which("systemd-analyze")
        if analyzer is None:
            self.skipTest("systemd-analyze is not installed")
        unit = self.root / "flightsim-on-linux-sayintentions.service"
        cli = ROOT / "bin" / "flightsim-on-linux"
        unit.write_text(watcher.unit_text(addons.load(ADDON), cli))
        result = subprocess.run([analyzer, "--user", "verify", str(unit)], capture_output=True, text=True, env={**os.environ, "HOME": str(self.root / "home")})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr.strip(), "")

    def test_the_unit_runs_the_links_script_through_the_cli_and_the_scripts_enable_nothing_by_themselves(self):
        text = watcher.unit_text(addons.load(ADDON), Path("/opt/fsol/bin/flightsim-on-linux"))
        self.assertIn('ExecStart="/opt/fsol/bin/flightsim-on-linux" addon launch sayintentions links-watcher', text)
        self.assertNotIn("KillMode=process", text)
        for name in ("apply", "install", "launch"):
            self.assertNotIn("systemctl", (ADDON / name).read_text())


class ApplyTest(SiCase):
    def test_apply_imports_one_registry_file_with_the_settings_and_copies_the_loader(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.imported()[0]
        self.assertIn(f'"SayIntentionsAI.exe"="{OPTIONS}"', text)
        self.assertIn('"Version"="win7"', text)
        for name in FONT_NAMES:
            self.assertIn(f'"{name}"=', text)
        self.assertEqual(self.app(LOADER).read_text(), "loader of the package")

    def test_the_telemetry_override_is_written_only_with_dotnet_4_8(self):
        self.script("apply")
        self.assertNotIn("si_telemetry", self.imported()[0])
        self.put_clr(CLR_4_8)
        self.script("apply")
        self.assertIn('"mscoree"="native"', self.imported()[1])

    def test_apply_does_not_run_the_hub_the_links_script_or_systemctl(self):
        self.script("apply")
        self.assertEqual([line for line in self.recorded() if not line.startswith("wine regedit")], [])

    def test_a_second_apply_imports_nothing_and_changes_nothing(self):
        self.script("apply")
        write(self.prefix / "user.reg", fixed_user_reg(telemetry=False))
        write(self.prefix / "system.reg", fixed_system_reg())
        before = self.snapshot()
        imports = len(self.imported())
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.imported()), imports)
        self.assertIn("already every registry setting", result.stdout)

    def test_a_loader_that_differs_from_the_package_is_replaced_and_the_old_one_kept(self):
        write(self.app(LOADER), "older loader")
        self.script("apply")
        self.assertEqual(self.app(LOADER).read_text(), "loader of the package")
        self.assertEqual((self.found.compatdata / "flightsim-on-linux-backup/sayintentions" / LOADER).read_text(), "older loader")

    def test_without_the_package_the_loader_step_waits_for_the_install(self):
        shutil.rmtree(self.prefix / APP_REL)
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("run apply again after the install", result.stdout)

    def test_apply_without_dotnet_4_8_points_at_the_base_add_on(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("flightsim-on-linux addon apply dotnet48", result.stdout)

    def test_apply_with_dotnet_4_8_does_not_point_at_the_base_add_on(self):
        self.put_clr(CLR_4_8)
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("dotnet48", result.stdout)

    def test_apply_says_what_it_leaves_to_doctor_and_to_the_links_watcher(self):
        result = self.script("apply")
        self.assertIn("flightsim-on-linux doctor", result.stdout)
        self.assertIn("links watcher", result.stdout)


class UndoTest(SiCase):
    def test_undo_restores_the_registry_through_one_import_and_the_loader_to_absent(self):
        before = self.snapshot()
        self.script("apply")
        imports = len(self.imported())
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.imported()), imports + 1)
        undo = self.imported()[-1]
        self.assertIn('"Version"=-', undo)
        self.assertIn('"Tahoma (TrueType)"=-', undo)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.app(LOADER).exists())

    def test_undo_puts_back_an_older_policy_text(self):
        write(self.prefix / "user.reg", BASE_USER_REG + reg_section(r"Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments", {"SayIntentionsAI.exe": "--remote-debugging-port=9333"}))
        self.script("apply")
        self.script("undo")
        self.assertIn('"SayIntentionsAI.exe"="--remote-debugging-port=9333"', self.imported()[-1])

    def test_undo_restores_an_older_loader(self):
        write(self.app(LOADER), "older loader")
        self.script("apply")
        self.script("undo")
        self.assertEqual(self.app(LOADER).read_text(), "older loader")

    def test_undo_without_apply_changes_nothing(self):
        before = self.snapshot()
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.recorded(), [])


class CheckTest(SiCase):
    def fixed(self):
        write(self.prefix / "user.reg", fixed_user_reg())
        write(self.prefix / "system.reg", fixed_system_reg())
        write(self.app(LOADER), "loader of the package")
        self.put_clr(CLR_4_8)

    def lines(self, result):
        return [line for line in result.stdout.splitlines() if line.startswith(("ok ", "FAIL "))]

    def fails_with(self, mutate, expected):
        self.fixed()
        mutate()
        result = self.script("check")
        failed = [line for line in self.lines(result) if line.startswith("FAIL ")]
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(len(failed), 1, result.stdout)
        self.assertIn(expected, failed[0])

    def test_a_fixed_machine_passes_all_ten_lines(self):
        self.fixed()
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(self.lines(result)), 10)

    def test_a_machine_nothing_was_applied_to_fails(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        text = "\n".join(self.lines(result))
        for part in ("SayIntentionsAI.exe under", "Version", ".NET Framework 4.8", "WebView2Loader.dll", "fonts registry key lacks"):
            self.assertIn(part, text)
        self.assertIn("flightsim-on-linux addon apply dotnet48", text)
        self.assertNotIn("apply the Fenix add-on", text)

    def test_a_policy_with_other_options_fails(self):
        self.fails_with(lambda: write(self.prefix / "user.reg", fixed_user_reg().replace(OPTIONS, "--other")), "SayIntentionsAI.exe under")

    def test_a_missing_windows_version_for_the_browser_fails(self):
        self.fails_with(lambda: write(self.prefix / "user.reg", fixed_user_reg().replace('"Version"="win7"', '"Version"="win10"')), "msedgewebview2.exe")

    def test_a_telemetry_override_that_is_missing_fails(self):
        self.fails_with(lambda: write(self.prefix / "user.reg", fixed_user_reg(telemetry=False)), "mscoree")

    def test_a_prefix_without_dotnet_4_8_fails_and_says_what_stays_on_screen(self):
        self.fails_with(lambda: self.put_clr(CLR_4_0), "interface stays on its loading screen")

    def test_a_loader_that_is_not_beside_the_exe_fails(self):
        self.fails_with(lambda: self.app(LOADER).unlink(), "WebView2Loader.dll is not beside SayIntentionsAI.exe")

    def test_a_loader_that_differs_from_the_package_fails(self):
        self.fails_with(lambda: write(self.app(LOADER), "stale"), "WebView2Loader.dll is not beside")

    def test_each_missing_font_fails_by_name(self):
        for name in FONT_NAMES:
            with self.subTest(font=name):
                self.fixed()
                write(self.prefix / "system.reg", fixed_system_reg().replace(f'"{name}"="{sayintentions.FONTS[name]}"\n', ""))
                result = self.script("check")
                failed = [line for line in self.lines(result) if line.startswith("FAIL ")]
                self.assertEqual(len(failed), 1, result.stdout)
                self.assertIn(name, failed[0])

    def test_each_proton_patch_that_is_missing_fails_by_name(self):
        for patch in FIXES:
            with self.subTest(patch=patch):
                self.fixed()
                self.write_build(without=patch)
                result = self.script("check")
                failed = [line for line in self.lines(result) if line.startswith("FAIL ")]
                self.assertEqual(len(failed), 1, result.stdout)
                self.assertIn(patch, failed[0])

    def test_the_heap_patch_line_says_expected_not_seen(self):
        self.fixed()
        result = self.script("check")
        heap = [line for line in self.lines(result) if "free list" in line]
        self.assertEqual(len(heap), 1)
        self.assertIn("expected", heap[0])
        self.assertIn("not yet seen in flight", heap[0])

    def test_a_mmdevapi_that_is_not_the_recorded_one_fails(self):
        self.fails_with(lambda: self.write_build(mmdevapi_hash="0" * 64), "mmdevapi.dll")

    def test_a_proton_build_without_the_overlay_fails_the_mmdevapi_line(self):
        self.fails_with(lambda: self.write_build(overlay=False), "mmdevapi.dll")

    def test_missing_proton_voice_files_fail(self):
        self.fails_with(lambda: shutil.rmtree(self.fake.games / "steamapps/common/Proton Voice Files"), "Proton Voice Files (app 3086180)")

    def test_the_check_changes_nothing(self):
        self.fixed()
        before = self.snapshot()
        self.script("check")
        self.assertEqual(self.snapshot(), before)


class InstallTest(SiCase):
    def setUp(self):
        super().setUp()
        self.setup = self.root / "SayIntentionsAI_UpdateHub.exe"
        self.setup.write_text("MZ")

    def test_it_refuses_while_the_links_watcher_is_not_running(self):
        result = self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 1)
        self.assertIn("start the links watcher first", result.stderr)
        self.assertFalse(any(line.startswith("wine") for line in self.recorded()))

    def test_with_the_watcher_running_the_hub_installer_runs_alone_with_direct3d_9_off(self):
        self.run_watcher_stand_in()
        result = self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line for line in self.recorded() if line.startswith("wine")], [f"wine {self.setup}"])
        self.assertIn("d3d9=d", Path(f"{self.record}.env").read_text())

    def run_watcher_stand_in(self):
        self.processes.append(start_stand_in(self.root, self.token, str(ADDON / "links-watcher")))
        time.sleep(0.2)

    def test_without_a_file_it_says_how_to_give_one(self):
        result = self.script("install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("flightsim-on-linux addon install sayintentions", result.stderr)


class LinkCase(SiCase):
    def setUp(self):
        super().setUp()
        probe = self.root / "probe"
        probe.mkdir()
        try:
            os.setxattr(probe, "user.WINEREPARSE", b"x")
        except OSError:
            self.skipTest("this file system does not keep user extended attributes")
        self.si_efb = self.make_link("sayintentions-efb", SI_LINK)
        self.si_core = self.make_link("SayIntentions-SimObjects-Core", SI_LINK)
        self.gsx = self.make_link("fsdreamteam-gsx-pro", GSX_LINK)
        (self.community / "plain-package").mkdir()
        write(self.community / "plain-package/layout.json", "{}")

    def make_link(self, name, value):
        folder = self.community / name
        folder.mkdir()
        os.setxattr(folder, "user.WINEREPARSE", value)
        return folder

    def stand_in(self, argv0, seconds=60):
        process = start_stand_in(self.root, self.token, argv0, seconds)
        self.processes.append(process)
        time.sleep(0.2)
        return process

    def wait_for(self, condition, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not condition():
            time.sleep(0.05)
        return condition()

    def names(self):
        return sorted(path.name for path in self.community.iterdir())

    def stash(self):
        folder = self.found.compatdata / "flightsim-on-linux-backup/sayintentions/links"
        return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []

    def start_watcher(self, **extra):
        process = subprocess.Popen([str(ADDON / "links-watcher")], env=self.environment(**extra), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.processes.append(process)
        return process


class LaunchTest(LinkCase):
    def test_a_closed_simulator_stops_it_with_a_notice(self):
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("start Microsoft Flight Simulator 2024 first", result.stderr)
        self.assertFalse(any(line.startswith("cli") for line in self.recorded()))

    def test_a_hub_that_is_not_installed_says_how_to_install_it(self):
        (self.prefix / HUB_REL).unlink()
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install sayintentions", result.stderr)

    def test_it_removes_the_sayintentions_links_and_only_those_then_opens_the_hub_in_the_container_with_direct3d_9_off(self):
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.wait_for(lambda: Path(f"{self.record}.env").exists()))
        self.assertEqual(self.names(), ["fsdreamteam-gsx-pro", "plain-package"])
        self.assertEqual([line for line in self.recorded() if line.startswith("cli")], [f"cli run {HUB}"])
        self.assertEqual(Path(f"{self.record}.env").read_text().strip(), "d3d9=d|")
        self.assertEqual(os.getxattr(self.gsx, "user.WINEREPARSE"), GSX_LINK)
        self.assertTrue((self.community / "plain-package/layout.json").is_file())

    def test_a_hub_already_running_leaves_the_links_alone(self):
        self.stand_in(HUB_STAND_IN)
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0)
        self.assertIn("sayintentions-efb", self.names())
        self.assertFalse(any(line.startswith("cli") for line in self.recorded()))

    def test_a_link_folder_that_is_not_empty_is_reported_and_kept(self):
        write(self.si_efb / "inside.txt", "x")
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sayintentions-efb", self.names())
        self.assertIn("could not remove", result.stderr)

    def test_its_output_goes_to_a_private_log(self):
        self.script("launch", FSOL_SIM_RUNNING="1")
        log = self.cache / "logs/sayintentions.log"
        self.assertTrue(self.wait_for(log.exists))
        self.assertEqual(oct(log.stat().st_mode & 0o777), "0o600")


class LinksWatcherTest(LinkCase):
    def test_nothing_happens_while_the_hub_is_not_running(self):
        process = self.start_watcher()
        time.sleep(0.8)
        self.assertEqual(self.names(), sorted(["fsdreamteam-gsx-pro", "plain-package", "sayintentions-efb", "SayIntentions-SimObjects-Core"]))
        self.assertIsNone(process.poll())

    def test_with_the_hub_open_and_the_simulator_closed_the_links_are_kept_and_removed_and_nothing_else(self):
        self.start_watcher()
        self.stand_in(HUB_STAND_IN)
        self.assertTrue(self.wait_for(lambda: self.names() == ["fsdreamteam-gsx-pro", "plain-package"]))
        self.assertEqual(self.stash(), ["SayIntentions-SimObjects-Core", "sayintentions-efb"])
        self.assertEqual(os.getxattr(self.gsx, "user.WINEREPARSE"), GSX_LINK)

    def test_when_the_hub_closes_the_links_come_back_with_the_same_value(self):
        self.start_watcher()
        hub = self.stand_in(HUB_STAND_IN)
        self.assertTrue(self.wait_for(lambda: len(self.stash()) == 2))
        hub.kill()
        hub.wait()
        self.assertTrue(self.wait_for(lambda: "sayintentions-efb" in self.names() and not self.stash()))
        self.assertEqual(os.getxattr(self.community / "sayintentions-efb", "user.WINEREPARSE"), SI_LINK)
        self.assertEqual(os.getxattr(self.community / "SayIntentions-SimObjects-Core", "user.WINEREPARSE"), SI_LINK)

    def test_with_the_simulator_open_the_links_stay(self):
        self.start_watcher()
        self.stand_in(SIMULATOR_STAND_IN)
        self.stand_in(HUB_STAND_IN)
        time.sleep(0.8)
        self.assertIn("sayintentions-efb", self.names())
        self.assertEqual(self.stash(), [])

    def test_a_link_the_hub_made_again_is_not_overwritten(self):
        self.start_watcher()
        hub = self.stand_in(HUB_STAND_IN)
        self.assertTrue(self.wait_for(lambda: len(self.stash()) == 2))
        self.make_link("sayintentions-efb", SI_LINK + "!".encode("utf-16-le"))
        hub.kill()
        hub.wait()
        self.assertTrue(self.wait_for(lambda: not self.stash()))
        self.assertEqual(os.getxattr(self.community / "sayintentions-efb", "user.WINEREPARSE"), SI_LINK + "!".encode("utf-16-le"))

    def test_stopping_the_watcher_with_the_hub_open_gives_the_links_back(self):
        process = self.start_watcher()
        self.stand_in(HUB_STAND_IN)
        self.assertTrue(self.wait_for(lambda: len(self.stash()) == 2))
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=10)
        self.assertEqual(process.returncode, 0)
        self.assertIn("sayintentions-efb", self.names())
        self.assertEqual(self.stash(), [])

    def test_it_logs_one_line_for_each_change(self):
        process = self.start_watcher()
        hub = self.stand_in(HUB_STAND_IN)
        self.assertTrue(self.wait_for(lambda: len(self.stash()) == 2))
        hub.kill()
        hub.wait()
        self.assertTrue(self.wait_for(lambda: not self.stash()))
        process.send_signal(signal.SIGTERM)
        output = process.communicate(timeout=10)[0]
        self.assertEqual(output.count("kept and removed"), 2)
        self.assertEqual(output.count("gave back"), 2)


class PageTest(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "site" / "pages" / "sayintentions.html").read_text()

    def test_the_page_counts_the_proton_fs2024_items_it_lists(self):
        start = self.text.index("It also needs ")
        listed = self.text[start:self.text.index("</ul>", start)]
        from_the_build = [item for item in listed.split("<li>")[1:] if "Proton Voice Files" not in item]
        self.assertEqual(len(from_the_build), 4)
        self.assertIn("It also needs four things from the Proton FS2024 build and one app from Steam", self.text)

    def test_the_page_marks_the_fix_as_touching_a_file_of_the_add_on_and_gives_the_undo_command(self):
        top = self.text[:self.text.index("<h2>Get it</h2>")]
        self.assertIn("touches add-on files", top)
        self.assertIn("addon undo sayintentions", top)

    def test_the_page_sends_the_reader_to_the_dotnet_48_add_on(self):
        self.assertIn("addon apply dotnet48", self.text)
        self.assertNotIn("The Fenix add-on installs it", self.text)


if __name__ == "__main__":
    unittest.main()
