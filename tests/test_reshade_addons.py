import hashlib
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fsol import addons
from tests.test_machine import FakeMachine, write

ROOT = Path(__file__).resolve().parent.parent
RESHADE = ROOT / "addons" / "reshade"
GLASS = ROOT / "addons" / "glass-cockpit-dlss-fix"
CACHE_PATH = "C:\\users\\steamuser\\AppData\\Local\\Temp\\ReShade"
DXGI = b"MZ ReShade 6.8.0 dxgi.dll"
INI = "[GENERAL]\r\nEffectSearchPaths=.\\reshade-shaders\\Shaders\r\nIntermediateCachePath=C:\\Users\\pilot\\AppData\\Local\\Temp\\ReShade\r\nPresetPath=.\\ReShadePreset.ini\r\n"
PRESET = "Techniques=\r\n"
VKD3D_PATCHES = ("root-signature-reuse-identical-blob.patch", "descriptor-heap-covers-its-cpu-handles.patch")
D3D12CORE = "lib/wine/vkd3d-proton/x86_64-windows/d3d12core.dll"
NVIDIA_GPU = ("PHYSICAL_DEVICE_TYPE_DISCRETE_GPU", "NVIDIA GeForce RTX 4080", "0x10de", "0x2704")
AMD_GPU = ("PHYSICAL_DEVICE_TYPE_DISCRETE_GPU", "AMD Radeon RX 7800 XT", "0x1002", "0x747e")
AMD_INTEGRATED = ("PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU", "AMD Radeon Graphics (RADV RAPHAEL_MENDOCINO)", "0x1002", "0x164e")
INTEL_INTEGRATED = ("PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU", "Intel(R) UHD Graphics", "0x8086", "0x9a49")
LLVMPIPE = ("PHYSICAL_DEVICE_TYPE_CPU", "llvmpipe (LLVM 19.1.7, 256 bits)", "0x10005", "0x0")
GLASS_INI = "[debug]\r\ndescriptor_tracking=0\r\n[overlay]\r\nenabled=1\r\n[experimental]\r\ndetail_clamp=0\r\ndark_screen_fix=0\r\n[hotkeys]\r\nsettings_window=Ctrl+Shift+F6\r\n"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return path.read_bytes().decode()


def stub(path, text):
    write(path, text)
    path.chmod(0o755)
    return path


def summary(*gpus):
    blocks = []
    for number, (kind, name, vendor, device) in enumerate(gpus):
        blocks.append(f"GPU{number}:\n\tapiVersion         = 1.4.303\n\tvendorID           = {vendor}\n\tdeviceID           = {device}\n\tdeviceType         = {kind}\n\tdeviceName         = {name}\n")
    return "Devices:\n========\n" + "\n".join(blocks)


def tree(root):
    return sorted(str(path.relative_to(root)) + (":" + sha256(path.read_bytes()) if path.is_file() and not path.is_symlink() else "") for path in root.rglob("*"))


class SimulatorCase(unittest.TestCase):
    ADDON = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake = FakeMachine(self.root).build()
        self.found = self.fake.discover()
        self.sim = self.found.sim_install
        self.proton = self.fake.tool
        self.windows = self.root / "windows-reshade"
        write(self.windows / "ReShade.ini", "")
        (self.windows / "ReShade.ini").write_bytes(INI.encode())
        (self.windows / "dxgi.dll").write_bytes(DXGI)
        write(self.windows / "ReShadePreset.ini", PRESET)
        write(self.sim / "FlightSimulator2024.exe", "MZ")

    def tearDown(self):
        self.temp.cleanup()

    def environment(self, **extra):
        env = {
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.fake.home),
            "FSOL_PREFIX": str(self.found.prefix),
            "FSOL_PROTON": str(self.found.proton),
            "FSOL_COMPATDATA": str(self.found.compatdata),
            "FSOL_COMMUNITY": str(self.found.community),
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

    def removed(self, name):
        return sorted(self.backup("reshade", "removed").glob(f"{name}.*"))


class ReShadeShapeTest(unittest.TestCase):
    def test_the_manifest_says_what_reshade_is(self):
        addon = addons.load(RESHADE)
        self.assertEqual((addon.id, addon.needs, addon.tested_version, addon.touches_addon_files), ("reshade", ["simulator-closed"], "6.8.0", False))
        self.assertEqual(addon.installed, {"sim": ["dxgi.dll"]})
        self.assertEqual(addon.install["kind"], "installer")
        self.assertTrue(addon.can_install)
        self.assertEqual(addon.shortcuts, [])

    def test_it_counts_as_installed_where_dxgi_dll_is(self):
        with tempfile.TemporaryDirectory() as temp:
            found = FakeMachine(Path(temp)).build().discover()
            addon = addons.load(RESHADE)
            self.assertFalse(addons.is_installed(addon, found))
            write(found.sim_install / "dxgi.dll", "MZ")
            self.assertTrue(addons.is_installed(addon, found))


class ReShadeInstallTest(SimulatorCase):
    ADDON = "reshade"

    def install(self, given=None, **extra):
        return self.script("install", FSOL_INSTALLER=str(given or self.windows / "dxgi.dll"), **extra)

    def test_it_copies_the_three_files_and_rewrites_only_the_cache_path(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.sim / "dxgi.dll").read_bytes(), DXGI)
        self.assertEqual(read(self.sim / "ReShadePreset.ini"), PRESET)
        expected = INI.replace("C:\\Users\\pilot\\AppData\\Local\\Temp\\ReShade", CACHE_PATH)
        self.assertEqual((self.sim / "ReShade.ini").read_bytes(), expected.encode())

    def test_a_folder_works_as_well_as_a_file_in_it(self):
        self.assertEqual(self.install(self.windows).returncode, 0)
        self.assertEqual((self.sim / "dxgi.dll").read_bytes(), DXGI)

    def test_the_line_endings_of_an_ini_with_plain_line_feeds_stay(self):
        (self.windows / "ReShade.ini").write_text("[GENERAL]\nIntermediateCachePath=C:\\Temp\nOther=1\n")
        self.install()
        self.assertEqual(read(self.sim / "ReShade.ini"), f"[GENERAL]\nIntermediateCachePath={CACHE_PATH}\nOther=1\n")

    def test_no_shaders_are_copied_even_when_the_windows_install_has_them(self):
        write(self.windows / "reshade-shaders" / "Shaders" / "Some.fx", "float4 main();")
        write(self.windows / "reshade-shaders" / "Textures" / "noise.png", "png")
        self.install()
        self.assertFalse((self.sim / "reshade-shaders").exists())
        self.assertEqual(sorted(path.name for path in self.sim.iterdir() if path.name != "FlightSimulator2024.exe"), ["ReShade.ini", "ReShadePreset.ini", "dxgi.dll"])

    def test_it_touches_nothing_else_in_the_simulator_folder(self):
        write(self.sim / "UserCfg.opt", "keep")
        write(self.sim / "glass-cockpit-dlss-fix.addon64", "keep")
        before = {path: path.read_bytes() for path in self.sim.iterdir() if path.is_file()}
        self.install()
        after = {path: path.read_bytes() for path in self.sim.iterdir() if path.is_file()}
        for path, data in before.items():
            self.assertEqual(after[path], data, path.name)
        self.assertEqual(sorted(set(after) - set(before)), sorted(self.sim / name for name in ("ReShade.ini", "ReShadePreset.ini", "dxgi.dll")))

    def test_a_preset_the_simulator_folder_already_has_is_kept(self):
        write(self.sim / "ReShadePreset.ini", "Techniques=Mine\r\n")
        result = self.install()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(read(self.sim / "ReShadePreset.ini"), "Techniques=Mine\r\n")
        self.assertIn("ReShadePreset.ini in the simulator folder stays", result.stdout)

    def test_an_ini_the_simulator_folder_already_has_is_kept_in_the_backup_folder(self):
        write(self.sim / "ReShade.ini", "[GENERAL]\nMine=1\n")
        self.install()
        self.assertEqual(read(self.backup("reshade", "ReShade.ini.previous")), "[GENERAL]\nMine=1\n")

    def test_a_different_dxgi_dll_already_there_stops_the_install_and_stays(self):
        (self.sim / "dxgi.dll").write_bytes(b"MZ some other dxgi")
        result = self.install()
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.sim / "dxgi.dll").read_bytes(), b"MZ some other dxgi")
        self.assertFalse((self.sim / "ReShade.ini").exists())

    def test_the_same_dxgi_dll_already_there_is_fine(self):
        (self.sim / "dxgi.dll").write_bytes(DXGI)
        self.assertEqual(self.install().returncode, 0)

    def test_each_missing_file_of_the_windows_install_is_named(self):
        for name in ("dxgi.dll", "ReShade.ini", "ReShadePreset.ini"):
            with self.subTest(name):
                moved = self.root / "moved"
                (self.windows / name).rename(moved)
                result = self.install(self.windows)
                (moved).rename(self.windows / name)
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"{self.windows / name} does not exist", result.stderr)
                self.assertFalse((self.sim / "dxgi.dll").exists())

    def test_it_refuses_while_the_simulator_runs(self):
        result = self.install(FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("close the simulator", result.stderr)
        self.assertFalse((self.sim / "dxgi.dll").exists())

    def test_no_argument_says_what_to_give(self):
        result = self.script("install", FSOL_INSTALLER="")
        self.assertEqual(result.returncode, 1)


class ReShadeCheckTest(SimulatorCase):
    ADDON = "reshade"

    def setUp(self):
        super().setUp()
        self.script("install", FSOL_INSTALLER=str(self.windows / "dxgi.dll"))

    def lines(self, result):
        return [line.split()[0] for line in result.stdout.splitlines()]

    def test_a_machine_with_reshade_installed_passes_with_two_ok_lines(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.lines(result), ["ok", "ok"])

    def test_the_log_line_appears_when_reshade_has_logged_its_load(self):
        write(self.sim / "ReShade.log", "00:00 loaded from 'S:\\steamapps\\common\\MSFS2024\\dxgi.dll'\n")
        result = self.script("check")
        self.assertEqual(self.lines(result), ["ok", "ok", "ok"])

    def test_without_dxgi_dll_it_fails(self):
        (self.sim / "dxgi.dll").unlink()
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.lines(result)[0], "FAIL")

    def test_a_dxgi_dll_that_is_not_reshade_fails(self):
        (self.sim / "dxgi.dll").write_bytes(b"MZ DXVK")
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is missing or is not ReShade's", result.stdout)

    def test_a_cache_path_from_the_windows_install_fails(self):
        (self.sim / "ReShade.ini").write_bytes(INI.encode())
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.lines(result), ["ok", "FAIL"])
        self.assertIn("C:\\Users\\pilot", result.stdout)

    def test_an_ini_without_a_cache_path_passes(self):
        (self.sim / "ReShade.ini").write_text("[GENERAL]\nPresetPath=.\\ReShadePreset.ini\n")
        self.assertEqual(self.script("check").returncode, 0)

    def test_a_missing_ini_fails(self):
        (self.sim / "ReShade.ini").unlink()
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ReShade.ini is missing", result.stdout)

    def test_check_changes_nothing(self):
        before = tree(self.root)
        self.script("check")
        self.assertEqual(tree(self.root), before)


class ReShadeApplyAndUndoTest(SimulatorCase):
    ADDON = "reshade"

    def test_apply_changes_nothing_and_says_there_is_no_launch_option(self):
        before = tree(self.root)
        result = self.script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertIn("no launch option", result.stdout)
        self.assertEqual(tree(self.root), before)

    def test_undo_takes_the_three_files_out_and_keeps_them_in_the_backup_folder(self):
        self.script("install", FSOL_INSTALLER=str(self.windows / "dxgi.dll"))
        write(self.sim / "ReShadePreset.ini", "Techniques=Mine\r\n")
        write(self.sim / "UserCfg.opt", "keep")
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(path.name for path in self.sim.iterdir()), ["FlightSimulator2024.exe", "UserCfg.opt"])
        self.assertEqual(self.removed("dxgi.dll")[0].read_bytes(), DXGI)
        self.assertEqual(read(self.removed("ReShadePreset.ini")[0]), "Techniques=Mine\r\n")
        self.assertEqual(len(self.removed("ReShade.ini")), 1)

    def test_undo_restores_the_ini_the_simulator_folder_had_before_and_keeps_the_one_it_took_out(self):
        write(self.sim / "ReShade.ini", "[GENERAL]\nMine=1\n")
        self.script("install", FSOL_INSTALLER=str(self.windows / "dxgi.dll"))
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read(self.sim / "ReShade.ini"), "[GENERAL]\nMine=1\n")
        self.assertFalse(self.backup("reshade", "ReShade.ini.previous").exists())
        self.assertEqual(len(self.removed("ReShade.ini")), 1)
        self.assertNotEqual(read(self.removed("ReShade.ini")[0]), "[GENERAL]\nMine=1\n")

    def test_a_second_install_does_not_replace_the_ini_kept_from_before_the_first(self):
        write(self.sim / "ReShade.ini", "[GENERAL]\nMine=1\n")
        self.script("install", FSOL_INSTALLER=str(self.windows / "dxgi.dll"))
        self.script("install", FSOL_INSTALLER=str(self.windows / "dxgi.dll"))
        self.assertEqual(read(self.backup("reshade", "ReShade.ini.previous")), "[GENERAL]\nMine=1\n")

    def test_undo_never_overwrites_what_an_earlier_undo_kept(self):
        for _ in range(3):
            self.script("install", FSOL_INSTALLER=str(self.windows / "dxgi.dll"))
            write(self.sim / "ReShadePreset.ini", "Techniques=Mine\r\n")
            self.assertEqual(self.script("undo").returncode, 0)
        for name in ("dxgi.dll", "ReShade.ini", "ReShadePreset.ini"):
            self.assertEqual(len(self.removed(name)), 3, name)

    def test_undo_twice_is_harmless(self):
        self.script("install", FSOL_INSTALLER=str(self.windows / "dxgi.dll"))
        self.script("undo")
        self.assertEqual(self.script("undo").returncode, 0)

    def test_undo_then_check_fails(self):
        self.script("install", FSOL_INSTALLER=str(self.windows / "dxgi.dll"))
        self.script("undo")
        self.assertEqual(self.script("check").returncode, 1)


class GlassShapeTest(unittest.TestCase):
    def test_the_manifest_says_what_the_add_on_needs(self):
        addon = addons.load(GLASS)
        self.assertEqual((addon.id, addon.tested_version, addon.tested_on, addon.touches_addon_files), ("glass-cockpit-dlss-fix", "1.0.2", "2026-10-20", True))
        self.assertEqual(sorted(addon.needs), ["proton-fs2024", "simulator-closed"])
        self.assertEqual(addon.installed, {"sim": ["glass-cockpit-dlss-fix.addon64"]})
        self.assertEqual(addon.install["kind"], "native")
        self.assertTrue(addon.can_install)
        self.assertFalse(addon.install_needs_file)

    def test_the_pinned_download_is_the_public_release_asset(self):
        text = (GLASS / "install").read_text()
        self.assertIn('GLASS_VERSION="1.0.2"', text)
        self.assertIn("https://github.com/IFreemz/msfs2024-glass-cockpit-dlss-fix/releases/download/v$GLASS_VERSION/glass-cockpit-dlss-fix.addon64", text)
        self.assertIn('GLASS_SHA256="9474b66e2e22bc005233463574fc9ba9c107d4bfa8364e5c3478588f0e8f2036"', text)

    def test_the_vkd3d_patches_it_checks_exist_in_this_repository(self):
        for patch in VKD3D_PATCHES:
            self.assertTrue((ROOT / "patches" / "vkd3d-proton" / patch).is_file(), patch)


class GlassCase(SimulatorCase):
    ADDON = "glass-cockpit-dlss-fix"

    def setUp(self):
        super().setUp()
        self.ini = self.sim / "glass-cockpit-dlss-fix.ini"
        write(self.sim / "dxgi.dll", "MZ ReShade")
        write(self.sim / "glass-cockpit-dlss-fix.addon64", "addon")
        self.gpus(NVIDIA_GPU)
        self.build_record()

    def gpus(self, *gpus):
        text = summary(*gpus)
        stub(self.root / "bin" / "vulkaninfo", f"#!/bin/sh\ncat <<'EOF'\n{text}EOF\n")

    def build_record(self, patches=VKD3D_PATCHES, module=b"MZ d3d12core of the build", recorded=None):
        path = self.proton / "files" / D3D12CORE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(module)
        lines = ["release cachyos-11.0-20261005-slr x86_64"]
        lines += [f"vkd3d-patch {sha256((ROOT / 'patches' / 'vkd3d-proton' / patch).read_bytes())} {patch}" for patch in patches]
        lines.append(f"module {recorded or sha256(module)} {D3D12CORE}")
        (self.proton / "fs2024-build.txt").write_text("\n".join(lines) + "\n")

    def write_ini(self, text=GLASS_INI):
        self.ini.write_bytes(text.encode())

    def lines(self, result):
        return [line.split()[0] for line in result.stdout.splitlines()]


class GlassApplyTest(GlassCase):
    def test_on_nvidia_it_sets_the_two_lines_and_keeps_every_other_line(self):
        self.write_ini()
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = GLASS_INI.replace("descriptor_tracking=0", "descriptor_tracking=1").replace("dark_screen_fix=0", "dark_screen_fix=1")
        self.assertEqual(self.ini.read_bytes(), expected.encode())
        self.assertIn("because the GPU is NVIDIA", result.stdout)

    def test_on_amd_only_the_dark_screen_line_is_set(self):
        self.gpus(AMD_GPU)
        self.write_ini()
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = read(self.ini)
        self.assertIn("dark_screen_fix=1", text)
        self.assertIn("descriptor_tracking=0", text)
        self.assertIn("is not NVIDIA", result.stdout)

    def test_on_intel_the_tracking_line_stays_off(self):
        self.gpus(INTEL_INTEGRATED)
        self.assertEqual(self.script("apply").returncode, 0)
        self.assertNotIn("descriptor_tracking", read(self.ini))

    def test_two_gpus_choose_the_discrete_one_and_here_that_is_nvidia(self):
        self.gpus(AMD_INTEGRATED, NVIDIA_GPU, LLVMPIPE)
        self.script("apply")
        self.assertIn("descriptor_tracking=1", read(self.ini))

    def test_two_gpus_where_the_discrete_one_is_not_nvidia_leave_tracking_off(self):
        self.gpus(AMD_GPU, INTEL_INTEGRATED)
        self.script("apply")
        self.assertNotIn("descriptor_tracking", read(self.ini))

    def test_two_discrete_gpus_leave_the_choice_to_the_reader_and_say_so(self):
        self.gpus(NVIDIA_GPU, AMD_GPU)
        result = self.script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("descriptor_tracking", read(self.ini))
        self.assertIn("cannot pick one GPU", result.stdout)

    def test_it_creates_the_ini_with_only_the_two_sections_when_there_is_none(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read(self.ini), "[experimental]\ndark_screen_fix=1\n[debug]\ndescriptor_tracking=1\n")

    def test_it_adds_a_missing_key_to_an_existing_section_and_a_missing_section_to_the_end(self):
        self.write_ini("[overlay]\r\nenabled=1\r\n[experimental]\r\ndetail_clamp=0\r\n")
        self.script("apply")
        self.assertEqual(read(self.ini), "[overlay]\r\nenabled=1\r\n[experimental]\r\ndark_screen_fix=1\r\ndetail_clamp=0\r\n[debug]\r\ndescriptor_tracking=1\r\n")

    def test_the_key_is_found_without_regard_to_case_and_spaces(self):
        self.write_ini("[DEBUG]\r\nDescriptor_Tracking = 0\r\n[Experimental]\r\ndark_screen_fix = 0\r\n")
        self.script("apply")
        self.assertEqual(read(self.ini), "[DEBUG]\r\ndescriptor_tracking=1\r\n[Experimental]\r\ndark_screen_fix=1\r\n")

    def test_a_key_of_another_section_is_not_taken_for_ours(self):
        self.write_ini("[overlay]\r\ndark_screen_fix=0\r\n[experimental]\r\ndetail_clamp=0\r\n")
        self.script("apply")
        text = read(self.ini)
        self.assertIn("[overlay]\r\ndark_screen_fix=0\r\n", text)
        self.assertIn("[experimental]\r\ndark_screen_fix=1\r\n", text)

    def test_apply_twice_gives_the_same_file(self):
        self.write_ini()
        self.script("apply")
        first = self.ini.read_bytes()
        self.script("apply")
        self.assertEqual(self.ini.read_bytes(), first)

    def test_without_the_add_on_it_says_to_install_it_and_writes_nothing(self):
        (self.sim / "glass-cockpit-dlss-fix.addon64").unlink()
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install glass-cockpit-dlss-fix", result.stderr)
        self.assertFalse(self.ini.exists())

    def test_a_missing_vulkaninfo_leaves_tracking_alone_and_says_to_set_it_by_hand(self):
        spec = importlib.util.spec_from_file_location("glass_parts", GLASS / "parts" / "glass.py")
        glass = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(glass)
        with mock.patch.object(glass.shutil, "which", return_value=None):
            vendor, detail = glass.simulator_gpu_vendor()
        self.assertIsNone(vendor)
        self.assertIn("vulkaninfo is missing", detail)


class GlassUndoTest(GlassCase):
    def test_undo_restores_an_ini_that_existed_byte_for_byte(self):
        self.write_ini()
        self.script("apply")
        self.assertNotEqual(self.ini.read_bytes(), GLASS_INI.encode())
        result = self.script("undo")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.ini.read_bytes(), GLASS_INI.encode())

    def test_undo_removes_an_ini_that_apply_created(self):
        self.script("apply")
        self.script("undo")
        self.assertFalse(self.ini.exists())

    def test_the_second_apply_does_not_replace_the_first_backup(self):
        self.write_ini()
        self.script("apply")
        self.script("apply")
        self.script("undo")
        self.assertEqual(self.ini.read_bytes(), GLASS_INI.encode())

    def test_undo_without_apply_says_so_and_changes_nothing(self):
        self.write_ini()
        before = tree(self.root)
        result = self.script("undo")
        self.assertEqual(result.returncode, 0)
        self.assertIn("apply did not run", result.stdout)
        self.assertEqual(tree(self.root), before)

    def test_undo_leaves_the_add_on_and_reshade_where_they_are(self):
        self.script("apply")
        self.script("undo")
        self.assertTrue((self.sim / "glass-cockpit-dlss-fix.addon64").exists())
        self.assertTrue((self.sim / "dxgi.dll").exists())


class GlassCheckTest(GlassCase):
    def applied(self):
        self.write_ini()
        self.script("apply")

    def test_a_fixed_machine_on_nvidia_passes_with_seven_ok_lines(self):
        self.applied()
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.lines(result), ["ok"] * 7)

    def test_a_fixed_machine_on_amd_passes_without_the_tracking_line(self):
        self.gpus(AMD_GPU)
        self.applied()
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("descriptor_tracking is not needed", result.stdout)
        self.assertEqual(self.lines(result), ["ok"] * 7)

    def test_check_fails_before_apply(self):
        self.write_ini()
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.lines(result).count("FAIL"), 2)

    def test_dark_screen_fix_off_fails_alone(self):
        self.applied()
        self.ini.write_text(read(self.ini).replace("dark_screen_fix=1", "dark_screen_fix=0"))
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([line for line in result.stdout.splitlines() if line.startswith("FAIL")], [line for line in result.stdout.splitlines() if "dark_screen_fix" in line])

    def test_tracking_off_on_nvidia_fails_alone(self):
        self.applied()
        self.ini.write_text(read(self.ini).replace("descriptor_tracking=1", "descriptor_tracking=0"))
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        failures = [line for line in result.stdout.splitlines() if line.startswith("FAIL")]
        self.assertEqual(len(failures), 1)
        self.assertIn("descriptor_tracking", failures[0])

    def test_two_discrete_gpus_make_the_tracking_line_fail_with_the_reason(self):
        self.applied()
        self.gpus(NVIDIA_GPU, AMD_GPU)
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot tell whether descriptor_tracking is needed", result.stdout)

    def test_a_proton_built_without_the_root_signature_patch_fails(self):
        self.applied()
        self.build_record(patches=VKD3D_PATCHES[1:])
        result = self.script("check")
        failures = [line for line in result.stdout.splitlines() if line.startswith("FAIL")]
        self.assertEqual(len(failures), 1)
        self.assertIn(VKD3D_PATCHES[0], failures[0])

    def test_a_proton_built_without_the_descriptor_heap_patch_fails(self):
        self.applied()
        self.build_record(patches=VKD3D_PATCHES[:1])
        result = self.script("check")
        failures = [line for line in result.stdout.splitlines() if line.startswith("FAIL")]
        self.assertEqual(len(failures), 1)
        self.assertIn(VKD3D_PATCHES[1], failures[0])

    def test_a_patch_the_build_recorded_with_other_contents_fails(self):
        self.applied()
        record = read(self.proton / "fs2024-build.txt").replace(sha256((ROOT / "patches" / "vkd3d-proton" / VKD3D_PATCHES[0]).read_bytes()), "0" * 64)
        (self.proton / "fs2024-build.txt").write_text(record)
        result = self.script("check")
        self.assertEqual(result.returncode, 1)

    def test_a_d3d12core_that_differs_from_the_build_record_fails(self):
        self.applied()
        self.build_record(recorded=sha256(b"MZ another d3d12core"))
        result = self.script("check")
        failures = [line for line in result.stdout.splitlines() if line.startswith("FAIL")]
        self.assertEqual(len(failures), 1)
        self.assertIn("d3d12core.dll", failures[0])

    def test_a_proton_without_a_build_record_fails_on_all_three_proton_lines(self):
        self.applied()
        (self.proton / "fs2024-build.txt").unlink()
        result = self.script("check")
        self.assertEqual(self.lines(result).count("FAIL"), 3)

    def test_no_reshade_or_no_add_on_fails(self):
        self.applied()
        (self.sim / "dxgi.dll").unlink()
        (self.sim / "glass-cockpit-dlss-fix.addon64").unlink()
        result = self.script("check")
        self.assertEqual(self.lines(result)[:2], ["FAIL", "FAIL"])

    def test_no_proton_fails_instead_of_reading_the_current_folder(self):
        self.applied()
        result = self.script("check", FSOL_PROTON="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL no Proton found", result.stdout)

    def test_check_changes_nothing(self):
        self.applied()
        before = tree(self.root)
        self.script("check")
        self.assertEqual(tree(self.root), before)


class GlassPageTest(unittest.TestCase):
    def test_the_page_says_the_check_prints_seven_lines_on_every_gpu(self):
        text = (ROOT / "site" / "pages" / "glass-cockpit-dlss-fix.html").read_text()
        self.assertIn("It prints seven lines, on NVIDIA and elsewhere", text)
        self.assertNotIn("six elsewhere", text)


class GlassDownloadTest(GlassCase):
    URL = "https://example.test/glass-cockpit-dlss-fix.addon64"

    def setUp(self):
        super().setUp()
        self.asset = b"MZ the addon64 of a release"
        self.sha = sha256(self.asset)
        self.log = self.root / "curl.log"
        stub(self.root / "bin" / "curl", f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.log}"\nout=""\nwhile [ $# -gt 0 ]; do [ "$1" = --output ] && out="$2"; shift; done\nprintf %s "$CURL_BODY" > "$out"\n')

    def bash(self, text, **extra):
        env = self.environment(CURL_BODY=self.asset.decode(), **extra)
        return subprocess.run(["bash", "-c", f'. "{GLASS}/parts/download.sh"; {text}'], env=env, capture_output=True, text=True)

    def test_a_download_with_the_right_hash_is_kept_in_the_cache_folder(self):
        target = self.root / "cache" / "downloads" / "file.addon64"
        result = self.bash(f'glass_fetch "{self.URL}" {self.sha} "{target}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_bytes(), self.asset)
        self.assertFalse(Path(f"{target}.part").exists())
        self.assertIn(self.URL, self.log.read_text())
        self.assertIn("--fail", self.log.read_text())

    def test_a_file_already_downloaded_is_not_downloaded_again(self):
        target = self.root / "cache" / "downloads" / "file.addon64"
        self.bash(f'glass_fetch "{self.URL}" {self.sha} "{target}"')
        self.log.unlink()
        result = self.bash(f'glass_fetch "{self.URL}" {self.sha} "{target}"')
        self.assertEqual(result.returncode, 0)
        self.assertIn("already downloaded", result.stdout)
        self.assertFalse(self.log.exists())

    def test_a_download_with_another_hash_is_thrown_away_and_nothing_is_kept(self):
        target = self.root / "cache" / "downloads" / "file.addon64"
        result = self.bash(f'glass_fetch "{self.URL}" {"0" * 64} "{target}"')
        self.assertEqual(result.returncode, 1)
        self.assertFalse(target.exists())
        self.assertFalse(Path(f"{target}.part").exists())
        self.assertIn("not the file this add-on was tested with", result.stderr)

    def test_a_cached_file_with_another_hash_is_downloaded_again(self):
        target = self.root / "cache" / "downloads" / "file.addon64"
        write(target, "an old or damaged file")
        result = self.bash(f'glass_fetch "{self.URL}" {self.sha} "{target}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_bytes(), self.asset)

    def test_installing_a_file_with_the_right_hash_copies_it_beside_dxgi_dll(self):
        file = self.root / "given.addon64"
        file.write_bytes(self.asset)
        result = self.bash(f'glass_install_file "{file}" {self.sha} "{self.sim}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.sim / "glass-cockpit-dlss-fix.addon64").read_bytes(), self.asset)

    def test_installing_a_file_with_another_hash_refuses_and_copies_nothing(self):
        (self.sim / "glass-cockpit-dlss-fix.addon64").unlink()
        file = self.root / "given.addon64"
        file.write_bytes(self.asset)
        result = self.bash(f'glass_install_file "{file}" {"0" * 64} "{self.sim}"')
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.sim / "glass-cockpit-dlss-fix.addon64").exists())


class GlassInstallScriptTest(GlassCase):
    def setUp(self):
        super().setUp()
        (self.sim / "glass-cockpit-dlss-fix.addon64").unlink()
        self.log = self.root / "curl.log"
        stub(self.root / "bin" / "curl", f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.log}"\nout=""\nwhile [ $# -gt 0 ]; do [ "$1" = --output ] && out="$2"; shift; done\nprintf junk > "$out"\n')

    def test_without_reshade_it_stops_and_says_to_install_it_first(self):
        (self.sim / "dxgi.dll").unlink()
        result = self.script("install")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install reshade", result.stderr)
        self.assertFalse(self.log.exists())

    def test_it_refuses_while_the_simulator_runs(self):
        result = self.script("install", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.log.exists())

    def test_it_downloads_the_pinned_url_and_installs_nothing_when_the_hash_differs(self):
        result = self.script("install")
        self.assertEqual(result.returncode, 1)
        self.assertIn("https://github.com/IFreemz/msfs2024-glass-cockpit-dlss-fix/releases/download/v1.0.2/glass-cockpit-dlss-fix.addon64", self.log.read_text())
        self.assertFalse((self.sim / "glass-cockpit-dlss-fix.addon64").exists())
        self.assertEqual(list((self.root / "cache" / "downloads").iterdir()), [])

    def test_a_file_given_by_hand_with_another_hash_is_refused(self):
        file = self.root / "newer.addon64"
        file.write_bytes(b"MZ a newer release")
        result = self.script("install", FSOL_INSTALLER=str(file))
        self.assertEqual(result.returncode, 1)
        self.assertIn("not the release this add-on was tested with", result.stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.sim / "glass-cockpit-dlss-fix.addon64").exists())


if __name__ == "__main__":
    unittest.main()
