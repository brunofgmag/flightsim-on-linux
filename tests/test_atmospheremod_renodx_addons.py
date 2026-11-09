import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fsol import addons
from tests.support import stub
from tests.test_machine import FakeMachine, write

ROOT = Path(__file__).resolve().parent.parent
ATMOSPHERE = ROOT / "addons" / "atmospheremod"
RENODX = ROOT / "addons" / "renodx-hdr"
PAGES = ROOT / "site" / "pages"
BASE = 1_700_000_000
PACK = "SHADERSPAK.d5d2118d.SM6.PC.PAK"
NEW_PACK = "SHADERSPAK.0badc0de.SM6.PC.PAK"
WINDOWS_DATA = "C:\\users\\steamuser\\AppData\\Local\\AtmosphereMod"
RESHADE_INI = (
    "[GENERAL]\r\nEffectSearchPaths=.\\reshade-shaders\\Shaders\r\n\r\n"
    "[ATMOSPHEREMOD]\r\nDataPath=C:\\users\\steamuser\\AppData\\Local\\AtmosphereMod\r\n"
    "InjectGraphicsRootSignature=1\r\nInjectRootSignature=1\r\n"
    "TunablesPath=C:\\users\\steamuser\\AppData\\Local\\AtmosphereMod\\tunables.ini\r\n\r\n"
    "[OVERLAY]\r\nTutorialProgress=4\r\n"
)
LAYOUTS = (
    "ph_globalCloudAtmospherics 0x0283ca18 layout 0x0000000009a05660\n"
    "  [0] root constants b0 space222 x2 dwords\n"
    "  [1] descriptor table t0 space0 x80\n"
    "  [5] root constants b0 space{space} x16 dwords\n"
    "  [6] root descriptor u0 space{space} x1\n"
    "  [7] root constants b1 space{space} x32 dwords\n"
    "\n"
    "ps_deferredRendering 0x2d523b59 layout 0x0000000009c7b990\n"
    "  [0] root constants b0 space222 x2 dwords\n"
    "  [23] root constants b0 space{space} x16 dwords\n"
)
REGISTERED_ATMOSPHERE = '16:21:09:165 [  344] | INFO  | Registered add-on "AtmosphereMod" v0.5.1.0 using ReShade API version 20.\n'
REGISTERED_RENODX = '16:21:09:259 [  344] | INFO  | Registered add-on "RenoDX" v0.2026.1101.238 using ReShade API version 18.\n'
INSERTED = "16:21:10:070 [  344] | INFO  | [RenoDX] mods::shader::OnCreatePipelineLayout(will insert cbuffer 13 at root_index 25 with slot count 13 creating new size of 26, root_dwords: 42 => 55, constant_injection_cost: 13)\n"
NOT_CREATED = "16:21:10:070 [  344] | WARN  | [RenoDX] mods::shader::OnInitPipelineLayout(Params not created for: 0x0000000009a05660)\n"
FAILED = "16:21:12:661 [ 1008] | ERROR | [RenoDX] utils::shader::PipelineShaderDetails(Failed to replace pipeline for 0x6bc6b2f3)\n"
WORKING_LOG = REGISTERED_RENODX + INSERTED + NOT_CREATED
FAILING_LOG = REGISTERED_RENODX + NOT_CREATED + FAILED
RENODX_ADDON = b"MZ renodx-msfs2024 of the tested release"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return Path(path).read_bytes().decode()


def at(path, offset):
    os.utime(path, (BASE + offset, BASE + offset))


def tree(root):
    return sorted(str(path.relative_to(root)) + (":" + sha256(path.read_bytes()) if path.is_file() and not path.is_symlink() else "") for path in root.rglob("*"))


def windows(path):
    return "Z:" + str(path).replace("/", "\\")


def lines(text):
    return [line.split()[0] for line in text.splitlines()]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MachineCase(unittest.TestCase):
    ADDON = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake = FakeMachine(self.root).build()
        self.found = self.fake.discover()
        self.sim = self.found.sim_install
        self.prefix = self.found.prefix
        self.proton = self.fake.tool
        self.community = self.found.community
        self.data = self.prefix / "drive_c" / "users" / "steamuser" / "AppData" / "Local" / "AtmosphereMod"
        self.record = self.root / "record"
        self.log = self.root / "curl.log"
        self.work = self.root / "work"
        self.work.mkdir()
        write(self.sim / "dxgi.dll", "MZ ReShade")
        stub(self.root / "bin" / "curl", f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.log}"\nout=""\nwhile [ $# -gt 0 ]; do [ "$1" = --output ] && out="$2"; shift; done\nprintf junk > "$out"\n')

    def tearDown(self):
        self.temp.cleanup()

    def environment(self, **extra):
        env = {
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.fake.home),
            "TMPDIR": str(self.work),
            "XDG_DATA_HOME": str(self.root / "data"),
            "FSOL_PREFIX": str(self.prefix),
            "FSOL_PROTON": str(self.found.proton),
            "FSOL_COMPATDATA": str(self.found.compatdata),
            "FSOL_COMMUNITY": str(self.community),
            "FSOL_BASE": str(self.found.base),
            "FSOL_SIM_INSTALL": str(self.sim),
            "FSOL_CACHE": str(self.root / "cache"),
            "FSOL_ADDON": self.ADDON,
            "FSOL_SIM_RUNNING": "0",
        }
        env.update(extra)
        return env

    def script(self, name, *args, **extra):
        return subprocess.run([str(ROOT / "addons" / self.ADDON / name), *args], env=self.environment(**extra), capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def backup(self, *parts):
        return self.found.compatdata.joinpath("flightsim-on-linux-backup", *parts)

    def write_ini(self, text=RESHADE_INI):
        (self.sim / "ReShade.ini").write_bytes(text.encode())
        at(self.sim / "ReShade.ini", 0)

    def install_atmospheremod(self, ini=RESHADE_INI, patched=3, failed=0, pack=PACK, version="0.5.1", register_space=None):
        write(self.sim / "AtmosphereMod.addon64", "MZ AtmosphereMod")
        self.write_ini(ini)
        for number in range(patched):
            write(self.data / "shaders" / f"{number:08x}.cso", "DXIL")
        record = {"version": version, "pak": pack, "patched": patched, "failed": failed}
        if register_space is not None:
            record["register_space"] = register_space
        write(self.data / "install.json", json.dumps(record))
        write(self.sim / "Shaders" / pack, "pack")
        for path in (self.sim / "AtmosphereMod.addon64", self.data / "install.json", self.sim / "Shaders" / pack):
            at(path, 0)

    def write_layouts(self, space, offset=10):
        write(self.data / "logs" / "layouts.txt", LAYOUTS.replace("{space}", str(space)))
        at(self.data / "logs" / "layouts.txt", offset)

    def write_reshade_log(self, text, offset=10):
        (self.sim / "ReShade.log").write_bytes(text.replace("\n", "\r\n").encode())
        at(self.sim / "ReShade.log", offset)


class ShapeTest(unittest.TestCase):
    def test_atmospheremod_is_a_downloaded_install_that_runs_in_a_prefix_of_its_own(self):
        addon = addons.load(ATMOSPHERE)
        self.assertEqual((addon.id, addon.tested_version, addon.tested_on, addon.needs, addon.touches_addon_files), ("atmospheremod", "0.7.1", "2026-11-09", ["simulator-closed"], False))
        self.assertEqual(addon.installed, {"sim": ["AtmosphereMod.addon64"]})
        self.assertEqual((addon.install["kind"], addon.install["paid"], addon.install["own_prefix"]), ("native", False, True))
        self.assertTrue(addon.can_install)
        self.assertFalse(addon.install_needs_file)

    def test_renodx_hdr_takes_a_file_from_nexus_and_edits_atmospheremods_setting(self):
        addon = addons.load(RENODX)
        self.assertEqual((addon.id, addon.tested_version, addon.tested_on, addon.needs, addon.touches_addon_files), ("renodx-hdr", "1", "2026-11-09", ["simulator-closed"], True))
        self.assertEqual(addon.installed, {"sim": ["renodx-msfs2024.addon64"]})
        self.assertEqual((addon.install["kind"], addon.install["paid"]), ("installer", False))
        self.assertEqual(addon.install["from"], "https://www.nexusmods.com/microsoftflightsimulator2024/mods/26")
        self.assertTrue(addon.can_install)
        self.assertTrue(addon.install_needs_file)

    def test_each_add_on_counts_as_installed_where_its_addon64_is(self):
        with tempfile.TemporaryDirectory() as temp:
            found = FakeMachine(Path(temp)).build().discover()
            for folder, name in ((ATMOSPHERE, "AtmosphereMod.addon64"), (RENODX, "renodx-msfs2024.addon64")):
                addon = addons.load(folder)
                self.assertFalse(addons.is_installed(addon, found))
                write(found.sim_install / name, "MZ")
                self.assertTrue(addons.is_installed(addon, found))

    def test_the_pinned_download_is_the_public_release_asset(self):
        text = (ATMOSPHERE / "parts" / "atmospheremod.sh").read_text()
        self.assertIn('ATMOSPHEREMOD_VERSION="0.7.1"', text)
        self.assertIn("https://github.com/tomwhoca/AtmosphereMod/releases/download/v$ATMOSPHEREMOD_VERSION/AtmosphereMod-$ATMOSPHEREMOD_VERSION.zip", text)
        self.assertIn('ATMOSPHEREMOD_SHA256="49a8aba9fd0b37cc1bb3b008cc537592a7d865edc7b5fca9feb7d54a1147b4d7"', text)

    def test_the_renodx_file_that_was_tested_is_pinned_by_its_hash(self):
        text = (RENODX / "parts" / "renodx.py").read_text()
        self.assertIn('ADDON_SHA256 = "304008e72d2fa6a9efe23bea527dec79525ae82decd7c16ef0c36bb27eeecb7e"', text)

    def test_no_file_of_atmospheremod_and_no_patch_for_it_is_in_the_repository(self):
        shipped = [path for folder in (ATMOSPHERE, RENODX) for path in folder.rglob("*") if path.is_file() and "__pycache__" not in path.parts]
        self.assertEqual(sorted(path.suffix for path in shipped if path.suffix not in ("", ".json", ".sh", ".py")), [])
        for path in shipped:
            self.assertNotIn(b"MOD_SPACE", path.read_bytes(), path.name)
            self.assertNotIn(b"kModSpace", path.read_bytes(), path.name)


class AtmosphereCheckTest(MachineCase):
    ADDON = "atmospheremod"

    def setUp(self):
        super().setUp()
        self.install_atmospheremod()

    def failures(self, result):
        return [line for line in result.stdout.splitlines() if line.startswith("FAIL")]

    def test_a_machine_with_the_installer_done_passes_with_six_ok_lines(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(lines(result.stdout), ["ok"] * 6)

    def test_the_log_line_appears_once_reshade_has_registered_the_add_on_after_the_install(self):
        self.write_reshade_log(REGISTERED_ATMOSPHERE)
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(lines(result.stdout), ["ok"] * 7)
        self.assertIn("registered AtmosphereMod 0.5.1.0", result.stdout)

    def test_a_log_written_after_the_install_without_the_registration_fails(self):
        self.write_reshade_log("16:21:09:164 [  344] | INFO  | Loading add-on from 'AtmosphereMod.addon64' ...\n16:21:09:165 [  344] | ERROR | Failed to load\n")
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.failures(result)), 1)
        self.assertIn("does not say ReShade registered AtmosphereMod", self.failures(result)[0])

    def test_a_log_from_before_the_install_says_nothing_either_way(self):
        self.write_reshade_log("16:21:09:164 [  344] | INFO  | nothing about the add-on\n", offset=-10)
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(lines(result.stdout), ["ok"] * 6)

    def with_space(self, patched, setting):
        ini = RESHADE_INI if setting is None else RESHADE_INI.replace("TunablesPath=", f"RegisterSpace={setting}\r\nTunablesPath=")
        self.install_atmospheremod(ini=ini, version="0.7.1", register_space=patched)

    def test_a_release_that_records_its_space_gets_a_seventh_line_when_the_ini_matches(self):
        self.with_space(51, "51")
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(lines(result.stdout), ["ok"] * 7)
        self.assertIn("patched for register space 51, and [ATMOSPHEREMOD] in ReShade.ini has RegisterSpace=51", result.stdout)

    def test_a_registerspace_other_than_the_patched_one_fails_alone_and_says_to_install_again(self):
        self.with_space(51, "50")
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.failures(result)), 1)
        self.assertIn("patched for register space 51, but [ATMOSPHEREMOD] in ReShade.ini has RegisterSpace=50", self.failures(result)[0])
        self.assertIn("addon install atmospheremod", self.failures(result)[0])

    def test_no_registerspace_means_50_so_it_passes_for_shaders_patched_in_50_and_fails_for_51(self):
        self.with_space(50, None)
        self.assertEqual(self.script("check").returncode, 0)
        self.with_space(51, None)
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("has no RegisterSpace, which means space 50,", self.failures(result)[0])

    def test_a_registerspace_that_is_not_a_number_fails(self):
        self.with_space(51, "fifty-one")
        self.assertEqual(self.script("check").returncode, 1)

    def test_a_registerspace_in_another_section_does_not_count(self):
        self.with_space(51, None)
        self.write_ini(RESHADE_INI.replace("[OVERLAY]", "[GENERAL2]\r\nRegisterSpace=51\r\n\r\n[OVERLAY]"))
        self.assertEqual(self.script("check").returncode, 1)

    def test_an_ini_without_the_section_fails_once_on_the_section_and_not_again_on_the_space(self):
        self.with_space(51, "51")
        self.write_ini("[GENERAL]\r\nEffectSearchPaths=.\r\n")
        result = self.script("check")
        self.assertEqual(len(self.failures(result)), 1)
        self.assertNotIn("register space", result.stdout)

    def test_without_reshade_it_fails_and_says_to_install_it(self):
        (self.sim / "dxgi.dll").unlink()
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.failures(result)), 1)
        self.assertIn("addon install reshade", result.stdout)

    def test_without_the_add_on_file_it_fails_and_says_to_install_it(self):
        (self.sim / "AtmosphereMod.addon64").unlink()
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.failures(result)), 1)
        self.assertIn("addon install atmospheremod", self.failures(result)[0])

    def test_a_section_without_tunables_path_fails_alone(self):
        self.write_ini(RESHADE_INI.replace("TunablesPath=C:\\users\\steamuser\\AppData\\Local\\AtmosphereMod\\tunables.ini\r\n", ""))
        result = self.script("check")
        self.assertEqual(len(self.failures(result)), 1)
        self.assertIn("has no TunablesPath", self.failures(result)[0])

    def test_a_section_without_data_path_fails_alone(self):
        self.write_ini(RESHADE_INI.replace("DataPath=C:\\users\\steamuser\\AppData\\Local\\AtmosphereMod\r\n", ""))
        result = self.script("check")
        self.assertEqual(len(self.failures(result)), 1)
        self.assertIn("has no DataPath", self.failures(result)[0])

    def test_a_data_path_in_another_section_does_not_count(self):
        self.write_ini("[GENERAL]\r\nDataPath=x\r\nTunablesPath=y\r\n[ATMOSPHEREMOD]\r\nInjectRootSignature=1\r\n")
        result = self.script("check")
        self.assertEqual(len(self.failures(result)), 1)
        self.assertIn("has no DataPath or TunablesPath", self.failures(result)[0])

    def test_a_missing_ini_fails(self):
        (self.sim / "ReShade.ini").unlink()
        result = self.script("check")
        self.assertEqual(len(self.failures(result)), 1)
        self.assertIn("ReShade.ini does not exist", self.failures(result)[0])

    def test_without_the_install_record_it_fails_once_and_skips_the_shader_pack_line(self):
        (self.data / "install.json").unlink()
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.failures(result)), 1)
        self.assertIn("install.json is missing", self.failures(result)[0])
        self.assertEqual(len(result.stdout.splitlines()), 4)

    def test_a_record_that_is_not_json_counts_as_missing(self):
        write(self.data / "install.json", "{not json")
        result = self.script("check")
        self.assertIn("install.json is missing or unreadable", self.failures(result)[0])

    def test_shaders_that_failed_to_patch_fail_alone(self):
        self.install_atmospheremod(failed=2)
        result = self.script("check")
        self.assertEqual(len(self.failures(result)), 1)
        self.assertIn("failed to patch 2 shaders", self.failures(result)[0])

    def test_a_shaders_folder_with_fewer_files_than_the_record_says_fails_alone(self):
        (self.data / "shaders" / "00000002.cso").unlink()
        result = self.script("check")
        self.assertEqual(len(self.failures(result)), 1)
        self.assertIn("3 shaders were patched, but the shaders folder holds 2", self.failures(result)[0])

    def test_a_shaders_folder_with_more_files_than_the_record_says_fails_alone(self):
        write(self.data / "shaders" / "ffffffff.cso", "DXIL")
        result = self.script("check")
        self.assertEqual(len(self.failures(result)), 1)
        self.assertIn("holds 4 .cso files", self.failures(result)[0])

    def test_a_simulator_update_that_brought_a_new_shader_pack_fails_and_says_to_install_again(self):
        write(self.sim / "Shaders" / NEW_PACK, "new pack")
        at(self.sim / "Shaders" / NEW_PACK, 20)
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.failures(result)), 1)
        self.assertIn("the simulator was updated", self.failures(result)[0])
        self.assertIn(f"patched from {PACK}", self.failures(result)[0])
        self.assertIn("run the install again", self.failures(result)[0])

    def test_with_two_packs_the_newest_one_is_the_one_that_counts(self):
        write(self.sim / "Shaders" / NEW_PACK, "new pack")
        at(self.sim / "Shaders" / NEW_PACK, -20)
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_a_simulator_without_a_shader_pack_fails(self):
        (self.sim / "Shaders" / PACK).unlink()
        result = self.script("check")
        self.assertEqual(len(self.failures(result)), 1)
        self.assertIn("has no SHADERSPAK", self.failures(result)[0])

    def test_no_simulator_folder_fails_instead_of_reading_the_current_folder(self):
        result = self.script("check", FSOL_SIM_INSTALL="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL no simulator install folder found", result.stdout)

    def test_no_prefix_fails_instead_of_reading_the_current_folder(self):
        result = self.script("check", FSOL_PREFIX="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL no prefix found", result.stdout)

    def test_check_changes_nothing(self):
        self.write_reshade_log(REGISTERED_ATMOSPHERE)
        before = tree(self.root)
        self.script("check")
        self.assertEqual(tree(self.root), before)


class AtmosphereApplyTest(MachineCase):
    ADDON = "atmospheremod"

    def test_apply_changes_nothing_and_says_the_installer_did_everything(self):
        self.install_atmospheremod()
        before = tree(self.root)
        result = self.script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertIn("already nothing to apply", result.stdout)
        self.assertIn("installer put its add-on file", result.stdout)
        self.assertEqual(tree(self.root), before)


class AtmosphereBashCase(MachineCase):
    ADDON = "atmospheremod"

    def setUp(self):
        super().setUp()
        self.sibling = self.root / "data" / "flightsim-on-linux" / "addons-prefix"
        write(self.sibling / "system.reg", "")
        self.wine(0)

    def wine(self, code):
        stub(self.proton / "files/bin/wine", (
            '#!/bin/sh\n'
            f'for argument in "$@"; do printf "%s\\n" "$argument"; done > "{self.record}.args"\n'
            f'printf "%s\\n" "$WINEPREFIX" > "{self.record}.prefix"\n'
            f'env > "{self.record}.env"\n'
            f'cat > "{self.record}.stdin"\n'
            + r"""first=$(printf %s "$1" | tr '\\' '/' | sed 's/^Z://')""" + "\n"
            f'[ -f "$first" ] && echo present > "{self.record}.present"\n'
            f'exit {code}\n'
        ))

    def bash(self, body, **extra):
        text = f'set -euo pipefail\n. "{ROOT}/tools/addon-launch.sh"\n. "{ROOT}/tools/addons-prefix.sh"\n. "{ATMOSPHERE}/parts/atmospheremod.sh"\n{body}'
        return subprocess.run(["bash", "-c", text], env=self.environment(**extra), capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def release_zip(self, folder="AtmosphereMod-9.9.9", members=("installer/install.py", "installer/python/python.exe", "installer/dxc/bin/x64/dxc.exe", "payload/AtmosphereMod.addon64")):
        path = self.root / "AtmosphereMod-9.9.9.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for member in members:
                archive.writestr(f"{folder}/{member}", "x")
        return path

    def arguments(self):
        return Path(f"{self.record}.args").read_text().splitlines()

    def stdin(self):
        return Path(f"{self.record}.stdin").read_text()

    def run_mode(self, mode, **extra):
        return self.bash(f'atmospheremod_run {mode} "{self.release_zip()}"', **extra)


class AtmosphereInstallRunTest(AtmosphereBashCase):
    def test_it_runs_the_zips_own_python_on_its_installer_with_the_arguments_the_installer_needs(self):
        result = self.run_mode("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = self.arguments()
        installer = arguments[1][: -len("\\install.py")]
        self.assertTrue(arguments[0].startswith("Z:\\") and arguments[0].endswith("\\AtmosphereMod-9.9.9\\installer\\python\\python.exe"))
        self.assertEqual(arguments[1], installer + "\\install.py")
        self.assertEqual(arguments[2:], ["--data", WINDOWS_DATA, "--yes", "--sim", windows(self.sim), "--community", windows(self.community), "--dxc", installer + "\\dxc\\bin\\x64"])

    def test_the_dxc_folder_of_the_zip_is_passed_so_the_installer_never_asks_powershell_for_a_signature(self):
        self.run_mode("install")
        self.assertIn("--dxc", self.arguments())

    def test_the_zip_is_unpacked_while_wine_runs_and_the_work_folder_is_gone_after(self):
        self.run_mode("install")
        self.assertTrue(Path(f"{self.record}.present").exists())
        self.assertEqual(list(self.work.iterdir()), [])

    def test_it_runs_in_the_add_ons_prefix_without_the_variable_that_breaks_wine_installers(self):
        result = self.run_mode("install", NoDefaultCurrentDirectoryInExePath="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(f"{self.record}.prefix").read_text().strip(), str(self.sibling))
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", Path(f"{self.record}.env").read_text())

    def test_the_installer_gets_no_input_and_so_is_never_left_waiting_for_an_answer(self):
        self.run_mode("install")
        self.assertEqual(self.stdin(), "")

    def test_the_exit_code_of_the_installer_is_the_exit_code_of_the_script_and_the_work_folder_still_goes(self):
        self.wine(1)
        result = self.run_mode("install")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_a_zip_without_the_installer_stops_before_wine_runs(self):
        result = self.bash(f'atmospheremod_run install "{self.release_zip(members=("README.md",))}"')
        self.assertEqual(result.returncode, 1)
        self.assertIn("installer/install.py", result.stderr)
        self.assertFalse(Path(f"{self.record}.args").exists())
        self.assertEqual(list(self.work.iterdir()), [])

    def test_it_makes_the_add_ons_prefix_when_there_is_none_yet(self):
        (self.sibling / "system.reg").unlink()
        for name in ("system.reg", "user.reg", "userdef.reg"):
            write(self.prefix / name, f"sim {name}\n")
        result = self.run_mode("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.sibling / "system.reg").read_text(), "sim system.reg\n")

    def test_with_renodx_in_the_simulator_folder_the_installer_also_gets_renodx_and_says_why(self):
        write(self.sim / "renodx-msfs2024.addon64", "MZ")
        result = self.run_mode("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.arguments()[-1], "--renodx")
        self.assertEqual(self.arguments().count("--renodx"), 1)
        self.assertIn("register space 51", result.stdout)

    def test_without_renodx_the_installer_gets_neither_space_option_so_it_keeps_the_space_in_reshade_ini(self):
        result = self.run_mode("install")
        self.assertNotIn("--renodx", self.arguments())
        self.assertNotIn("--no-renodx", self.arguments())
        self.assertNotIn("register space", result.stdout)

    def test_a_file_named_like_renodx_in_another_folder_does_not_count(self):
        write(self.sim / "Shaders" / "renodx-msfs2024.addon64", "MZ")
        self.run_mode("install")
        self.assertNotIn("--renodx", self.arguments())

    def test_it_leaves_an_existing_add_ons_prefix_alone_and_says_nothing_about_it(self):
        write(self.sibling / "user.reg", "grown\n")
        result = self.run_mode("install")
        self.assertEqual((self.sibling / "user.reg").read_text(), "grown\n")
        self.assertNotIn("already exists", result.stderr)


class AtmosphereCommunityTest(AtmosphereBashCase):
    def community_argument(self, mode="install"):
        self.run_mode(mode)
        arguments = self.arguments()
        return arguments[arguments.index("--community") + 1]

    def test_a_real_community_folder_is_passed_as_it_is(self):
        self.assertEqual(self.community_argument(), windows(self.community))

    def test_a_community_entry_that_is_a_link_passes_the_folder_that_holds_the_real_app(self):
        library = self.root / "library" / "Utils"
        (library / "atmospheremod-weather").mkdir(parents=True)
        (self.community / "atmospheremod-weather").symlink_to(library / "atmospheremod-weather")
        self.assertEqual(self.community_argument(), windows(library))

    def test_the_real_app_folder_is_not_touched_by_this_script_when_the_entry_is_a_link(self):
        library = self.root / "library" / "Utils"
        write(library / "atmospheremod-weather" / "manifest.json", "{}")
        (self.community / "atmospheremod-weather").symlink_to(library / "atmospheremod-weather")
        self.run_mode("install")
        self.assertEqual(read(library / "atmospheremod-weather" / "manifest.json"), "{}")

    def test_the_uninstall_passes_the_same_folder(self):
        library = self.root / "library" / "Utils"
        (library / "atmospheremod-weather").mkdir(parents=True)
        (self.community / "atmospheremod-weather").symlink_to(library / "atmospheremod-weather")
        self.assertEqual(self.community_argument("uninstall"), windows(library))

    def test_a_machine_without_a_community_folder_stops_before_wine_runs(self):
        result = self.run_mode("install", FSOL_COMMUNITY=str(self.root / "no-such-folder"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no Community folder found", result.stderr)
        self.assertFalse(Path(f"{self.record}.args").exists())


class AtmosphereUninstallRunTest(AtmosphereBashCase):
    def test_it_runs_the_installers_uninstall_for_this_simulator_and_this_community_folder(self):
        result = self.run_mode("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = self.arguments()
        self.assertTrue(arguments[0].endswith("\\installer\\python\\python.exe"))
        self.assertEqual(arguments[2:], ["--data", WINDOWS_DATA, "--uninstall", "--sim", windows(self.sim), "--community", windows(self.community)])

    def test_it_answers_yes_to_the_uninstall_and_no_to_deleting_the_data_folder(self):
        self.run_mode("uninstall")
        self.assertEqual(self.stdin(), "y\nn\n")
        self.assertNotIn("--yes", self.arguments())

    def test_the_uninstall_never_gets_a_space_option_even_beside_renodx(self):
        write(self.sim / "renodx-msfs2024.addon64", "MZ")
        self.run_mode("uninstall")
        self.assertNotIn("--renodx", self.arguments())

    def test_the_work_folder_goes_after_an_uninstall_too(self):
        self.run_mode("uninstall")
        self.assertEqual(list(self.work.iterdir()), [])


class AtmosphereDownloadTest(AtmosphereBashCase):
    URL = "https://example.test/AtmosphereMod.zip"

    def setUp(self):
        super().setUp()
        self.asset = b"a zip of a release"
        self.sha = sha256(self.asset)
        stub(self.root / "bin" / "curl", f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.log}"\nout=""\nwhile [ $# -gt 0 ]; do [ "$1" = --output ] && out="$2"; shift; done\nprintf %s "$CURL_BODY" > "$out"\n')

    def fetch(self, target, sha=None):
        return self.bash(f'atmospheremod_fetch "{self.URL}" {sha or self.sha} "{target}"', CURL_BODY=self.asset.decode())

    def test_a_download_with_the_right_hash_is_kept_in_the_cache_folder(self):
        target = self.root / "cache" / "downloads" / "file.zip"
        result = self.fetch(target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_bytes(), self.asset)
        self.assertFalse(Path(f"{target}.part").exists())
        self.assertIn(self.URL, self.log.read_text())
        self.assertIn("--fail", self.log.read_text())

    def test_a_file_already_downloaded_is_not_downloaded_again(self):
        target = self.root / "cache" / "downloads" / "file.zip"
        self.fetch(target)
        self.log.unlink()
        result = self.fetch(target)
        self.assertEqual(result.returncode, 0)
        self.assertIn("already downloaded", result.stdout)
        self.assertFalse(self.log.exists())

    def test_a_download_with_another_hash_is_thrown_away_and_nothing_is_kept(self):
        target = self.root / "cache" / "downloads" / "file.zip"
        result = self.fetch(target, sha="0" * 64)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(target.exists())
        self.assertFalse(Path(f"{target}.part").exists())
        self.assertIn("not the file this add-on was tested with", result.stderr)

    def test_a_cached_file_with_another_hash_is_downloaded_again(self):
        target = self.root / "cache" / "downloads" / "file.zip"
        write(target, "an old or damaged file")
        self.assertEqual(self.fetch(target).returncode, 0)
        self.assertEqual(target.read_bytes(), self.asset)


class AtmosphereInstallScriptTest(MachineCase):
    ADDON = "atmospheremod"

    def test_it_refuses_while_the_simulator_runs_and_downloads_nothing(self):
        result = self.script("install", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("close the simulator", result.stderr)
        self.assertFalse(self.log.exists())

    def test_without_reshade_it_stops_and_says_to_install_it_first(self):
        (self.sim / "dxgi.dll").unlink()
        result = self.script("install")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install reshade", result.stderr)
        self.assertFalse(self.log.exists())

    def test_it_downloads_the_pinned_url_and_runs_nothing_when_the_hash_differs(self):
        result = self.script("install")
        self.assertEqual(result.returncode, 1)
        self.assertIn("https://github.com/tomwhoca/AtmosphereMod/releases/download/v0.7.1/AtmosphereMod-0.7.1.zip", self.log.read_text())
        self.assertIn("nothing was run", result.stderr)
        self.assertEqual(list((self.root / "cache" / "downloads").iterdir()), [])

    def test_a_zip_given_by_hand_with_another_hash_is_refused_and_nothing_is_downloaded(self):
        file = self.root / "AtmosphereMod-0.5.1.zip"
        file.write_bytes(b"a newer release")
        result = self.script("install", FSOL_INSTALLER=str(file))
        self.assertEqual(result.returncode, 1)
        self.assertIn("not the release this add-on was tested with", result.stderr)
        self.assertFalse(self.log.exists())

    def test_the_installer_does_not_need_the_simulators_prefix_to_be_free(self):
        self.assertTrue(addons.load(ATMOSPHERE).install["own_prefix"])


class AtmosphereUndoScriptTest(MachineCase):
    ADDON = "atmospheremod"

    def test_it_refuses_while_the_simulator_runs(self):
        self.install_atmospheremod()
        result = self.script("undo", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("close the simulator", result.stderr)
        self.assertFalse(self.log.exists())

    def test_without_the_add_on_it_says_so_and_downloads_nothing(self):
        before = tree(self.root)
        result = self.script("undo")
        self.assertEqual(result.returncode, 0)
        self.assertIn("nothing to undo", result.stdout)
        self.assertFalse(self.log.exists())
        self.assertEqual(tree(self.root), before)

    def test_a_data_folder_alone_does_not_count_as_installed_so_a_second_undo_is_cheap(self):
        write(self.data / "tunables.ini", "T1=1\n")
        result = self.script("undo")
        self.assertIn("nothing to undo", result.stdout)
        self.assertFalse(self.log.exists())

    def test_an_add_on_file_alone_counts_as_installed(self):
        write(self.sim / "AtmosphereMod.addon64", "MZ")
        result = self.script("undo")
        self.assertEqual(result.returncode, 1)
        self.assertIn("AtmosphereMod-0.7.1.zip", self.log.read_text())

    def test_a_link_in_community_alone_counts_as_installed(self):
        (self.community / "atmospheremod-weather").symlink_to(self.root / "gone")
        result = self.script("undo")
        self.assertEqual(result.returncode, 1)
        self.assertIn("AtmosphereMod-0.7.1.zip", self.log.read_text())


class RenoDXCase(MachineCase):
    ADDON = "renodx-hdr"

    def setUp(self):
        super().setUp()
        self.addon = self.sim / "renodx-msfs2024.addon64"
        self.stash = self.backup("renodx-hdr", "InjectGraphicsRootSignature.orig")

    def place_addon(self, data=RENODX_ADDON, offset=0):
        self.addon.write_bytes(data)
        at(self.addon, offset)

    def official(self, space=50, layouts=True):
        self.install_atmospheremod()
        if layouts:
            self.write_layouts(space)

    def module(self):
        module = load("renodx_under_test", RENODX / "parts" / "renodx.py")
        patcher = mock.patch.object(module, "ADDON_SHA256", sha256(RENODX_ADDON))
        patcher.start()
        self.addCleanup(patcher.stop)
        return module

    def check(self):
        module = self.module()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = module.check(str(self.sim), str(self.prefix), str(self.backup("renodx-hdr")))
        return code, out.getvalue()

    def failures(self, output):
        return [line for line in output.splitlines() if line.startswith("FAIL")]

    def ini_without_injection_change(self):
        return RESHADE_INI.replace("InjectGraphicsRootSignature=1", "InjectGraphicsRootSignature=0")

    def with_option(self, space=50, version="0.7.1", layouts=None, offset=10):
        self.install_atmospheremod(ini=RESHADE_INI.replace("TunablesPath=", f"RegisterSpace={space}\r\nTunablesPath="), version=version)
        if layouts is not None:
            self.write_layouts(layouts, offset=offset)


class RenoDXInstallTest(RenoDXCase):
    def zip_with(self, *members):
        path = self.root / "download.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in members:
                archive.writestr(name, data)
        return path

    def install_in_process(self, given, **env):
        module = self.module()
        out, err = io.StringIO(), io.StringIO()
        merged = {"FSOL_INSTALLER": str(given), "FSOL_SIM_RUNNING": "0", **env}
        with mock.patch.dict(os.environ, merged), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = module.install(str(self.sim))
        return code, out.getvalue(), err.getvalue()

    def test_a_zip_from_nexus_gives_its_addon64_to_the_simulator_folder(self):
        given = self.zip_with(("README.txt", "readme"), ("LICENSE.txt", "licence"), ("renodx-msfs2024.addon64", RENODX_ADDON))
        code, out, err = self.install_in_process(given)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.addon.read_bytes(), RENODX_ADDON)
        self.assertEqual(sorted(path.name for path in self.sim.glob("renodx*")), ["renodx-msfs2024.addon64"])

    def test_the_addon64_alone_works_as_well(self):
        given = self.root / "renodx-msfs2024.addon64"
        given.write_bytes(RENODX_ADDON)
        code, _, err = self.install_in_process(given)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.addon.read_bytes(), RENODX_ADDON)

    def test_a_zip_that_keeps_the_file_in_a_folder_works(self):
        given = self.zip_with(("renodx-hdr/renodx-msfs2024.addon64", RENODX_ADDON))
        self.assertEqual(self.install_in_process(given)[0], 0)

    def test_the_same_file_again_is_fine(self):
        self.place_addon()
        given = self.zip_with(("renodx-msfs2024.addon64", RENODX_ADDON))
        self.assertEqual(self.install_in_process(given)[0], 0)

    def test_a_zip_without_the_addon64_is_refused_and_copies_nothing(self):
        given = self.zip_with(("README.txt", "readme"))
        code, _, err = self.install_in_process(given)
        self.assertEqual(code, 1)
        self.assertIn("holds no renodx-msfs2024.addon64", err)
        self.assertFalse(self.addon.exists())

    def test_a_file_with_another_hash_is_refused_and_copies_nothing(self):
        given = self.zip_with(("renodx-msfs2024.addon64", b"MZ a later version"))
        code, _, err = self.install_in_process(given)
        self.assertEqual(code, 1)
        self.assertIn("not the release this add-on was tested with", err)
        self.assertFalse(self.addon.exists())

    def test_the_real_script_refuses_a_file_that_is_not_the_tested_release(self):
        given = self.root / "renodx-msfs2024.addon64"
        given.write_bytes(b"MZ something else")
        result = self.script("install", FSOL_INSTALLER=str(given))
        self.assertEqual(result.returncode, 1)
        self.assertIn("not the release this add-on was tested with", result.stderr)
        self.assertFalse(self.addon.exists())

    def test_it_refuses_while_the_simulator_runs(self):
        given = self.zip_with(("renodx-msfs2024.addon64", RENODX_ADDON))
        code, _, err = self.install_in_process(given, FSOL_SIM_RUNNING="1")
        self.assertEqual(code, 1)
        self.assertIn("close the simulator", err)
        self.assertFalse(self.addon.exists())

    def test_without_reshade_it_says_to_install_it_first(self):
        (self.sim / "dxgi.dll").unlink()
        given = self.zip_with(("renodx-msfs2024.addon64", RENODX_ADDON))
        code, _, err = self.install_in_process(given)
        self.assertEqual(code, 1)
        self.assertIn("addon install reshade", err)
        self.assertFalse(self.addon.exists())

    def test_another_renodx_addon_in_the_folder_is_named_and_stops_the_install(self):
        write(self.sim / "renodx-devkit.addon64", "MZ")
        given = self.zip_with(("renodx-msfs2024.addon64", RENODX_ADDON))
        code, _, err = self.install_in_process(given)
        self.assertEqual(code, 1)
        self.assertIn("renodx-devkit.addon64", err)
        self.assertFalse(self.addon.exists())

    def test_without_a_file_it_says_where_to_get_it(self):
        result = self.script("install")
        self.assertEqual(result.returncode, 1)
        self.assertIn("https://www.nexusmods.com/microsoftflightsimulator2024/mods/26", result.stderr)
        self.assertIn("addon install renodx-hdr", result.stderr)

    def test_a_path_that_is_not_a_file_is_refused(self):
        result = self.script("install", FSOL_INSTALLER=str(self.root / "no-such-file.zip"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not a file", result.stderr)

    def test_the_simulator_folder_gets_nothing_else(self):
        given = self.zip_with(("README.txt", "readme"), ("renodx-msfs2024.addon64", RENODX_ADDON))
        before = {path.name for path in self.sim.iterdir()}
        self.install_in_process(given)
        self.assertEqual({path.name for path in self.sim.iterdir()} - before, {"renodx-msfs2024.addon64"})


class RenoDXApplyTest(RenoDXCase):
    def setUp(self):
        super().setUp()
        self.place_addon()

    def injection(self):
        return re.search(r"InjectGraphicsRootSignature=(\S*)", read(self.sim / "ReShade.ini")).group(1)

    def test_with_the_official_atmospheremod_it_turns_the_graphics_injection_off_and_keeps_every_other_line(self):
        self.official()
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.sim / "ReShade.ini").read_bytes(), RESHADE_INI.replace("InjectGraphicsRootSignature=1", "InjectGraphicsRootSignature=0").encode())

    def test_it_says_what_the_fix_costs(self):
        self.official()
        result = self.script("apply")
        self.assertIn("ground haze", result.stdout)
        self.assertIn("ps_deferredRendering", result.stdout)
        self.assertIn("sky and clouds keep the mod", result.stdout)

    def test_the_original_value_is_kept_for_undo(self):
        self.official()
        self.script("apply")
        self.assertEqual(read(self.stash), "1")

    def test_without_layouts_txt_it_assumes_the_official_space_and_says_so(self):
        self.official(layouts=False)
        result = self.script("apply")
        self.assertEqual(self.injection(), "0")
        self.assertIn("space 50, which is assumed", result.stdout)

    def test_layouts_txt_written_before_the_last_atmospheremod_install_is_not_believed(self):
        self.official(space=51)
        self.write_layouts(51, offset=-10)
        self.script("apply")
        self.assertEqual(self.injection(), "0")

    def test_an_atmospheremod_with_its_parameters_outside_space_50_is_left_alone(self):
        self.official(space=51)
        before = (self.sim / "ReShade.ini").read_bytes()
        result = self.script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertEqual((self.sim / "ReShade.ini").read_bytes(), before)
        self.assertIn("nothing was changed", result.stdout)
        self.assertIn("space 51", result.stdout)
        self.assertFalse(self.stash.exists())

    def test_an_injection_that_is_already_off_is_left_alone(self):
        self.install_atmospheremod(ini=self.ini_without_injection_change())
        before = tree(self.root)
        result = self.script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertIn("already 0", result.stdout)
        self.assertEqual(tree(self.root), before)

    def test_a_setting_that_is_not_there_is_left_alone_and_the_reader_is_told_what_to_do(self):
        self.install_atmospheremod(ini=RESHADE_INI.replace("InjectGraphicsRootSignature=1\r\n", ""))
        before = tree(self.root)
        result = self.script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertIn("not 0 or 1", result.stdout)
        self.assertIn("run the AtmosphereMod install again", result.stdout)
        self.assertEqual(tree(self.root), before)

    def test_a_setting_that_is_not_there_does_not_matter_when_the_parameters_are_outside_space_50_and_the_check_agrees(self):
        self.install_atmospheremod(ini=RESHADE_INI.replace("InjectGraphicsRootSignature=1\r\n", ""))
        self.write_layouts(51)
        before = tree(self.root)
        result = self.script("apply")
        self.assertIn("so they do not meet", result.stdout)
        self.assertNotIn("cannot tell", result.stdout)
        self.assertEqual(tree(self.root), before)
        code, output = self.check()
        self.assertEqual(code, 0, output)

    def test_with_a_release_that_has_the_renodx_option_it_changes_nothing_and_names_the_reinstall(self):
        self.with_option(space=50, layouts=50)
        before = tree(self.root)
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("nothing was changed", result.stderr)
        self.assertIn("addon install atmospheremod", result.stderr)
        self.assertIn("--renodx", result.stderr)
        self.assertNotIn("addon apply renodx-hdr", result.stderr)
        self.assertEqual(tree(self.root), before)

    def test_0_7_0_is_the_first_release_with_the_option_and_0_6_1_still_gets_the_injection_turned_off(self):
        self.with_option(space=50, version="0.7.0", layouts=50)
        self.assertEqual(self.script("apply").returncode, 1)
        self.assertEqual(self.injection(), "1")
        self.with_option(space=50, version="0.6.1", layouts=50)
        self.assertEqual(self.script("apply").returncode, 0)
        self.assertEqual(self.injection(), "0")

    def test_a_record_without_a_version_counts_as_an_old_release(self):
        self.official()
        write(self.data / "install.json", json.dumps({"pak": PACK, "patched": 3, "failed": 0}))
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.injection(), "0")
        self.assertIn("of unknown version", result.stdout)

    def test_right_after_a_reinstall_with_renodx_registerspace_is_believed_over_the_older_layouts_txt(self):
        self.with_option(space=51, layouts=50, offset=-10)
        before = tree(self.root)
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing was changed", result.stdout)
        self.assertIn("space 51, which RegisterSpace under [ATMOSPHEREMOD] in ReShade.ini sets", result.stdout)
        self.assertEqual(tree(self.root), before)

    def test_registerspace_50_without_a_newer_layouts_txt_still_clashes(self):
        self.with_option(space=50)
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("space 50, which RegisterSpace", result.stderr)

    def test_a_layouts_txt_newer_than_the_install_wins_over_registerspace(self):
        self.with_option(space=51, layouts=50)
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("space 50", result.stderr)
        self.assertNotIn("RegisterSpace", result.stderr)

    def test_a_registerspace_that_is_not_a_number_counts_as_missing(self):
        self.install_atmospheremod(ini=RESHADE_INI.replace("TunablesPath=", "RegisterSpace=fifty-one\r\nTunablesPath="), version="0.7.1")
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("space 50, which is assumed", result.stderr)

    def test_an_old_release_is_still_fixed_by_turning_the_injection_off_and_is_told_about_the_update(self):
        self.official()
        result = self.script("apply")
        self.assertEqual(self.injection(), "0")
        self.assertIn("AtmosphereMod 0.5.1 puts its parameters", result.stdout)
        self.assertIn("to keep the haze, run instead: flightsim-on-linux addon install atmospheremod", result.stdout)

    def test_without_atmospheremod_nothing_changes(self):
        self.write_ini()
        before = tree(self.root)
        result = self.script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertIn("AtmosphereMod is not installed", result.stdout)
        self.assertEqual(tree(self.root), before)

    def test_without_the_renodx_addon_it_says_to_install_it_and_writes_nothing(self):
        self.official()
        self.addon.unlink()
        before = tree(self.root)
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install renodx-hdr", result.stderr)
        self.assertEqual(tree(self.root), before)

    def test_apply_twice_gives_the_same_file_and_the_same_backup(self):
        self.official()
        self.script("apply")
        first = (self.sim / "ReShade.ini").read_bytes()
        self.script("apply")
        self.assertEqual((self.sim / "ReShade.ini").read_bytes(), first)
        self.assertEqual(read(self.stash), "1")

    def test_an_ini_with_plain_line_feeds_keeps_them(self):
        self.install_atmospheremod(ini=RESHADE_INI.replace("\r\n", "\n"))
        self.script("apply")
        self.assertEqual(read(self.sim / "ReShade.ini"), RESHADE_INI.replace("\r\n", "\n").replace("InjectGraphicsRootSignature=1", "InjectGraphicsRootSignature=0"))

    def test_the_key_is_found_without_regard_to_case_and_spaces_and_only_in_its_own_section(self):
        ini = "[GENERAL]\r\nInjectGraphicsRootSignature=1\r\n[atmospheremod]\r\nDataPath=C:\\x\r\ninjectgraphicsrootsignature = 1\r\nTunablesPath=C:\\y\r\n"
        self.install_atmospheremod(ini=ini)
        self.script("apply")
        self.assertEqual(read(self.sim / "ReShade.ini"), ini.replace("injectgraphicsrootsignature = 1", "injectgraphicsrootsignature =0"))


class RenoDXUndoTest(RenoDXCase):
    def setUp(self):
        super().setUp()
        self.place_addon()

    def test_undo_puts_the_original_value_back_byte_for_byte(self):
        self.official()
        original = (self.sim / "ReShade.ini").read_bytes()
        self.script("apply")
        self.assertNotEqual((self.sim / "ReShade.ini").read_bytes(), original)
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.sim / "ReShade.ini").read_bytes(), original)
        self.assertFalse(self.stash.exists())

    def test_undo_leaves_the_addon_and_says_how_to_delete_it(self):
        self.official()
        self.script("apply")
        result = self.script("undo")
        self.assertTrue(self.addon.exists())
        self.assertIn("delete renodx-msfs2024.addon64", result.stdout)

    def test_undo_changes_only_the_one_line_when_the_file_changed_after_apply(self):
        self.official()
        self.script("apply")
        text = read(self.sim / "ReShade.ini").replace("TutorialProgress=4", "TutorialProgress=9")
        (self.sim / "ReShade.ini").write_bytes(text.encode())
        self.script("undo")
        self.assertEqual(read(self.sim / "ReShade.ini"), RESHADE_INI.replace("TutorialProgress=4", "TutorialProgress=9"))

    def test_undo_without_apply_says_so_and_changes_nothing(self):
        self.official()
        before = tree(self.root)
        result = self.script("undo")
        self.assertEqual(result.returncode, 0)
        self.assertIn("apply did not change anything", result.stdout)
        self.assertEqual(tree(self.root), before)

    def test_the_second_apply_after_a_reinstall_of_atmospheremod_keeps_the_first_backup(self):
        self.official()
        self.script("apply")
        self.write_ini()
        self.script("apply")
        self.script("undo")
        self.assertEqual((self.sim / "ReShade.ini").read_bytes(), RESHADE_INI.encode())

    def test_when_atmospheremods_section_is_gone_undo_does_not_write_a_new_one(self):
        self.official()
        self.script("apply")
        self.write_ini("[GENERAL]\r\nEffectSearchPaths=.\r\n")
        result = self.script("undo")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(read(self.sim / "ReShade.ini"), "[GENERAL]\r\nEffectSearchPaths=.\r\n")
        self.assertIn("nothing to put back", result.stdout)
        self.assertFalse(self.stash.exists())


class RenoDXCheckTest(RenoDXCase):
    def setUp(self):
        super().setUp()
        self.place_addon()

    def test_with_the_official_atmospheremod_the_conflict_fails_and_names_apply(self):
        self.official()
        code, output = self.check()
        self.assertEqual(code, 1)
        failures = self.failures(output)
        self.assertEqual(len(failures), 1)
        self.assertIn("addon apply renodx-hdr", failures[0])
        self.assertIn("addon install atmospheremod", failures[0])
        self.assertIn("space 50", failures[0])

    def test_with_a_release_that_has_the_renodx_option_the_conflict_names_the_reinstall_and_not_apply(self):
        self.with_option(space=50, layouts=50)
        code, output = self.check()
        self.assertEqual(code, 1)
        failures = self.failures(output)
        self.assertEqual(len(failures), 1)
        self.assertIn("run: flightsim-on-linux addon install atmospheremod", failures[0])
        self.assertIn("--renodx", failures[0])
        self.assertNotIn("addon apply renodx-hdr", failures[0])

    def test_right_after_a_reinstall_with_renodx_it_passes_before_the_simulator_has_run(self):
        self.with_option(space=51, layouts=50, offset=-10)
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertEqual(lines(output), ["ok"] * 5)
        self.assertIn("space 51, which RegisterSpace", output)

    def test_a_missing_injection_setting_on_a_release_with_the_option_names_the_reinstall(self):
        self.install_atmospheremod(ini=RESHADE_INI.replace("InjectGraphicsRootSignature=1\r\n", ""), version="0.7.1")
        code, output = self.check()
        self.assertEqual(code, 1)
        self.assertIn("cannot tell what AtmosphereMod does", self.failures(output)[0])
        self.assertIn("with: flightsim-on-linux addon install atmospheremod", self.failures(output)[0])

    def test_after_apply_the_same_machine_passes(self):
        self.official()
        self.script("apply")
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertEqual(lines(output), ["ok"] * 5)
        self.assertIn("InjectGraphicsRootSignature=0", output)

    def test_an_atmospheremod_in_another_space_passes_with_the_injection_on(self):
        self.official(space=51)
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn("space 51", output)

    def test_layouts_txt_older_than_the_atmospheremod_install_is_not_believed_and_the_conflict_fails(self):
        self.official(space=51)
        self.write_layouts(51, offset=-10)
        code, output = self.check()
        self.assertEqual(code, 1)
        self.assertIn("assumed because layouts.txt is missing or older", output)

    def test_without_layouts_txt_the_official_space_is_assumed_and_the_conflict_fails(self):
        self.official(layouts=False)
        code, output = self.check()
        self.assertEqual(code, 1)
        self.assertIn("space 50", self.failures(output)[0])

    def test_layouts_txt_with_no_parameter_block_counts_as_unknown(self):
        self.official(layouts=False)
        write(self.data / "logs" / "layouts.txt", "ps_foo 0x1 layout 0x2\n  [0] root constants b0 space222 x2 dwords\n")
        at(self.data / "logs" / "layouts.txt", 10)
        self.assertEqual(self.check()[0], 1)

    def test_a_layouts_txt_that_still_has_space_50_blocks_fails(self):
        self.official(space=50)
        code, _ = self.check()
        self.assertEqual(code, 1)

    def test_a_setting_that_is_not_there_fails_and_says_it_cannot_tell(self):
        self.install_atmospheremod(ini=RESHADE_INI.replace("InjectGraphicsRootSignature=1\r\n", ""))
        code, output = self.check()
        self.assertEqual(code, 1)
        self.assertIn("cannot tell what AtmosphereMod does", self.failures(output)[0])

    def test_without_atmospheremod_the_line_says_nothing_conflicts(self):
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn("AtmosphereMod is not installed", output)

    def test_without_the_addon_or_reshade_it_fails_on_both(self):
        self.addon.unlink()
        (self.sim / "dxgi.dll").unlink()
        code, output = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.failures(output)), 2)
        self.assertIn("addon install renodx-hdr", output)

    def test_an_addon_with_another_hash_fails_as_untested(self):
        self.place_addon(b"MZ a later version")
        code, output = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.failures(output)), 1)
        self.assertIn("untested", self.failures(output)[0])

    def test_the_real_check_script_fails_a_file_that_is_not_the_tested_release(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("untested", result.stdout)

    def test_another_renodx_addon_fails(self):
        write(self.sim / "renodx-devkit.addon64", "MZ")
        code, output = self.check()
        self.assertEqual(code, 1)
        self.assertIn("renodx-devkit.addon64", self.failures(output)[0])

    def test_no_simulator_folder_fails(self):
        result = self.script("check", FSOL_SIM_INSTALL="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL no simulator install folder found", result.stdout)

    def test_check_changes_nothing(self):
        self.official()
        self.write_reshade_log(WORKING_LOG)
        before = tree(self.root)
        self.check()
        self.script("check")
        self.assertEqual(tree(self.root), before)


class RenoDXLogTest(RenoDXCase):
    def setUp(self):
        super().setUp()
        self.place_addon()
        self.install_atmospheremod(ini=self.ini_without_injection_change())

    def test_a_log_where_renodx_inserts_its_cbuffer_adds_an_ok_line_with_the_root_index(self):
        self.write_reshade_log(WORKING_LOG)
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertEqual(lines(output)[-1], "ok")
        self.assertIn("cbuffer 13 at root_index 25", output.splitlines()[-1])

    def test_params_not_created_alone_is_not_a_failure_because_working_runs_log_it_too(self):
        self.write_reshade_log(WORKING_LOG)
        self.assertIn("Params not created", read(self.sim / "ReShade.log"))
        self.assertEqual(self.check()[0], 0)

    def test_a_log_with_a_failed_replacement_fails_whatever_else_it_says(self):
        self.write_reshade_log(REGISTERED_RENODX + INSERTED + FAILED + FAILED)
        code, output = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.failures(output)), 1)
        self.assertIn("failed to replace a pipeline 2 times", self.failures(output)[0])

    def test_a_failing_log_fails_on_a_machine_where_the_settings_look_right(self):
        self.write_reshade_log(FAILING_LOG)
        self.assertEqual(self.check()[0], 1)

    def test_a_log_where_renodx_never_registered_adds_no_line(self):
        self.write_reshade_log(REGISTERED_ATMOSPHERE + FAILED)
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertEqual(len(output.splitlines()), 5)

    def test_a_log_that_registered_renodx_but_logged_neither_adds_no_line(self):
        self.write_reshade_log(REGISTERED_RENODX)
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertEqual(len(output.splitlines()), 5)

    def test_without_a_log_there_is_no_log_line(self):
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertEqual(len(output.splitlines()), 5)

    def test_a_failing_log_from_before_the_addon_was_installed_is_not_held_against_it(self):
        self.write_reshade_log(FAILING_LOG, offset=-10)
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn("older than the last change", output.splitlines()[-1])
        self.assertEqual(len(self.failures(output)), 0)

    def test_a_failing_log_from_before_the_atmospheremod_install_is_not_held_against_it(self):
        self.place_addon(offset=-20)
        self.write_reshade_log(FAILING_LOG, offset=-10)
        at(self.data / "install.json", 5)
        code, output = self.check()
        self.assertEqual(code, 0, output)

    def test_a_failing_log_from_before_apply_is_not_held_against_it_so_apply_then_check_passes(self):
        self.install_atmospheremod()
        self.write_layouts(50, offset=-5)
        self.write_reshade_log(FAILING_LOG, offset=1)
        at(self.addon, -50)
        at(self.data / "install.json", -40)
        self.assertEqual(self.check()[0], 1)
        self.script("apply")
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn("older than the last change", output.splitlines()[-1])

    def test_a_failing_log_written_after_apply_fails_again(self):
        self.install_atmospheremod()
        self.write_layouts(50, offset=-5)
        self.script("apply")
        os.utime(self.stash, (BASE + 30, BASE + 30))
        self.write_reshade_log(FAILING_LOG, offset=60)
        self.assertEqual(self.check()[0], 1)


class PageTest(unittest.TestCase):
    def page(self, name):
        return (PAGES / f"{name}.html").read_text()

    def test_each_page_has_a_title_a_description_and_the_sections_of_the_other_add_on_pages(self):
        for name in ("atmospheremod", "renodx-hdr"):
            text = self.page(name)
            self.assertTrue(text.startswith("title: "), name)
            self.assertRegex(text, r"\ndescription: .+\n---\n")
            for heading in ("Get it", "Install it", "Make it run", "Check it", "Known problems", "Undo"):
                self.assertIn(f"<h2>{heading}</h2>", text, f"{name}: {heading}")

    def test_the_renodx_page_marks_the_edit_of_atmospheremods_setting_and_gives_the_undo_command_at_the_top(self):
        text = self.page("renodx-hdr")
        top = text[: text.index("<h2>Get it</h2>")]
        self.assertIn('<span class="tag warn">touches add-on files</span>', top)
        self.assertIn("addon undo renodx-hdr", top)

    def test_the_atmospheremod_page_does_not_carry_the_touches_files_tag(self):
        self.assertNotIn("touches add-on files", self.page("atmospheremod"))

    def test_the_atmospheremod_page_links_the_renodx_page_under_known_problems(self):
        text = self.page("atmospheremod")
        self.assertIn('href="renodx-hdr.html"', text[text.index("<h2>Known problems</h2>"):])

    def test_the_renodx_page_links_the_atmospheremod_page(self):
        self.assertIn('href="atmospheremod.html"', self.page("renodx-hdr"))

    def test_the_renodx_page_gives_the_licence_sections_that_allow_a_private_build_and_forbid_sharing_it(self):
        text = self.page("renodx-hdr")
        self.assertIn("1c", text)
        self.assertIn("2b", text)

    def test_the_renodx_page_gives_the_measured_log_lines_of_the_conflict_and_of_the_fix(self):
        text = self.page("renodx-hdr")
        for needle in ("Params not created", "Failed to replace pipeline", "will insert cbuffer 13 at root_index 23", "will insert cbuffer 13 at root_index 25"):
            self.assertIn(needle, text)

    def test_the_atmospheremod_page_gives_the_pinned_hash_and_the_licence_limits(self):
        text = self.page("atmospheremod")
        self.assertIn("49a8aba9fd0b37cc1bb3b008cc537592a7d865edc7b5fca9feb7d54a1147b4d7", text)
        self.assertIn("https://github.com/tomwhoca/AtmosphereMod/releases/download/v0.7.1/AtmosphereMod-0.7.1.zip", text)

    def test_the_atmospheremod_page_gives_the_measured_numbers_of_the_tested_release(self):
        text = self.page("atmospheremod")
        self.assertIn("Tested with AtmosphereMod 0.5.1 on 1 November 2026 and with 0.7.1 on 9 November 2026", text)
        self.assertIn("0.7.1 patched 3079", text)
        self.assertIn("registered 0.7.1.0", text)

    def test_the_atmospheremod_page_lists_renodx_among_the_installer_arguments(self):
        text = self.page("atmospheremod")
        arguments = text[text.index("The arguments are the mod's own"): text.index("</ul>", text.index("The arguments are the mod's own"))]
        self.assertIn("<code>--renodx</code>, only when a <code>renodx-*.addon64</code> is in the simulator's folder", arguments)

    def test_the_renodx_page_makes_the_reinstall_with_renodx_the_way_and_the_injection_off_the_fallback(self):
        text = self.page("renodx-hdr")
        make_it_run = text[text.index("<h2>Make it run</h2>"): text.index("<h3>Why the two clash</h3>")]
        self.assertIn("Install AtmosphereMod.cmd --renodx", make_it_run)
        self.assertIn("flightsim-on-linux addon install atmospheremod\n", make_it_run)
        self.assertNotIn("InjectGraphicsRootSignature=0", make_it_run)
        self.assertLess(text.index("<h2>With AtmosphereMod older than 0.7.0</h2>"), text.index("<h2>Before 0.7.0: a private build</h2>"))

    def test_the_renodx_page_gives_the_run_with_the_official_option(self):
        text = self.page("renodx-hdr")
        self.assertIn("AtmosphereMod: root parameters in register space 51 (installed with --renodx)", text)
        self.assertIn("RenoDX: compatible, register space 51", text)

    def test_the_renodx_page_gives_the_nexus_page_and_the_hashes_of_the_tested_files(self):
        text = self.page("renodx-hdr")
        self.assertIn("https://www.nexusmods.com/microsoftflightsimulator2024/mods/26", text)
        self.assertIn("226df85c594e8ef6cfb4d416d2bb20d598b32e927d60f6f0a914a0605c3b4af2", text)
        self.assertIn("304008e72d2fa6a9efe23bea527dec79525ae82decd7c16ef0c36bb27eeecb7e", text)

    def test_the_check_line_counts_on_the_pages_match_what_the_checks_print(self):
        self.assertIn("It prints six lines", self.page("atmospheremod"))
        self.assertIn("A release from 0.7.0 on adds a line about the register space, and a log to read adds one more", self.page("atmospheremod"))
        self.assertIn("five lines", self.page("renodx-hdr"))


if __name__ == "__main__":
    unittest.main()
