import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARTS = ROOT / "addons" / "fenix-a320" / "parts"
INSTALLER = ROOT / "addons" / "fenix-a320" / "installer"
SESSION = PARTS / "installer-session.sh"

HARNESS = """
set -u
. "$PARTS/common.sh"
. "$PARTS/dxvk.sh"
. "$PARTS/geometry.sh"
"""

FILTER = 'dxvk.deviceFilter = "NVIDIA GeForce RTX 4080"'
SETTINGS = 'user_settings = {\n    "WINE_D2D1_GEOMETRY_PROVIDER": "%s",\n}\n'
CONFIG = "<config>\n  <displayMode>%s</displayMode>\n</config>\n"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


GEOMETRY_STUBS = r"""
sha() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }
GEOMETRY_CAB_SHA256="$(sha cab)"
GEOMETRY_MEMBER0_SHA256="$(sha member0)"
GEOMETRY_MEMBER1_SHA256="$(sha member1)"
GEOMETRY_BASIS_SHA256="$(sha basis)"
fenix_download() { echo download >> "$LOG"; echo "/nowhere/$3"; }
fenix_wineserver_kill() { :; }
x86_64-w64-mingw32-gcc() {
  local out
  echo gcc >> "$LOG"
  while [ $# -gt 0 ]; do
    [ "$1" != -o ] || out="$2"
    shift
  done
  echo exe > "$out"
}
fenix_wine() {
  local out="${!#}" content
  [ "$2" != wineboot ] || return 0
  out="${out#Z:}"
  out="${out//\\//}"
  case "$5" in
    Windows6.1*) content=cab ;;
    0) content=member0 ;;
    1) content=member1 ;;
    -) content=basis ;;
    *basis.dll) content="$PAYLOAD" ;;
  esac
  printf '%s' "$content" > "$out"
}
"""


class PartsCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.compatdata = self.root / "compatdata"
        self.prefix = self.compatdata / "pfx"
        self.deps = self.prefix / "drive_c" / "Program Files" / "FenixSim A320" / "deps"
        self.config = self.prefix / "drive_c" / "ProgramData" / "Fenix" / "FenixSim A320" / "fenixConfig.xml"
        self.backup = self.compatdata / "flightsim-on-linux-backup" / "fenix-a320"
        self.cache = self.root / "cache"
        self.dxvk = self.cache / "dxvk-fenix"
        self.proton = self.root / "proton"
        self.stub = self.root / "discrete-gpu"
        self.deps.mkdir(parents=True)
        self.proton.mkdir()
        self.gpus("one")

    def gpus(self, kind, name="NVIDIA GeForce RTX 4080"):
        bodies = {
            "one": "exit 0",
            "two": f'echo "{name}"',
            "ambiguous": 'echo "cannot pick one GPU: 2 GPUs and 0 distinct discrete ones: A; B" >&2; exit 3',
            "novulkaninfo": 'echo "vulkaninfo is missing; install your distribution\'s vulkan-tools package" >&2; exit 2',
        }
        self.stub.write_text("#!/bin/sh\n[ $# -eq 0 ] || exit 64\n" + bodies[kind] + "\n")
        self.stub.chmod(0o755)

    def mode(self, mode):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(CONFIG % mode)

    def build_cache(self, d3d11=b"patched d3d11", dxgi=b"patched dxgi"):
        self.dxvk.mkdir(parents=True, exist_ok=True)
        (self.dxvk / "d3d11.dll").write_bytes(d3d11)
        (self.dxvk / "dxgi.dll").write_bytes(dxgi)

    def run_part(self, command, **extra):
        env = {
            **os.environ,
            "PARTS": str(PARTS),
            "FSOL_ROOT": str(ROOT),
            "FSOL_PREFIX": str(self.prefix),
            "FSOL_COMPATDATA": str(self.compatdata),
            "FSOL_PROTON": str(self.proton),
            "FSOL_CACHE": str(self.cache),
            "FSOL_DISCRETE_GPU": str(self.stub),
            **extra,
        }
        result = subprocess.run(["bash", "-c", HARNESS + command], capture_output=True, text=True, env=env)
        return result.returncode, result.stdout

    def lines(self, output, prefix):
        return [line for line in output.splitlines() if line.startswith(prefix)]


class DxvkCheckTest(PartsCase):
    def test_one_gpu_in_cpu_mode_needs_neither_filter_nor_dlls(self):
        self.mode("CPU")
        code, output = self.run_part("dxvk_check")
        self.assertEqual(code, 0, output)
        self.assertIn("ok one GPU, no device filter needed", output)
        self.assertIn("ok displays render on the CPU, no DXVK needed", output)

    def test_two_gpus_need_the_filter_line_naming_the_discrete_one(self):
        self.gpus("two")
        self.mode("CPU")
        code, output = self.run_part("dxvk_check")
        self.assertNotEqual(code, 0)
        self.assertEqual(len(self.lines(output, "FAIL device filter")) + len(self.lines(output, "FAIL dxvk.conf")), 1, output)
        (self.deps / "dxvk.conf").write_text(FILTER + "\n")
        code, output = self.run_part("dxvk_check")
        self.assertEqual(code, 0, output)
        self.assertIn("ok dxvk.conf names the GPU: NVIDIA GeForce RTX 4080", output)

    def test_a_filter_that_is_only_part_of_the_name_fails(self):
        self.gpus("two")
        self.mode("CPU")
        (self.deps / "dxvk.conf").write_text('dxvk.deviceFilter = "RTX 4080"\n')
        code, output = self.run_part("dxvk_check")
        self.assertNotEqual(code, 0)
        self.assertEqual(len(self.lines(output, "FAIL dxvk.conf in deps does not contain")), 1, output)

    def test_a_filter_line_with_windows_line_endings_passes(self):
        self.gpus("two")
        self.mode("CPU")
        (self.deps / "dxvk.conf").write_bytes((FILTER + "\r\n").encode())
        code, output = self.run_part("dxvk_check")
        self.assertEqual(code, 0, output)

    def test_ambiguous_gpus_fail_with_the_tool_message(self):
        self.gpus("ambiguous")
        self.mode("CPU")
        code, output = self.run_part("dxvk_check")
        self.assertNotEqual(code, 0)
        self.assertIn("FAIL device filter: cannot pick one GPU", output)

    def test_missing_vulkaninfo_fails_with_the_tool_message(self):
        self.gpus("novulkaninfo")
        self.mode("CPU")
        code, output = self.run_part("dxvk_check")
        self.assertNotEqual(code, 0)
        self.assertIn("FAIL device filter: vulkaninfo is missing", output)

    def test_a_missing_fenix_config_asks_to_start_the_fenix(self):
        code, output = self.run_part("dxvk_check")
        self.assertNotEqual(code, 0)
        self.assertIn("FAIL fenixConfig.xml is missing; start the Fenix once and sign in", output)

    def test_an_unknown_display_mode_fails(self):
        self.mode("TOASTER")
        code, output = self.run_part("dxvk_check")
        self.assertNotEqual(code, 0)
        self.assertIn("FAIL displayMode in fenixConfig.xml is 'TOASTER'", output)

    def test_gpu_mode_without_the_cache_says_to_build_dxvk(self):
        self.mode("GPU")
        code, output = self.run_part("dxvk_check")
        self.assertNotEqual(code, 0)
        self.assertIn("run bin/flightsim-on-linux dxvk build", output)

    def test_gpu_mode_passes_when_deps_hold_the_cached_dlls(self):
        self.mode("GPU")
        self.build_cache()
        for name in ("d3d11.dll", "dxgi.dll"):
            (self.deps / name).write_bytes((self.dxvk / name).read_bytes())
        code, output = self.run_part("dxvk_check")
        self.assertEqual(code, 0, output)
        self.assertIn("ok d3d11.dll in deps is the patched DXVK", output)
        self.assertIn("ok dxgi.dll in deps is the patched DXVK", output)

    def test_gpu_mode_fails_on_the_dll_that_differs_and_on_a_missing_one(self):
        self.mode("GPU")
        self.build_cache()
        (self.deps / "d3d11.dll").write_bytes(b"stock d3d11")
        code, output = self.run_part("dxvk_check")
        self.assertNotEqual(code, 0)
        self.assertEqual(len(self.lines(output, "FAIL d3d11.dll")), 1, output)
        self.assertEqual(len(self.lines(output, "FAIL dxgi.dll")), 1, output)


class DxvkApplyTest(PartsCase):
    def test_two_gpus_in_cpu_mode_write_only_the_filter(self):
        self.gpus("two")
        self.mode("CPU")
        code, output = self.run_part("dxvk_apply")
        self.assertEqual(code, 0, output)
        self.assertEqual((self.deps / "dxvk.conf").read_text(), FILTER + "\n")
        self.assertFalse((self.deps / "d3d11.dll").exists())
        self.assertFalse((self.deps / "dxgi.dll").exists())
        self.assertEqual(self.run_part("dxvk_check")[0], 0)

    def test_applying_twice_changes_nothing_the_second_time(self):
        self.gpus("two")
        self.mode("GPU")
        self.build_cache()
        self.assertEqual(self.run_part("dxvk_apply")[0], 0)
        code, output = self.run_part("dxvk_apply")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.lines(output, "changed"), [], output)

    def test_a_conf_with_our_line_and_another_option_passes_and_apply_leaves_it_byte_identical(self):
        self.gpus("two")
        self.mode("CPU")
        original = f"dxgi.maxFrameRate = 60\r\n{FILTER}\r\nd3d11.relaxedBarriers = True\r\n".encode()
        (self.deps / "dxvk.conf").write_bytes(original)
        code, output = self.run_part("dxvk_check")
        self.assertEqual(code, 0, output)
        code, output = self.run_part("dxvk_apply")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.lines(output, "changed"), [], output)
        self.assertEqual((self.deps / "dxvk.conf").read_bytes(), original)
        self.assertFalse((self.backup / "dxvk.conf").exists())

    def test_a_conf_with_another_option_and_no_filter_keeps_the_option_and_is_backed_up_once(self):
        self.gpus("two")
        self.mode("CPU")
        (self.deps / "dxvk.conf").write_text("dxgi.maxFrameRate = 60\n")
        code, output = self.run_part("dxvk_apply")
        self.assertEqual(code, 0, output)
        self.assertEqual((self.deps / "dxvk.conf").read_text(), f"dxgi.maxFrameRate = 60\n{FILTER}\n")
        self.assertEqual((self.backup / "dxvk.conf").read_text(), "dxgi.maxFrameRate = 60\n")
        self.assertEqual(self.run_part("dxvk_check")[0], 0)

    def test_a_second_filter_line_fails_the_check_and_apply_leaves_only_ours(self):
        self.gpus("two")
        self.mode("CPU")
        (self.deps / "dxvk.conf").write_text(f'{FILTER}\ndxgi.maxFrameRate = 60\ndxvk.deviceFilter="Radeon"\n')
        code, output = self.run_part("dxvk_check")
        self.assertNotEqual(code, 0)
        self.assertEqual(len(self.lines(output, "FAIL dxvk.conf in deps does not contain")), 1, output)
        code, output = self.run_part("dxvk_apply")
        self.assertEqual(code, 0, output)
        self.assertEqual((self.deps / "dxvk.conf").read_text(), f"dxgi.maxFrameRate = 60\n{FILTER}\n")
        self.assertEqual(self.run_part("dxvk_check")[0], 0)

    def test_cpu_mode_gives_back_the_dlls_an_earlier_gpu_mode_replaced(self):
        self.mode("GPU")
        self.build_cache()
        (self.deps / "d3d11.dll").write_bytes(b"stock d3d11")
        self.assertEqual(self.run_part("dxvk_apply")[0], 0)
        self.assertEqual((self.deps / "d3d11.dll").read_bytes(), b"patched d3d11")
        self.mode("CPU")
        code, output = self.run_part("dxvk_apply")
        self.assertEqual(code, 0, output)
        self.assertEqual((self.deps / "d3d11.dll").read_bytes(), b"stock d3d11")
        self.assertFalse((self.deps / "dxgi.dll").exists())
        self.assertEqual(self.run_part("dxvk_check")[0], 0)

    def test_one_gpu_writes_no_dxvk_conf(self):
        self.mode("CPU")
        code, output = self.run_part("dxvk_apply")
        self.assertEqual(code, 0, output)
        self.assertFalse((self.deps / "dxvk.conf").exists())

    def test_one_gpu_after_two_removes_the_conf_the_add_on_wrote(self):
        self.gpus("two")
        self.mode("CPU")
        self.run_part("dxvk_apply")
        self.gpus("one")
        code, output = self.run_part("dxvk_apply")
        self.assertEqual(code, 0, output)
        self.assertFalse((self.deps / "dxvk.conf").exists())

    def test_ambiguous_gpus_fail_and_write_nothing(self):
        self.gpus("ambiguous")
        self.mode("CPU")
        code, output = self.run_part("dxvk_apply")
        self.assertNotEqual(code, 0)
        self.assertIn("cannot pick one GPU", output)
        self.assertFalse((self.deps / "dxvk.conf").exists())

    def test_gpu_mode_without_the_cache_fails_before_writing_anything(self):
        self.gpus("two")
        self.mode("GPU")
        code, output = self.run_part("dxvk_apply")
        self.assertNotEqual(code, 0)
        self.assertIn("run bin/flightsim-on-linux dxvk build", output)
        self.assertFalse((self.deps / "dxvk.conf").exists())

    def test_gpu_mode_copies_both_dlls_from_the_cache(self):
        self.mode("GPU")
        self.build_cache()
        code, output = self.run_part("dxvk_apply")
        self.assertEqual(code, 0, output)
        self.assertEqual((self.deps / "d3d11.dll").read_bytes(), b"patched d3d11")
        self.assertEqual((self.deps / "dxgi.dll").read_bytes(), b"patched dxgi")
        self.assertEqual(self.run_part("dxvk_check")[0], 0)

    def test_a_missing_fenix_config_fails(self):
        code, output = self.run_part("dxvk_apply")
        self.assertNotEqual(code, 0)
        self.assertIn("start the Fenix once and sign in", output)

    def test_a_missing_deps_folder_fails(self):
        self.deps.rmdir()
        self.mode("CPU")
        code, output = self.run_part("dxvk_apply")
        self.assertNotEqual(code, 0)
        self.assertIn("install the Fenix first", output)


class DxvkUndoTest(PartsCase):
    def test_undo_restores_the_files_apply_replaced(self):
        self.gpus("two")
        self.mode("GPU")
        self.build_cache()
        (self.deps / "dxvk.conf").write_text("dxvk.deviceFilter = \"RTX 4080\"\n")
        (self.deps / "d3d11.dll").write_bytes(b"stock d3d11")
        (self.deps / "dxgi.dll").write_bytes(b"stock dxgi")
        self.assertEqual(self.run_part("dxvk_apply")[0], 0)
        self.assertEqual(self.run_part("dxvk_apply")[0], 0)
        code, output = self.run_part("dxvk_undo")
        self.assertEqual(code, 0, output)
        self.assertEqual((self.deps / "dxvk.conf").read_text(), "dxvk.deviceFilter = \"RTX 4080\"\n")
        self.assertEqual((self.deps / "d3d11.dll").read_bytes(), b"stock d3d11")
        self.assertEqual((self.deps / "dxgi.dll").read_bytes(), b"stock dxgi")

    def test_a_changed_gpu_name_does_not_replace_the_first_backup(self):
        self.gpus("two", "First Card")
        self.mode("CPU")
        (self.deps / "dxvk.conf").write_text("original\n")
        self.run_part("dxvk_apply")
        self.gpus("two", "Second Card")
        self.run_part("dxvk_apply")
        self.run_part("dxvk_undo")
        self.assertEqual((self.deps / "dxvk.conf").read_text(), "original\n")

    def test_undo_removes_what_apply_created_where_nothing_was(self):
        self.gpus("two")
        self.mode("GPU")
        self.build_cache()
        self.run_part("dxvk_apply")
        code, output = self.run_part("dxvk_undo")
        self.assertEqual(code, 0, output)
        for name in ("dxvk.conf", "d3d11.dll", "dxgi.dll"):
            self.assertFalse((self.deps / name).exists(), name)

    def test_undo_leaves_files_it_never_touched(self):
        (self.deps / "dxvk.conf").write_text("mine\n")
        (self.deps / "d3d11.dll").write_bytes(b"mine")
        code, output = self.run_part("dxvk_undo")
        self.assertEqual(code, 0, output)
        self.assertEqual((self.deps / "dxvk.conf").read_text(), "mine\n")
        self.assertEqual((self.deps / "d3d11.dll").read_bytes(), b"mine")


class GeometryTest(PartsCase):
    DLL = b"geometry provider"

    def setUp(self):
        super().setUp()
        self.target = self.prefix / "drive_c" / "windows" / "system32" / "d2d1_geometry.dll"
        self.built = self.cache / "fenix-geometry" / "d2d1_geometry.dll"
        self.hash = sha256(self.DLL)
        self.settings("FenixDisplay.exe")

    def settings(self, providers):
        (self.proton / "user_settings.py").write_text(SETTINGS % providers)

    def run_geometry(self, command, **extra):
        return self.run_part(command, FENIX_GEOMETRY_SHA256=self.hash, **extra)

    def install(self, data):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_bytes(data)

    def build(self):
        self.built.parent.mkdir(parents=True)
        self.built.write_bytes(self.DLL)

    def test_check_passes_with_the_dll_and_the_provider_setting(self):
        self.install(self.DLL)
        code, output = self.run_geometry("geometry_check")
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.lines(output, "ok ")), 2, output)

    def test_check_fails_on_a_corrupted_dll(self):
        self.install(self.DLL[:-1] + b"X")
        code, output = self.run_geometry("geometry_check")
        self.assertNotEqual(code, 0)
        self.assertIn("FAIL d2d1_geometry.dll in system32 is not the expected file", output)

    def test_check_fails_on_a_missing_dll(self):
        code, output = self.run_geometry("geometry_check")
        self.assertNotEqual(code, 0)
        self.assertIn("FAIL d2d1_geometry.dll is missing from system32", output)

    def test_check_fails_when_the_proton_does_not_name_fenix_display(self):
        self.install(self.DLL)
        self.settings("SomethingElse.exe")
        code, output = self.run_geometry("geometry_check")
        self.assertNotEqual(code, 0)
        self.assertIn("FAIL", output)
        self.assertIn("WINE_D2D1_GEOMETRY_PROVIDER", output)

    def test_check_accepts_fenix_display_among_several_programs(self):
        self.install(self.DLL)
        self.settings("Other.exe;FenixDisplay.exe")
        self.assertEqual(self.run_geometry("geometry_check")[0], 0)

    def test_check_fails_without_user_settings(self):
        self.install(self.DLL)
        (self.proton / "user_settings.py").unlink()
        self.assertNotEqual(self.run_geometry("geometry_check")[0], 0)

    def test_a_cached_dll_with_the_wrong_hash_is_built_again(self):
        self.built.parent.mkdir(parents=True)
        self.built.write_bytes(b"stale")
        script = GEOMETRY_STUBS + "geometry_build"
        code, output = self.run_geometry(script, LOG=str(self.root / "calls.log"), PAYLOAD=self.DLL.decode())
        self.assertEqual(code, 0, output)
        self.assertEqual(self.built.read_bytes(), self.DLL)
        calls = (self.root / "calls.log").read_text().split()
        self.assertEqual(calls.count("gcc"), 1)
        self.assertEqual(calls.count("download"), 3)
        self.assertEqual(sorted(path.name for path in self.built.parent.iterdir()), ["d2d1_geometry.dll"])

    def test_a_cached_dll_with_the_right_hash_is_not_built_again(self):
        self.build()
        code, output = self.run_geometry(GEOMETRY_STUBS + "geometry_build", LOG=str(self.root / "calls.log"), PAYLOAD=self.DLL.decode())
        self.assertEqual(code, 0, output)
        self.assertFalse((self.root / "calls.log").exists())

    def test_apply_installs_the_built_dll_and_is_idempotent(self):
        self.build()
        code, output = self.run_geometry("geometry_apply")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.target.read_bytes(), self.DLL)
        code, output = self.run_geometry("geometry_apply")
        self.assertEqual(self.lines(output, "changed"), [], output)

    def test_undo_restores_a_different_dll_that_was_there(self):
        self.build()
        self.install(b"someone else's")
        self.run_geometry("geometry_apply")
        self.run_geometry("geometry_apply")
        self.assertEqual(self.target.read_bytes(), self.DLL)
        code, output = self.run_geometry("geometry_undo")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.target.read_bytes(), b"someone else's")

    def test_undo_removes_the_dll_when_none_was_there(self):
        self.build()
        self.run_geometry("geometry_apply")
        self.run_geometry("geometry_undo")
        self.assertFalse(self.target.exists())

    def test_undo_leaves_a_dll_it_did_not_install(self):
        self.install(self.DLL)
        self.run_geometry("geometry_apply")
        self.run_geometry("geometry_undo")
        self.assertEqual(self.target.read_bytes(), self.DLL)


class RunInRuntimeTest(PartsCase):
    def setUp(self):
        super().setUp()
        self.log = self.root / "calls.log"
        wine = self.proton / "files/bin/wine"
        wine.parent.mkdir(parents=True)
        wine.write_text('#!/bin/sh\necho "wine $* prefix=$WINEPREFIX" >> "$LOG"\n')
        wine.chmod(0o755)
        self.runner = self.root / "runner"
        self.runner.write_text('#!/bin/sh\necho "runner $*" >> "$LOG"\nshift\nexec "$@"\n')
        self.runner.chmod(0o755)

    def run_wine(self, runtime, **extra):
        script = f'find_runtime() {{ echo "{runtime}"; echo "${{STEAM_DIR-unset}}" > "$STEAM_LOG"; }}\nfenix_wine "$FSOL_PREFIX" reg query HKCU'
        steam = {"STEAM_DIR": "", "FSOL_STEAM": "", **extra}
        return self.run_part(script, LOG=str(self.log), STEAM_LOG=str(self.root / "steam.log"), **steam)

    def test_a_runtime_runner_wraps_the_proton_wine(self):
        code, output = self.run_wine(self.runner)
        self.assertEqual(code, 0, output)
        calls = self.log.read_text().splitlines()
        self.assertEqual(len(calls), 2, calls)
        self.assertTrue(calls[0].startswith("runner -- env WINEPREFIX="), calls[0])
        self.assertEqual(calls[1], f"wine reg query HKCU prefix={self.prefix}")

    def test_without_a_runtime_proton_wine_runs_directly(self):
        code, output = self.run_wine("")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.log.read_text().splitlines(), [f"wine reg query HKCU prefix={self.prefix}"])

    def test_steam_dir_wins_over_fsol_steam_and_fsol_steam_is_the_fallback(self):
        self.run_wine("", STEAM_DIR="/from/steam-dir", FSOL_STEAM="/from/fsol-steam")
        self.assertEqual((self.root / "steam.log").read_text().strip(), "/from/steam-dir")
        self.run_wine("", FSOL_STEAM="/from/fsol-steam")
        self.assertEqual((self.root / "steam.log").read_text().strip(), "/from/fsol-steam")


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.prefix = self.root / "pfx"
        self.app = self.prefix / "drive_c/users/steamuser/AppData/Local/FenixApp/current"
        self.cli = self.root / "cli"
        self.cli.write_text(f'#!/bin/sh\necho \'{{"prefix": "{self.prefix}"}}\'\n')
        self.cli.chmod(0o755)
        self.stub("protontricks", 'echo "protontricks $*"; echo "cwd $PWD"')
        self.stub("pgrep", "exit 1")
        self.notes = self.root / "notify.log"
        self.stub("notify-send", f'printf "%s|" "$@" >> "{self.notes}"; echo >> "{self.notes}"')

    def stub(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def run_installer(self, path=None):
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{path if path is not None else os.environ['PATH']}",
            "FSOL_CLI": str(self.cli),
        }
        result = subprocess.run([shutil.which("bash"), str(INSTALLER)], capture_output=True, text=True, env=env)
        return result.returncode, result.stdout + result.stderr

    def install_app(self):
        self.app.mkdir(parents=True)
        (self.app / "FenixApp.exe").write_bytes(b"MZ")

    def test_a_refusal_without_a_terminal_also_shows_a_desktop_notification(self):
        self.install_app()
        self.stub("pgrep", '[ "$2" = "FlightSimulator2024[.]exe" ] && exit 0; exit 1')
        code, output = self.run_installer()
        self.assertNotEqual(code, 0)
        notes = self.notes.read_text().splitlines()
        self.assertEqual(len(notes), 1)
        self.assertIn("Fenix Installer|", notes[0])
        self.assertIn("close Microsoft Flight Simulator 2024 first", notes[0])

    def test_a_start_without_a_refusal_shows_no_notification(self):
        self.install_app()
        code, output = self.run_installer()
        self.assertEqual(code, 0, output)
        self.assertFalse(self.notes.exists())

    def test_it_refuses_while_the_simulator_runs(self):
        self.install_app()
        self.stub("pgrep", '[ "$2" = "FlightSimulator2024[.]exe" ] && exit 0; exit 1')
        code, output = self.run_installer()
        self.assertNotEqual(code, 0)
        self.assertIn("close Microsoft Flight Simulator 2024 first", output)
        self.assertNotIn("cwd", output)

    def test_it_refuses_when_the_installer_is_already_open(self):
        self.install_app()
        self.stub("pgrep", '[ "$2" = "FenixApp[.]exe" ] && exit 0; exit 1')
        code, output = self.run_installer()
        self.assertNotEqual(code, 0)
        self.assertIn("already open", output)

    def report(self, **fields):
        data = json.dumps({"prefix": str(self.prefix), **fields})
        self.cli.write_text(f"#!/bin/sh\necho '{data}'\n")

    def test_it_refuses_while_another_program_holds_the_prefix_and_names_it(self):
        self.install_app()
        self.report(prefix_in_use=True, prefix_programs=["tritrisim-hub.exe", "AutoFPS.exe"])
        code, output = self.run_installer()
        self.assertNotEqual(code, 0)
        self.assertIn("close tritrisim-hub.exe, AutoFPS.exe first", output)
        self.assertNotIn("cwd", output)

    def test_it_refuses_while_wine_still_runs_in_the_prefix_without_a_program_to_name(self):
        self.install_app()
        self.report(prefix_in_use=True, prefix_programs=[])
        code, output = self.run_installer()
        self.assertNotEqual(code, 0)
        self.assertIn("Wine is still running in the prefix", output)
        self.assertNotIn("cwd", output)

    def test_a_free_prefix_lets_it_start(self):
        self.install_app()
        self.report(prefix_in_use=False, prefix_programs=[])
        code, output = self.run_installer()
        self.assertEqual(code, 0, output)
        self.assertIn("protontricks --no-term", output)

    def test_it_says_where_to_get_the_installer_when_it_is_not_in_the_prefix(self):
        code, output = self.run_installer()
        self.assertNotEqual(code, 0)
        self.assertIn("FenixApp.exe is not in", output)
        self.assertIn("fenixsim.com", output)

    def test_it_says_when_the_machine_report_has_no_prefix(self):
        self.install_app()
        self.cli.write_text("#!/bin/sh\necho '{\"prefix\": null}'\n")
        code, output = self.run_installer()
        self.assertNotEqual(code, 0)
        self.assertIn("finds no simulator prefix", output)
        self.assertNotIn("cwd", output)

    def test_it_says_when_protontricks_is_missing(self):
        self.install_app()
        (self.bin / "protontricks").unlink()
        code, output = self.run_installer(path=self.limited("readlink", "dirname"))
        self.assertNotEqual(code, 0)
        self.assertIn("missing program: protontricks", output)
        self.assertNotIn("cwd", output)

    def limited(self, *tools):
        folder = self.root / "limited"
        folder.mkdir(exist_ok=True)
        for tool in tools:
            (folder / tool).symlink_to(shutil.which(tool))
        return str(folder)

    def test_it_opens_the_installer_through_protontricks_from_the_fenixapp_folder(self):
        self.install_app()
        code, output = self.run_installer()
        self.assertEqual(code, 0, output)
        self.assertIn(f"protontricks --no-term -c {SESSION} 2537590", output.splitlines())
        self.assertIn(f"cwd {self.app.parent}", output.splitlines())

    def test_it_opens_without_gamescope_installed(self):
        self.install_app()
        code, output = self.run_installer(path=self.limited("readlink", "dirname", "python3"))
        self.assertEqual(code, 0, output)
        self.assertIn(f"protontricks --no-term -c {SESSION} 2537590", output.splitlines())


class InstallerSessionTest(unittest.TestCase):
    UPDATER = r"C:\users\steamuser\AppData\Local\FenixApp\Update.exe apply --package C:\users\steamuser\AppData\Local\FenixApp\packages\FenixApp-1.0.355-1cf2ef7-full.nupkg"
    RESTARTED = r"C:\users\steamuser\AppData\Local\FenixApp\current\FenixApp.exe"
    STUB = r"C:\users\steamuser\AppData\Local\FenixApp\Fenix Installer.exe"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.log"
        self.stub("wine", 'echo "wine $* in $PWD" >> "$LOG"; exit "${WINE_EXIT:-0}"')
        self.stub("pgrep", 'echo "pgrep $*" >> "$LOG"; n=$(grep -c "^pgrep" "$LOG"); [ "$n" -le "${RUNNING_CHECKS:-0}" ]')
        self.stub("sleep", 'echo "sleep $*" >> "$LOG"')

    def stub(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def run_session(self, **extra):
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "LOG": str(self.log), **extra}
        result = subprocess.run([shutil.which("bash"), str(SESSION)], capture_output=True, text=True, env=env, cwd=self.root)
        return result.returncode, result.stdout + result.stderr

    def calls(self, prefix):
        return [line for line in self.log.read_text().splitlines() if line.startswith(prefix)]

    def test_it_starts_the_current_installer_from_the_folder_above_it(self):
        code, output = self.run_session()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.calls("wine"), [f"wine {self.root}/current/FenixApp.exe in {self.root}"])

    def test_it_waits_while_the_updater_or_the_restarted_installer_runs(self):
        code, output = self.run_session(RUNNING_CHECKS="4")
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.calls("pgrep")), 7)
        self.assertEqual(self.log.read_text().splitlines()[0].split()[0], "wine")

    def test_a_gap_shorter_than_three_checks_does_not_end_the_wait(self):
        self.stub("pgrep", 'echo "pgrep $*" >> "$LOG"; n=$(grep -c "^pgrep" "$LOG"); [ "$n" -eq 1 ] || [ "$n" -eq 4 ]')
        code, output = self.run_session()
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.calls("pgrep")), 7)

    def test_it_ends_with_the_exit_status_of_the_first_installer(self):
        code, output = self.run_session(WINE_EXIT="5")
        self.assertEqual(code, 5, output)
        self.assertEqual(len(self.calls("pgrep")), 3)

    def test_the_pattern_finds_the_updater_and_the_installer_and_not_the_session(self):
        self.run_session()
        pattern = self.calls("pgrep")[0].split(" ", 2)[2]
        for line in (self.UPDATER, self.RESTARTED, self.STUB):
            self.assertRegex(line, pattern)
        self.assertNotRegex(f"bash {SESSION}", pattern)
        self.assertNotRegex(r"S:\steamapps\common\Microsoft Flight Simulator 2024\FlightSimulator2024.exe", pattern)


if __name__ == "__main__":
    unittest.main()
