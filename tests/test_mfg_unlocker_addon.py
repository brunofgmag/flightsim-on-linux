import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fsol import addons
from tests.test_machine import write
from tests.test_maddog_addon import page_problems
from tests.test_reshade_addons import AMD_GPU, AMD_INTEGRATED, NVIDIA_GPU, SimulatorCase, read, sha256, stub, summary, tree

ROOT = Path(__file__).resolve().parent.parent
MFG = ROOT / "addons" / "mfg-unlocker"
PAGE = ROOT / "site" / "pages" / "mfg-unlocker.html"
RTX_5080 = ("PHYSICAL_DEVICE_TYPE_DISCRETE_GPU", "NVIDIA GeForce RTX 5080", "0x10de", "0x2c02")
RTX_4090_LAPTOP = ("PHYSICAL_DEVICE_TYPE_DISCRETE_GPU", "NVIDIA GeForce RTX 4090 Laptop GPU", "0x10de", "0x2757")
GTX_1080 = ("PHYSICAL_DEVICE_TYPE_DISCRETE_GPU", "NVIDIA GeForce GTX 1080", "0x10de", "0x1b80")
ZIP_URL = "https://github.com/dashdogy/MFG-Unlocker/releases/download/v1.7.0/MFGUnlocker-v1.7.0.zip"
ZIP_SHA256 = "5bd19d03288e1ece1d0bb2030ac072b36758045028da192044dcaad73fd901dd"
DLL_SHA256 = "e6b6e6b70a6ed3af839c9647a32fc22660ae6b2d655062c5c072246c91fc10c7"
PATCHED_SHA256 = "a610fc756ce93e04be6719a9077e85eca3c3121fdad8a4cf9a9a7cf3d3dce5cf"
FULL_OPTIONS = 'STEAM_COMPAT_LIBRARY_PATHS=/games WINEDLLOVERRIDES="version=n,b" PROTON_NVIDIA_NVCUDA=1 PROTON_ENABLE_WAYLAND=1 PROTON_DLSS_UPGRADE=1 DXVK_NVAPI_DRS_SETTINGS=ngx_dlss_sr_override=on,ngx_dlss_fg_override=on,ngx_dlss_fg_override_render_preset_selection=render_preset_b VKD3D_CONFIG=no_upload_hvv game %command%'
FIRST_RUN = '{"followGame":true,"mode":"follow","multiplier":2,"dynamicTargetFrameRate":0,"dlssgPreset":3,"vsyncMode":0,"reflexFrameLimitFps":0,"generatedOnlyDebug":false,"intervalLogging":true,"version":13,"dlss5":false}'
FIFO = "VKD3D_SWAPCHAIN_PRESENT_MODE=FIFO"
FIXED = FIRST_RUN.replace('"followGame":true,"mode":"follow"', '"followGame":false,"mode":"fixed"')
LOG = """[16:21:08.004] Backend module: S:\\steamapps\\common\\MSFS2024\\VERSION.dll, file 1.7.0.1145, product "MFG Unlocker" 1.7.0
[16:21:08.094] MFG_PROXY_UI state=active boundary=real-factory-return nativeTableWrites=0
[16:21:09.734] D157 D3D12 adapter verification: luid=0x00000000000003F2 capability=8.9 verified=1
[16:25:13.441] State heartbeat: route=local ready=1 routeFailure=0 control=fixed 3x gameFG=1 appliedFG=1 restart=0 | presented=3
"""
LOG_NO_CUDA = LOG.replace("capability=8.9 verified=1", "capability=0.0 verified=0")
LOG_NO_MENU = LOG.replace("MFG_PROXY_UI state=active", "MFG_PROXY_UI factory gateway first call")
SOURCE = bytes((index * 7 + 3) % 251 for index in range(1024))
REPLACEMENTS = (
    (0x10, "menu", b"\x90\x90"),
    (0x100, "protection", b"\x08"),
    (0x2F0, "shaders", b"abs(e)>=3e38"),
    (0x3F0, "shaders", b"\x90" * 6),
)


def load_module():
    spec = importlib.util.spec_from_file_location("mfg_parts", MFG / "parts" / "mfg.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mfg = load_module()


def synthetic_table(image=SOURCE):
    patches = {}
    patched = bytearray(image)
    for offset, name, replacement in REPLACEMENTS:
        original = image[offset:offset + len(replacement)]
        patched[offset:offset + len(original)] = replacement
        patches.setdefault(name, []).append({"offset": hex(offset), "original": original.hex(), "patched": replacement.hex()})
    table = {"release": "1.7.0", "input_sha256": sha256(image), "output_sha256": sha256(bytes(patched)), "patches": [{"name": name, "runs": runs} for name, runs in patches.items()]}
    return table, bytes(patched)


def localconfig(options):
    escaped = options.replace("\\", "\\\\").replace('"', '\\"')
    return '"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" {\n  "2537590" { "LaunchOptions" "' + escaped + '" }\n} } } } }\n'


def make_zip(path, members):
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    return path


class MfgShapeTest(unittest.TestCase):
    def test_the_manifest_says_what_the_add_on_needs(self):
        addon = addons.load(MFG)
        self.assertEqual((addon.id, addon.name, addon.tested_version, addon.tested_on, addon.touches_addon_files), ("mfg-unlocker", "MFG Unlocker", "1.7.0", "2026-11-05", True))
        self.assertEqual(addon.needs, ["simulator-closed"])
        self.assertEqual(addon.installed, {"sim": ["version.dll"]})
        self.assertEqual(addon.install, {"from": "https://github.com/dashdogy/MFG-Unlocker/releases", "paid": False, "kind": "native"})
        self.assertTrue(addon.can_install)
        self.assertFalse(addon.install_needs_file)

    def test_the_pinned_download_is_the_official_release_asset(self):
        text = (MFG / "install").read_text()
        self.assertIn('MFG_VERSION="1.7.0"', text)
        self.assertIn("https://github.com/dashdogy/MFG-Unlocker/releases/download/v$MFG_VERSION/MFGUnlocker-v$MFG_VERSION.zip", text)
        self.assertIn(f'MFG_SHA256="{ZIP_SHA256}"', text)

    def test_the_repository_ships_no_dll_and_no_zip(self):
        shipped = [path.name for path in MFG.rglob("*") if path.suffix.lower() in (".dll", ".zip")]
        self.assertEqual(shipped, [])


class PatchTableTest(unittest.TestCase):
    def setUp(self):
        self.table = mfg.load_table()
        self.runs = mfg.table_runs(self.table)

    def test_it_names_the_release_and_both_hashes(self):
        self.assertEqual((self.table["release"], self.table["input_sha256"], self.table["output_sha256"]), ("1.7.0", DLL_SHA256, PATCHED_SHA256))

    def test_it_holds_the_seven_patches_in_eighteen_runs(self):
        self.assertEqual(len(self.table["patches"]), 7)
        self.assertEqual(len(self.runs), 18)

    def test_the_runs_change_exactly_the_123_bytes_that_cmp_found(self):
        changed = sum(1 for _, original, patched in self.runs for before, after in zip(original, patched) if before != after)
        self.assertEqual(changed, 123)

    def test_each_run_keeps_its_length_and_none_overlap(self):
        spans = []
        for offset, original, patched in self.runs:
            self.assertEqual(len(original), len(patched), hex(offset))
            spans.append((offset, offset + len(original)))
        spans.sort()
        for (_, end), (start, _) in zip(spans, spans[1:]):
            self.assertLessEqual(end, start)

    def test_the_loader_callout_import_becomes_the_shutdown_check(self):
        by_offset = {offset: (original, patched) for offset, original, patched in self.runs}
        original, patched = by_offset[0x40495B]
        self.assertEqual(b"Rtl" + original, b"RtlIsThreadWithinLoaderCallout")
        self.assertEqual(b"Rtl" + patched.rstrip(b"\0"), b"RtlDllShutdownInProgress")


class PatchImageTest(unittest.TestCase):
    def setUp(self):
        self.table, self.patched = synthetic_table()

    def test_it_writes_every_run_and_nothing_else(self):
        result = mfg.patch_image(SOURCE, self.table)
        self.assertEqual(result, self.patched)
        changed = [index for index, (before, after) in enumerate(zip(SOURCE, result)) if before != after]
        inside = {index for offset, original, _ in mfg.table_runs(self.table) for index in range(offset, offset + len(original))}
        self.assertTrue(changed)
        self.assertLessEqual(set(changed), inside)
        self.assertEqual(len(result), len(SOURCE))

    def test_each_run_is_checked_against_the_original_before_anything_is_written(self):
        for index in range(len(REPLACEMENTS)):
            with self.subTest(run=index):
                table = json.loads(json.dumps(self.table))
                runs = [run for patch in table["patches"] for run in patch["runs"]]
                original = bytearray(bytes.fromhex(runs[index]["original"]))
                original[0] ^= 0xFF
                runs[index]["original"] = original.hex()
                with self.assertRaises(mfg.PatchError) as raised:
                    mfg.patch_image(SOURCE, table)
                self.assertIn(f"{int(runs[index]['offset'], 16):#x}", str(raised.exception))

    def test_a_file_with_another_hash_is_refused_even_when_every_run_matches(self):
        other = bytearray(SOURCE)
        other[0x200] ^= 0xFF
        with self.assertRaises(mfg.PatchError) as raised:
            mfg.patch_image(bytes(other), self.table)
        self.assertIn(sha256(bytes(other)), str(raised.exception))

    def test_a_table_whose_output_hash_is_wrong_is_refused(self):
        self.table["output_sha256"] = "0" * 64
        with self.assertRaises(mfg.PatchError) as raised:
            mfg.patch_image(SOURCE, self.table)
        self.assertIn("promises", str(raised.exception))

    def test_a_run_past_the_end_of_the_file_is_refused(self):
        table = json.loads(json.dumps(self.table))
        table["patches"][0]["runs"][0]["offset"] = hex(len(SOURCE) - 1)
        with self.assertRaises(mfg.PatchError):
            mfg.patch_image(SOURCE, table)

    def test_the_table_of_this_repository_refuses_a_file_that_is_not_the_release(self):
        with self.assertRaises(mfg.PatchError):
            mfg.patch_image(SOURCE, mfg.load_table())


class MfgCase(SimulatorCase):
    ADDON = "mfg-unlocker"

    def setUp(self):
        super().setUp()
        self.table, self.patched = synthetic_table()
        self.steam = self.fake.steam
        self.backup_folder = self.backup("mfg-unlocker")
        self.config = self.sim / "RTXMFG-Universal.json"
        self.log = self.sim / "RTXMFG-flightsimulator2024-A2C6379C1634BF0A.log"
        self.gpus(NVIDIA_GPU)
        patcher = mock.patch.dict(os.environ, {"PATH": f"{self.root / 'bin'}:{os.environ['PATH']}"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.options(FULL_OPTIONS)

    def gpus(self, *gpus):
        stub(self.root / "bin" / "vulkaninfo", f"#!/bin/sh\ncat <<'EOF'\n{summary(*gpus)}EOF\n")

    def options(self, text, user="1234"):
        write(self.steam / "userdata" / user / "config" / "localconfig.vdf", localconfig(text))

    def environment(self, **extra):
        env = super().environment(**extra)
        env.setdefault("FSOL_STEAM", str(self.steam))
        return env

    def installed(self, data=None):
        (self.sim / "version.dll").write_bytes(self.patched if data is None else data)

    def configured(self, text=FIXED):
        self.config.write_bytes(text.encode())

    def fixed_machine(self):
        self.installed()
        self.configured()
        self.log.write_text(LOG)

    def call(self, function, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(mfg, "load_table", return_value=self.table), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = function(*args)
        return code, out.getvalue(), err.getvalue()

    def check(self):
        code, out, _ = self.call(mfg.check, str(self.sim), str(self.steam))
        return code, out.splitlines()

    def apply(self):
        return self.call(mfg.apply, str(self.sim), str(self.steam), str(self.backup_folder))

    def undo(self):
        return self.call(mfg.undo, str(self.sim), str(self.backup_folder))

    def install(self, archive, sha=None):
        return self.call(mfg.install, str(archive), sha or sha256(Path(archive).read_bytes()), str(self.sim))


class MfgInstallTest(MfgCase):
    def archive(self, members=None):
        return make_zip(self.root / "release.zip", members if members is not None else {"MFGUnlocker.dll": SOURCE})

    def test_it_installs_the_patched_dll_as_version_dll_and_leaves_no_part_file(self):
        code, out, err = self.install(self.archive())
        self.assertEqual(code, 0, err)
        self.assertEqual((self.sim / "version.dll").read_bytes(), self.patched)
        self.assertEqual(sorted(path.name for path in self.sim.glob("version.dll*")), ["version.dll"])
        self.assertIn("addon apply mfg-unlocker", out)

    def test_a_zip_with_another_hash_than_the_one_given_is_refused_and_nothing_is_written(self):
        code, _, err = self.install(self.archive(), sha="0" * 64)
        self.assertEqual(code, 1)
        self.assertIn("not the release this add-on was tested with", err)
        self.assertFalse((self.sim / "version.dll").exists())

    def test_a_dll_that_is_not_the_release_is_refused_and_nothing_is_written(self):
        code, _, err = self.install(self.archive({"MFGUnlocker.dll": SOURCE[:-1] + b"\xff"}))
        self.assertEqual(code, 1)
        self.assertIn("cannot be patched", err)
        self.assertFalse((self.sim / "version.dll").exists())

    def test_a_zip_without_the_dll_is_refused(self):
        code, _, err = self.install(self.archive({"readme.txt": b"hello"}))
        self.assertEqual(code, 1)
        self.assertIn("cannot be patched", err)
        self.assertFalse((self.sim / "version.dll").exists())

    def test_a_file_that_is_not_a_zip_is_refused(self):
        junk = self.root / "junk.zip"
        junk.write_bytes(b"not a zip")
        code, _, err = self.install(junk)
        self.assertEqual(code, 1)
        self.assertFalse((self.sim / "version.dll").exists())

    def test_a_missing_file_is_refused_and_named(self):
        code, _, err = self.install(self.root / "missing.zip", sha="0" * 64)
        self.assertEqual(code, 1)
        self.assertIn("missing.zip", err)

    def test_a_version_dll_that_is_not_ours_is_never_overwritten_and_is_named(self):
        self.installed(b"MZ another mod")
        code, _, err = self.install(self.archive())
        self.assertEqual(code, 1)
        self.assertEqual((self.sim / "version.dll").read_bytes(), b"MZ another mod")
        self.assertIn(str(self.sim / "version.dll"), err)
        self.assertIn(sha256(b"MZ another mod"), err)

    def test_the_patched_dll_already_there_is_left_alone(self):
        self.installed()
        before = (self.sim / "version.dll").stat().st_mtime_ns
        code, out, _ = self.install(self.archive())
        self.assertEqual(code, 0)
        self.assertIn("already in", out)
        self.assertEqual((self.sim / "version.dll").stat().st_mtime_ns, before)

    def test_without_reshade_it_notes_that_only_reshade_was_tested(self):
        (self.sim / "dxgi.dll").unlink(missing_ok=True)
        _, out, _ = self.install(self.archive())
        self.assertIn("only tested with ReShade present", out)

    def test_with_reshade_it_has_no_such_note(self):
        write(self.sim / "dxgi.dll", "MZ ReShade")
        _, out, _ = self.install(self.archive())
        self.assertNotIn("ReShade", out)


class MfgInstallScriptTest(MfgCase):
    def setUp(self):
        super().setUp()
        self.curl_log = self.root / "curl.log"
        stub(self.root / "bin" / "curl", f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.curl_log}"\nout=""\nwhile [ $# -gt 0 ]; do [ "$1" = --output ] && out="$2"; shift; done\nprintf junk > "$out"\n')

    def test_it_refuses_while_the_simulator_runs(self):
        result = self.script("install", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("close the simulator", result.stderr)
        self.assertFalse(self.curl_log.exists())

    def test_it_downloads_the_pinned_url_and_installs_nothing_when_the_hash_differs(self):
        result = self.script("install")
        self.assertEqual(result.returncode, 1)
        self.assertIn(ZIP_URL, self.curl_log.read_text())
        self.assertIn("--fail", self.curl_log.read_text())
        self.assertIn("not the file this add-on was tested with", result.stderr)
        self.assertFalse((self.sim / "version.dll").exists())
        self.assertEqual(list((self.root / "cache" / "downloads").iterdir()), [])

    def test_a_file_given_by_hand_with_another_hash_is_refused_without_downloading(self):
        file = make_zip(self.root / "newer.zip", {"MFGUnlocker.dll": b"MZ a newer release"})
        result = self.script("install", FSOL_INSTALLER=str(file))
        self.assertEqual(result.returncode, 1)
        self.assertIn("not the release this add-on was tested with", result.stderr)
        self.assertFalse(self.curl_log.exists())
        self.assertFalse((self.sim / "version.dll").exists())

    @unittest.skipUnless(os.environ.get("MFG_UNLOCKER_ZIP"), "set MFG_UNLOCKER_ZIP to the official MFGUnlocker-v1.7.0.zip to run the real patcher")
    def test_the_real_release_patches_to_the_pinned_hash(self):
        result = self.script("install", FSOL_INSTALLER=os.environ["MFG_UNLOCKER_ZIP"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sha256((self.sim / "version.dll").read_bytes()), PATCHED_SHA256)


class MfgDownloadTest(MfgCase):
    URL = "https://example.test/MFGUnlocker.zip"

    def setUp(self):
        super().setUp()
        self.asset = b"PK the zip of a release"
        self.sha = sha256(self.asset)
        self.curl_log = self.root / "curl.log"
        stub(self.root / "bin" / "curl", f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.curl_log}"\nout=""\nwhile [ $# -gt 0 ]; do [ "$1" = --output ] && out="$2"; shift; done\nprintf %s "$CURL_BODY" > "$out"\n')
        self.target = self.root / "cache" / "downloads" / "file.zip"

    def bash(self, text):
        env = self.environment(CURL_BODY=self.asset.decode())
        return subprocess.run(["bash", "-c", f'. "{MFG}/parts/download.sh"; {text}'], env=env, capture_output=True, text=True)

    def fetch(self, sha=None):
        return self.bash(f'mfg_fetch "{self.URL}" {sha or self.sha} "{self.target}"')

    def test_a_download_with_the_right_hash_is_kept_in_the_cache_folder(self):
        result = self.fetch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.read_bytes(), self.asset)
        self.assertFalse(Path(f"{self.target}.part").exists())
        self.assertIn(self.URL, self.curl_log.read_text())

    def test_a_file_already_downloaded_is_not_downloaded_again(self):
        self.fetch()
        self.curl_log.unlink()
        result = self.fetch()
        self.assertEqual(result.returncode, 0)
        self.assertIn("already downloaded", result.stdout)
        self.assertFalse(self.curl_log.exists())

    def test_a_download_with_another_hash_is_thrown_away_and_nothing_is_kept(self):
        result = self.fetch("0" * 64)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.target.exists())
        self.assertFalse(Path(f"{self.target}.part").exists())
        self.assertIn("not the file this add-on was tested with", result.stderr)

    def test_a_cached_file_with_another_hash_is_downloaded_again(self):
        write(self.target, "an old or damaged file")
        result = self.fetch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.read_bytes(), self.asset)


class MfgCheckTest(MfgCase):
    def setUp(self):
        super().setUp()
        self.fixed_machine()

    def failing(self, lines):
        return [line for line in lines if line.startswith("FAIL")]

    def only_failure(self, code, lines):
        self.assertEqual(code, 1, lines)
        self.assertEqual(len(self.failing(lines)), 1, lines)

    def test_a_fixed_machine_passes_with_ten_ok_lines(self):
        code, lines = self.check()
        self.assertEqual(code, 0, lines)
        self.assertEqual([line.split()[0] for line in lines], ["ok"] * 10)

    def test_without_a_log_the_log_lines_are_left_out(self):
        self.log.unlink()
        code, lines = self.check()
        self.assertEqual(code, 0, lines)
        self.assertEqual(len(lines), 8)

    def test_the_gpu_line_names_the_card(self):
        _, lines = self.check()
        self.assertIn("NVIDIA GeForce RTX 4080", lines[0])

    def test_an_rtx_40_laptop_card_passes(self):
        self.gpus(RTX_4090_LAPTOP)
        code, _ = self.check()
        self.assertEqual(code, 0)

    def test_an_rtx_50_card_fails_alone_and_says_why(self):
        self.gpus(RTX_5080)
        code, lines = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.failing(lines)), 1)
        self.assertIn("RTX 50 cards have multi frame generation of their own", lines[0])

    def test_an_older_nvidia_card_fails_alone(self):
        self.gpus(GTX_1080)
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertTrue(lines[0].startswith("FAIL"))

    def test_an_amd_card_fails_alone(self):
        self.gpus(AMD_GPU)
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn("not NVIDIA", lines[0])

    def test_two_gpus_choose_the_discrete_nvidia_one(self):
        self.gpus(AMD_INTEGRATED, NVIDIA_GPU)
        code, _ = self.check()
        self.assertEqual(code, 0)

    def test_two_discrete_gpus_leave_the_choice_to_the_reader_and_say_so(self):
        self.gpus(NVIDIA_GPU, AMD_GPU)
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn("cannot pick one GPU", lines[0])

    def test_a_missing_vulkaninfo_fails_the_gpu_line_and_names_the_package(self):
        with mock.patch.object(mfg.shutil, "which", return_value=None):
            code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn("vulkan-tools", lines[0])

    def test_without_version_dll_it_fails_alone_and_says_to_install(self):
        (self.sim / "version.dll").unlink()
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn("addon install mfg-unlocker", lines[1])

    def test_a_version_dll_that_is_not_ours_fails_alone_and_is_named(self):
        self.installed(b"MZ another mod")
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn(sha256(b"MZ another mod"), lines[1])
        self.assertIn("does not overwrite it", lines[1])

    def option_failure(self, options, expected):
        self.options(options)
        code, lines = self.check()
        self.assertEqual(code, 1)
        failing = self.failing(lines)
        self.assertEqual(len(failing), 1, lines)
        self.assertIn(expected, failing[0])

    def test_without_the_dll_override_it_fails_alone(self):
        self.option_failure(FULL_OPTIONS.replace('WINEDLLOVERRIDES="version=n,b" ', ""), 'WINEDLLOVERRIDES="version=n,b"')

    def test_an_override_for_another_dll_does_not_count(self):
        self.option_failure(FULL_OPTIONS.replace('"version=n,b"', "winmm=n,b"), 'WINEDLLOVERRIDES="version=n,b"')

    def test_an_override_that_puts_the_builtin_first_does_not_count(self):
        self.option_failure(FULL_OPTIONS.replace('"version=n,b"', "version=b,n"), 'WINEDLLOVERRIDES="version=n,b"')

    def test_version_among_other_overrides_counts(self):
        self.options(FULL_OPTIONS.replace('"version=n,b"', '"xinput9_1_0,winmm,version=n,b;dxgi=n"'))
        self.assertEqual(self.check()[0], 0)

    def test_without_nvcuda_it_fails_alone(self):
        self.option_failure(FULL_OPTIONS.replace("PROTON_NVIDIA_NVCUDA=1 ", ""), "PROTON_NVIDIA_NVCUDA=1")

    def test_nvcuda_set_to_zero_fails(self):
        self.option_failure(FULL_OPTIONS.replace("PROTON_NVIDIA_NVCUDA=1", "PROTON_NVIDIA_NVCUDA=0"), "PROTON_NVIDIA_NVCUDA=1")

    def test_without_the_dlss_upgrade_it_fails_alone(self):
        self.option_failure(FULL_OPTIONS.replace("PROTON_DLSS_UPGRADE=1 ", ""), "PROTON_DLSS_UPGRADE=1")

    def test_without_the_frame_generation_override_it_fails_alone(self):
        self.option_failure(FULL_OPTIONS.replace("ngx_dlss_fg_override=on,", ""), "ngx_dlss_fg_override=on in DXVK_NVAPI_DRS_SETTINGS")

    def test_the_override_set_off_fails(self):
        self.option_failure(FULL_OPTIONS.replace("ngx_dlss_fg_override=on", "ngx_dlss_fg_override=off"), "ngx_dlss_fg_override=on in DXVK_NVAPI_DRS_SETTINGS")

    def test_a_word_after_the_command_is_not_a_launch_option(self):
        self.option_failure(FULL_OPTIONS.replace('WINEDLLOVERRIDES="version=n,b" ', "") + " WINEDLLOVERRIDES=version=n,b", 'WINEDLLOVERRIDES="version=n,b"')

    def test_no_launch_options_saved_fails_the_four_launch_lines_and_nothing_else(self):
        self.options("")
        code, lines = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.failing(lines)), 4)
        self.assertTrue(all("launch options" in line for line in self.failing(lines)))

    def test_options_held_by_any_steam_user_count(self):
        self.options("", user="1234")
        self.options(FULL_OPTIONS, user="5678")
        self.assertEqual(self.check()[0], 0)

    def test_the_fifo_present_mode_is_not_needed_and_not_named_with_or_without_it(self):
        code, without = self.check()
        self.assertEqual(code, 0, without)
        self.options(FULL_OPTIONS.replace(" %command%", f" {FIFO} %command%"))
        code, with_it = self.check()
        self.assertEqual(code, 0, with_it)
        self.assertNotIn("VKD3D_SWAPCHAIN_PRESENT_MODE", "\n".join(without + with_it))

    def test_no_steam_folder_gives_one_failure_with_the_next_step(self):
        code, out, _ = self.call(mfg.check, str(self.sim), "")
        lines = out.splitlines()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.failing(lines)), 1)
        self.assertIn("flightsim-on-linux machine", self.failing(lines)[0])

    def test_without_the_config_file_it_is_one_failure_with_the_next_step(self):
        self.config.unlink()
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn("start the simulator once", self.failing(lines)[0])
        self.assertIn("addon apply mfg-unlocker", self.failing(lines)[0])

    def test_a_config_that_is_not_json_is_one_failure(self):
        self.configured("{not json")
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn("not valid JSON", self.failing(lines)[0])

    def test_follow_game_left_on_fails_alone(self):
        self.configured(FIXED.replace('"followGame":false', '"followGame":true'))
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn('"followGame" is true', self.failing(lines)[0])

    def test_a_mode_other_than_fixed_fails_alone(self):
        for mode in ("follow", "dynamic"):
            with self.subTest(mode=mode):
                self.configured(FIXED.replace('"mode":"fixed"', f'"mode":"{mode}"'))
                code, lines = self.check()
                self.only_failure(code, lines)
                self.assertIn(f'"mode" is "{mode}"', self.failing(lines)[0])

    def test_the_mode_line_gives_the_multiplier(self):
        self.configured(FIXED.replace('"multiplier":2', '"multiplier":4'))
        _, lines = self.check()
        self.assertTrue(any('"mode" is "fixed"' in line and "at 4x" in line for line in lines))

    def test_a_log_without_the_capability_line_fails_alone_and_names_the_variable(self):
        self.log.write_text(LOG_NO_CUDA)
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn("capability=8.9 verified=1", self.failing(lines)[0])
        self.assertIn("PROTON_NVIDIA_NVCUDA=1", self.failing(lines)[0])

    def test_a_log_without_the_menu_line_fails_alone(self):
        self.log.write_text(LOG_NO_MENU)
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn("MFG_PROXY_UI state=active", self.failing(lines)[0])

    def test_the_previous_logs_do_not_stand_in_for_the_current_one(self):
        self.log.write_text(LOG_NO_CUDA)
        (self.sim / "RTXMFG-flightsimulator2024-A2C6379C1634BF0A.previous.log").write_text(LOG)
        (self.sim / "RTXMFG-flightsimulator2024-A2C6379C1634BF0A.previous2.log").write_text(LOG)
        code, lines = self.check()
        self.only_failure(code, lines)

    def test_a_log_of_previous_runs_alone_is_no_log(self):
        self.log.unlink()
        (self.sim / "RTXMFG-flightsimulator2024-A2C6379C1634BF0A.previous.log").write_text(LOG_NO_CUDA)
        code, lines = self.check()
        self.assertEqual((code, len(lines)), (0, 8))

    def test_check_changes_nothing(self):
        before = tree(self.root)
        self.check()
        self.assertEqual(tree(self.root), before)


class MfgApplyTest(MfgCase):
    def setUp(self):
        super().setUp()
        self.installed()

    def test_it_sets_the_two_keys_and_keeps_every_other_byte(self):
        self.configured(FIRST_RUN)
        code, out, err = self.apply()
        self.assertEqual(code, 0, err)
        self.assertEqual(read(self.config), FIXED)
        self.assertIn("Backspace", out)

    def test_the_multiplier_the_reader_chose_stays(self):
        self.configured(FIRST_RUN.replace('"multiplier":2', '"multiplier":5'))
        self.apply()
        self.assertEqual(json.loads(read(self.config))["multiplier"], 5)

    def test_a_pretty_printed_file_with_crlf_keeps_its_layout(self):
        text = json.dumps(json.loads(FIRST_RUN), indent=2).replace("\n", "\r\n") + "\r\n"
        self.configured(text)
        self.apply()
        expected = text.replace('"followGame": true', '"followGame": false').replace('"mode": "follow"', '"mode": "fixed"')
        self.assertEqual(self.config.read_bytes(), expected.encode())

    def test_a_missing_key_is_added_and_the_others_stay(self):
        self.configured('{"multiplier":3,"version":13}')
        code, _, err = self.apply()
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(read(self.config)), {"multiplier": 3, "version": 13, "followGame": False, "mode": "fixed"})

    def test_a_file_already_right_is_not_written_again(self):
        self.configured(FIXED)
        before = self.config.stat().st_mtime_ns
        code, _, _ = self.apply()
        self.assertEqual(code, 0)
        self.assertEqual(read(self.config), FIXED)
        self.assertEqual(self.config.stat().st_mtime_ns, before)

    def test_apply_twice_gives_the_same_file(self):
        self.configured(FIRST_RUN)
        self.apply()
        first = self.config.read_bytes()
        self.apply()
        self.assertEqual(self.config.read_bytes(), first)

    def test_it_keeps_the_original_before_the_first_change_and_not_the_second_time(self):
        self.configured(FIRST_RUN)
        self.apply()
        self.assertEqual(read(self.backup_folder / "RTXMFG-Universal.json.orig"), FIRST_RUN)
        self.configured(FIXED.replace('"multiplier":2', '"multiplier":6'))
        self.apply()
        self.assertEqual(read(self.backup_folder / "RTXMFG-Universal.json.orig"), FIRST_RUN)

    def test_without_the_config_file_it_says_to_start_the_simulator_once_and_writes_nothing(self):
        code, _, err = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("start the simulator once", err)
        self.assertIn("run flightsim-on-linux addon apply mfg-unlocker again", err)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.backup_folder.exists())

    def test_a_config_that_is_not_json_is_left_alone(self):
        self.configured("{not json")
        code, _, err = self.apply()
        self.assertEqual(code, 1)
        self.assertEqual(read(self.config), "{not json")
        self.assertIn("cannot be edited", err)
        self.assertFalse(self.backup_folder.exists())

    def test_without_the_patched_dll_it_says_to_install_and_changes_nothing(self):
        (self.sim / "version.dll").unlink()
        self.configured(FIRST_RUN)
        code, _, err = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("addon install mfg-unlocker", err)
        self.assertEqual(read(self.config), FIRST_RUN)

    def test_a_version_dll_that_is_not_ours_stops_apply_and_is_named(self):
        self.installed(b"MZ another mod")
        self.configured(FIRST_RUN)
        code, _, err = self.apply()
        self.assertEqual(code, 1)
        self.assertIn(sha256(b"MZ another mod"), err)
        self.assertEqual(read(self.config), FIRST_RUN)

    def test_it_names_each_launch_option_that_is_missing_and_edits_no_steam_file(self):
        self.configured(FIRST_RUN)
        self.options("PROTON_ENABLE_WAYLAND=1 game %command%")
        before = tree(self.steam)
        code, out, _ = self.apply()
        self.assertEqual(code, 0)
        for text in ('WINEDLLOVERRIDES="version=n,b"', "PROTON_NVIDIA_NVCUDA=1", "PROTON_DLSS_UPGRADE=1", "ngx_dlss_fg_override=on in DXVK_NVAPI_DRS_SETTINGS"):
            self.assertIn(text, out)
        self.assertEqual(tree(self.steam), before)

    def test_it_names_only_what_is_missing(self):
        self.configured(FIRST_RUN)
        self.options(FULL_OPTIONS.replace("PROTON_DLSS_UPGRADE=1 ", ""))
        _, out, _ = self.apply()
        self.assertIn("PROTON_DLSS_UPGRADE=1", out)
        self.assertNotIn("PROTON_NVIDIA_NVCUDA=1", out)

    def test_with_every_option_in_place_it_says_so(self):
        self.configured(FIRST_RUN)
        _, out, _ = self.apply()
        self.assertIn("hold everything the mod needs", out)

    def test_it_does_not_ask_for_the_fifo_present_mode(self):
        self.configured(FIRST_RUN)
        _, out, err = self.apply()
        self.assertIn("hold everything the mod needs", out)
        self.assertNotIn("VKD3D_SWAPCHAIN_PRESENT_MODE", out + err)

    def test_the_launch_options_are_still_listed_when_the_config_file_is_missing(self):
        self.options("game %command%")
        code, out, _ = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("PROTON_NVIDIA_NVCUDA=1", out)

    def test_apply_then_check_passes_and_before_apply_check_fails(self):
        self.configured(FIRST_RUN)
        self.log.write_text(LOG)
        self.assertEqual(self.check()[0], 1)
        self.apply()
        self.assertEqual(self.check()[0], 0)


class MfgUndoTest(MfgCase):
    def test_it_removes_the_patched_dll_and_restores_the_config_byte_for_byte(self):
        self.installed()
        self.configured(FIRST_RUN)
        self.apply()
        self.assertNotEqual(read(self.config), FIRST_RUN)
        code, out, _ = self.undo()
        self.assertEqual(code, 0)
        self.assertFalse((self.sim / "version.dll").exists())
        self.assertEqual(read(self.config), FIRST_RUN)
        self.assertFalse((self.backup_folder / "RTXMFG-Universal.json.orig").exists())
        self.assertIn("as it was before apply", out)

    def test_a_version_dll_that_is_not_ours_stays_and_is_named(self):
        self.installed(b"MZ another mod")
        _, out, _ = self.undo()
        self.assertEqual((self.sim / "version.dll").read_bytes(), b"MZ another mod")
        self.assertIn(sha256(b"MZ another mod"), out)
        self.assertIn("so it stays", out)

    def test_without_apply_the_config_is_not_touched_and_it_says_so(self):
        self.installed()
        self.configured(FIXED.replace('"multiplier":2', '"multiplier":4'))
        _, out, _ = self.undo()
        self.assertEqual(json.loads(read(self.config))["multiplier"], 4)
        self.assertIn("nothing to restore", out)

    def test_it_lists_the_mod_files_to_delete_and_deletes_none_of_them(self):
        self.fixed_machine()
        for name in ("RTXMFG-Universal.status.json", "RTX40MFG-UI.ini", "RTX40MFG-UI-layout.ini", "RTXMFG-flightsimulator2024-A2C6379C1634BF0A-intervals.csv", "RTXMFG-flightsimulator2024-A2C6379C1634BF0A.previous.log", "ReShade.ini"):
            write(self.sim / name, "x")
        _, out, _ = self.undo()
        for name in ("RTXMFG-Universal.json", "RTXMFG-Universal.status.json", "RTX40MFG-UI.ini", "RTX40MFG-UI-layout.ini", "RTXMFG-flightsimulator2024-A2C6379C1634BF0A.log", "RTXMFG-flightsimulator2024-A2C6379C1634BF0A-intervals.csv", "RTXMFG-flightsimulator2024-A2C6379C1634BF0A.previous.log"):
            self.assertIn(name, out)
            self.assertTrue((self.sim / name).exists(), name)
        self.assertNotIn("ReShade.ini", out)

    def test_it_says_the_launch_options_are_the_readers_to_change(self):
        _, out, _ = self.undo()
        self.assertIn("launch options are not touched", out)

    def test_undo_twice_is_harmless(self):
        self.installed()
        self.configured(FIRST_RUN)
        self.apply()
        self.undo()
        code, _, _ = self.undo()
        self.assertEqual(code, 0)
        self.assertEqual(read(self.config), FIRST_RUN)

    def test_undo_then_check_fails(self):
        self.fixed_machine()
        self.assertEqual(self.check()[0], 0)
        self.undo()
        self.assertEqual(self.check()[0], 1)


class MfgScriptsTest(MfgCase):
    def test_check_reads_the_simulator_folder_and_the_steam_folder_it_is_given(self):
        self.configured(FIXED)
        self.log.write_text(LOG)
        result = self.script("check")
        lines = result.stdout.splitlines()
        self.assertEqual(result.returncode, 1)
        self.assertEqual([line.split()[0] for line in lines], ["ok", "FAIL"] + ["ok"] * 8)
        self.assertIn("addon install mfg-unlocker", lines[1])

    def test_check_without_a_simulator_folder_fails_with_the_next_step(self):
        result = self.script("check", FSOL_SIM_INSTALL="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL no simulator install folder found", result.stdout)

    def test_apply_without_the_dll_stops_and_says_to_install(self):
        self.configured(FIRST_RUN)
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install mfg-unlocker", result.stderr)
        self.assertEqual(read(self.config), FIRST_RUN)

    def test_undo_leaves_a_foreign_version_dll_where_it_is(self):
        self.installed(b"MZ another mod")
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.sim / "version.dll").read_bytes(), b"MZ another mod")

    def test_every_script_is_a_plain_shell_wrapper_around_the_helper(self):
        for name in ("apply", "check", "install", "undo"):
            self.assertTrue(os.access(MFG / name, os.X_OK), name)
            self.assertIn("python3 -I", (MFG / name).read_text())


class MfgPageTest(unittest.TestCase):
    def setUp(self):
        self.text = PAGE.read_text()

    def test_it_follows_the_template(self):
        self.assertEqual(page_problems("mfg-unlocker"), [])

    def test_it_gives_the_pinned_download_and_the_three_hashes(self):
        for needed in (ZIP_URL, ZIP_SHA256, DLL_SHA256, PATCHED_SHA256):
            self.assertIn(needed, self.text)

    def test_it_says_the_check_prints_ten_lines_and_eight_before_the_first_run(self):
        self.assertIn("ten lines", self.text)
        self.assertIn("eight", self.text)

    def test_it_gives_the_launch_options_the_check_looks_for(self):
        for needed in ('WINEDLLOVERRIDES="version=n,b"', "PROTON_NVIDIA_NVCUDA=1", "PROTON_DLSS_UPGRADE=1", "ngx_dlss_fg_override=on", "DXVK_NVAPI_DRS_SETTINGS"):
            self.assertIn(needed, self.text)

    def test_it_names_the_config_keys_the_menu_key_and_the_other_add_on(self):
        for needed in ("RTXMFG-Universal.json", "followGame", "fixed", "Backspace", "dlss5-nr.html", "glass-cockpit-dlss-fix.html", "reshade.html"):
            self.assertIn(needed, self.text)

    def test_it_says_which_patch_is_for_which_log_line(self):
        for needed in ("RtlIsThreadWithinLoaderCallout", "isfinite", "PAGE_WRITECOPY", "application graphics import publication unavailable", "DLSS 5 inputs refused", "123"):
            self.assertIn(needed, self.text)

    def test_it_offers_the_fifo_present_mode_for_tearing_outside_the_four_launch_options(self):
        self.assertIn(FIFO, self.text)
        self.assertIn("the four launch options", self.text)
        launch_line = re.search(r"<pre><code>(WINEDLLOVERRIDES=.*?)</code></pre>", self.text).group(1)
        self.assertNotIn("VKD3D_SWAPCHAIN_PRESENT_MODE", launch_line)
        tearing = next(item for item in re.findall(r"<li>(.*?)</li>", self.text) if FIFO in item)
        for needed in ("optional", "top and bottom", "IMMEDIATE", "Off no longer turns V-Sync off"):
            self.assertIn(needed, tearing)

    def test_it_says_the_repository_ships_no_dll(self):
        self.assertIn("ships no DLL", self.text)

    def test_its_numbers_match_the_patch_table(self):
        self.assertEqual(len(mfg.load_table()["patches"]), 7)
        self.assertIn("seven patches", self.text)


if __name__ == "__main__":
    unittest.main()
