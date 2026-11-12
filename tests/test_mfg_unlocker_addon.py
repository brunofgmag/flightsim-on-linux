import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
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
ZIP_URL = "https://github.com/brunofgmag/MFG-Unlocker/releases/download/v1.7.1-xmx.1/MFGUnlocker-v1.7.1-xmx.1.zip"
ZIP_SHA256 = "ca5719cbe73a7053422bf1a7794e1951f782865d5fd4f12721e9f1aa78605dea"
DLL_SHA256 = "6a606ef333ad5764b5b379b1486ef10ecf81654d1139a5267812a2ae3af1d001"
SDK_URL = "https://github.com/intel/xess/releases/download/v3.0.2/XeSS_SDK_3.0.2.zip"
SDK_SHA256 = "88b8a373f30e33f3558a77a93e634f11b8132fc3047ea1a8edeead32b8471990"
XESS_FG_SHA256 = "ec5e0c65e075570c6ede72618bb666d0be0c2e10b2ea9762c0fe8cb8e375ab27"
XELL_SHA256 = "d2030dcd694fda8f2ec7e044b13e6db8f0b56d4ba9113a5efad334e3f3ded8c7"
FULL_OPTIONS = 'STEAM_COMPAT_LIBRARY_PATHS=/games WINEDLLOVERRIDES="version=n,b" PROTON_NVIDIA_NVCUDA=1 PROTON_ENABLE_WAYLAND=1 PROTON_DLSS_UPGRADE=1 DXVK_NVAPI_DRS_SETTINGS=ngx_dlss_sr_override=on,ngx_dlss_sr_override_render_preset_selection=render_preset_m VKD3D_CONFIG=no_upload_hvv game %command%'
FIRST_RUN = '{"followGame":true,"mode":"follow","multiplier":2,"dynamicTargetFrameRate":0,"dlssgPreset":3,"vsyncMode":0,"reflexFrameLimitFps":0,"generatedOnlyDebug":false,"intervalLogging":true,"version":13,"xmx":0,"reflexSleepSkip":0,"xmxXell":0,"dlss5":false}'
MAILBOX = "VKD3D_SWAPCHAIN_PRESENT_MODE=MAILBOX"
FIXED = FIRST_RUN.replace('"followGame":true,"mode":"follow"', '"followGame":false,"mode":"fixed"').replace('"xmx":0', '"xmx":1').replace('"xmxXell":0', '"xmxXell":1')
LOG = """[17:33:56.275] Backend module: S:\\steamapps\\common\\MSFS2024\\VERSION.dll, file 1.7.1.177, product "MFG Unlocker" 1.7.1
[17:33:57.258] MFG_PROXY_UI state=active boundary=real-factory-return nativeTableWrites=0
[17:33:58.088] D157 D3D12 adapter verification: luid=0x00000000000003F2 capability=8.9 verified=1
[17:37:02.013] XMX runtime S:\\steamapps\\common\\MSFS2024\\RTXMFG-XeSS\\libxess_fg.dll and S:\\steamapps\\common\\MSFS2024\\RTXMFG-XeSS\\libxell.dll
[17:37:02.217] XMX init 3840x2160 format 24 flags 1 frames 3 result 0
"""
LOG_NO_CUDA = LOG.replace("capability=8.9 verified=1", "capability=0.0 verified=0")
LOG_NO_MENU = LOG.replace("MFG_PROXY_UI state=active", "MFG_PROXY_UI factory gateway first call")
LOG_NO_RUNTIME = LOG.replace("XMX runtime ", "XMX libraries ")
PROXY_IMAGE = bytes((index * 7 + 3) % 251 for index in range(1024))
XESS_FG_IMAGE = b"MZ the XeSS frame generation library"
XELL_IMAGE = b"MZ the XeLL library"
XESS_IMAGES = {"libxess_fg.dll": XESS_FG_IMAGE, "libxell.dll": XELL_IMAGE}
XESS_FOLDER = "RTXMFG-XeSS"


def load_module():
    spec = importlib.util.spec_from_file_location("mfg_parts", MFG / "parts" / "mfg.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mfg = load_module()


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
        self.assertEqual((addon.id, addon.name, addon.tested_version, addon.tested_on, addon.touches_addon_files), ("mfg-unlocker", "MFG Unlocker", "1.7.1-xmx.1", "2026-11-12", True))
        self.assertEqual(addon.needs, ["simulator-closed"])
        self.assertEqual(addon.installed, {"sim": ["version.dll"]})
        self.assertEqual(addon.install, {"from": "https://github.com/brunofgmag/MFG-Unlocker/releases", "kind": "native"})
        self.assertTrue(addon.can_install)
        self.assertFalse(addon.install_needs_file)

    def test_the_pinned_downloads_are_the_forks_release_and_intels_sdk(self):
        text = (MFG / "install").read_text()
        self.assertIn('MFG_VERSION="1.7.1-xmx.1"', text)
        self.assertIn("https://github.com/brunofgmag/MFG-Unlocker/releases/download/v$MFG_VERSION/MFGUnlocker-v$MFG_VERSION.zip", text)
        self.assertIn(f'MFG_SHA256="{ZIP_SHA256}"', text)
        self.assertIn(f'XESS_URL="{SDK_URL}"', text)
        self.assertIn(f'XESS_SHA256="{SDK_SHA256}"', text)

    def test_the_helper_pins_the_release_dll_and_the_two_intel_files(self):
        self.assertEqual(mfg.RELEASE, "1.7.1-xmx.1")
        self.assertEqual(mfg.PROXY_SHA256, DLL_SHA256)
        self.assertEqual(mfg.XESS_FILES, {"libxess_fg.dll": XESS_FG_SHA256, "libxell.dll": XELL_SHA256})
        self.assertEqual(mfg.XESS_FOLDER, XESS_FOLDER)

    def test_the_repository_ships_no_dll_and_no_zip(self):
        shipped = [path.name for path in MFG.rglob("*") if path.suffix.lower() in (".dll", ".zip")]
        self.assertEqual(shipped, [])

    def test_no_byte_patching_is_left(self):
        self.assertFalse((MFG / "parts" / "patches.json").exists())
        self.assertFalse(hasattr(mfg, "patch_image"))
        self.assertFalse(hasattr(mfg, "load_table"))


class MfgCase(SimulatorCase):
    ADDON = "mfg-unlocker"

    def setUp(self):
        super().setUp()
        self.steam = self.fake.steam
        self.backup_folder = self.backup("mfg-unlocker")
        self.config = self.sim / "RTXMFG-Universal.json"
        self.log = self.sim / "RTXMFG-flightsimulator2024-A2C6379C1634BF0A.log"
        self.xess = self.sim / XESS_FOLDER
        self.gpus(NVIDIA_GPU)
        self.patch_environment({"PATH": f"{self.root / 'bin'}:{os.environ['PATH']}"})
        self.patch_release()
        self.options(FULL_OPTIONS)

    def patch_environment(self, values):
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def patch_release(self):
        files = {name: sha256(image) for name, image in XESS_IMAGES.items()}
        for name, value in (("PROXY_SHA256", sha256(PROXY_IMAGE)), ("XESS_FILES", files)):
            patcher = mock.patch.object(mfg, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def gpus(self, *gpus):
        stub(self.root / "bin" / "vulkaninfo", f"#!/bin/sh\ncat <<'EOF'\n{summary(*gpus)}EOF\n")

    def options(self, text, user="1234"):
        write(self.steam / "userdata" / user / "config" / "localconfig.vdf", localconfig(text))

    def environment(self, **extra):
        env = super().environment(**extra)
        env.setdefault("FSOL_STEAM", str(self.steam))
        return env

    def installed(self, data=None):
        (self.sim / "version.dll").write_bytes(PROXY_IMAGE if data is None else data)

    def runtime(self, **replacing):
        self.xess.mkdir(exist_ok=True)
        for name, image in {**XESS_IMAGES, **replacing}.items():
            (self.xess / name).write_bytes(image)

    def configured(self, text=FIXED):
        self.config.write_bytes(text.encode())

    def fixed_machine(self):
        self.installed()
        self.runtime()
        self.configured()
        self.log.write_text(LOG)

    def call(self, function, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = function(*args)
        return code, out.getvalue(), err.getvalue()

    def check(self):
        code, out, _ = self.call(mfg.check, str(self.sim), str(self.steam))
        return code, out.splitlines()

    def apply(self):
        return self.call(mfg.apply, str(self.sim), str(self.steam), str(self.backup_folder))

    def undo(self):
        return self.call(mfg.undo, str(self.sim), str(self.backup_folder))

    def install(self, archive, sdk=None, sha=None, sdk_sha=None):
        sdk = sdk or self.sdk()
        return self.call(mfg.install, str(archive), sha or sha256(Path(archive).read_bytes()), str(sdk), sdk_sha or sha256(Path(sdk).read_bytes()), str(self.sim))

    def archive(self, members=None):
        return make_zip(self.root / "release.zip", members if members is not None else {"MFGUnlocker.dll": PROXY_IMAGE})

    def sdk(self, members=None):
        default = {f"bin/{name}": image for name, image in XESS_IMAGES.items()}
        return make_zip(self.root / "sdk.zip", members if members is not None else {**default, "include/xess/xess.h": b"header"})

    def nothing_written(self):
        self.assertFalse((self.sim / "version.dll").exists())
        self.assertFalse(self.xess.exists())
        self.assertEqual(sorted(path.name for path in self.sim.glob("*.part")), [])


class MfgInstallTest(MfgCase):
    def test_it_installs_the_fork_as_version_dll_and_the_two_intel_files_and_leaves_no_part_file(self):
        code, out, err = self.install(self.archive())
        self.assertEqual(code, 0, err)
        self.assertEqual((self.sim / "version.dll").read_bytes(), PROXY_IMAGE)
        for name, image in XESS_IMAGES.items():
            self.assertEqual((self.xess / name).read_bytes(), image)
        self.assertEqual(sorted(path.name for path in self.sim.glob("version.dll*")), ["version.dll"])
        self.assertEqual(sorted(path.name for path in self.xess.iterdir()), sorted(XESS_IMAGES))
        self.assertIn("addon apply mfg-unlocker", out)

    def test_it_takes_only_the_two_libraries_out_of_the_sdk(self):
        self.install(self.archive(), self.sdk({**{f"bin/{name}": image for name, image in XESS_IMAGES.items()}, "bin/libxess.dll": b"MZ the upscaler", "bin/libxell.pdb": b"symbols"}))
        self.assertEqual(sorted(path.name for path in self.xess.iterdir()), sorted(XESS_IMAGES))

    def test_a_zip_with_another_hash_than_the_one_given_is_refused_and_nothing_is_written(self):
        code, _, err = self.install(self.archive(), sha="0" * 64)
        self.assertEqual(code, 1)
        self.assertIn("not the release this add-on was tested with", err)
        self.nothing_written()

    def test_an_sdk_zip_with_another_hash_than_the_one_given_is_refused_and_nothing_is_written(self):
        code, _, err = self.install(self.archive(), sdk_sha="0" * 64)
        self.assertEqual(code, 1)
        self.assertIn("XeSS SDK", err)
        self.assertIn("not the release this add-on was tested with", err)
        self.nothing_written()

    def test_a_dll_that_is_not_the_release_is_refused_and_nothing_is_written(self):
        code, _, err = self.install(self.archive({"MFGUnlocker.dll": PROXY_IMAGE[:-1] + b"\xff"}))
        self.assertEqual(code, 1)
        self.assertIn("cannot be installed", err)
        self.nothing_written()

    def test_a_zip_without_the_dll_is_refused(self):
        code, _, err = self.install(self.archive({"readme.txt": b"hello"}))
        self.assertEqual(code, 1)
        self.assertIn("cannot be installed", err)
        self.nothing_written()

    def test_an_intel_library_that_is_not_the_one_tested_is_refused_and_not_even_the_dll_is_written(self):
        for name in XESS_IMAGES:
            with self.subTest(library=name):
                members = {f"bin/{other}": image for other, image in XESS_IMAGES.items()}
                members[f"bin/{name}"] = b"MZ another build"
                code, _, err = self.install(self.archive(), self.sdk(members))
                self.assertEqual(code, 1)
                self.assertIn("cannot be installed", err)
                self.assertIn(name, err)
                self.nothing_written()

    def test_an_sdk_without_one_of_the_libraries_is_refused_and_nothing_is_written(self):
        code, _, err = self.install(self.archive(), self.sdk({"bin/libxell.dll": XELL_IMAGE}))
        self.assertEqual(code, 1)
        self.assertIn("libxess_fg.dll", err)
        self.nothing_written()

    def test_a_file_that_is_not_a_zip_is_refused(self):
        junk = self.root / "junk.zip"
        junk.write_bytes(b"not a zip")
        code, _, _ = self.install(junk)
        self.assertEqual(code, 1)
        self.nothing_written()

    def test_an_sdk_that_is_not_a_zip_is_refused(self):
        junk = self.root / "junk.zip"
        junk.write_bytes(b"not a zip")
        code, _, _ = self.install(self.archive(), junk)
        self.assertEqual(code, 1)
        self.nothing_written()

    def test_a_missing_file_is_refused_and_named(self):
        code, _, err = self.install(self.root / "missing.zip", sha="0" * 64)
        self.assertEqual(code, 1)
        self.assertIn("missing.zip", err)

    def test_a_missing_sdk_is_refused_and_named(self):
        code, _, err = self.install(self.archive(), self.root / "missing-sdk.zip", sdk_sha="0" * 64)
        self.assertEqual(code, 1)
        self.assertIn("missing-sdk.zip", err)
        self.nothing_written()

    def test_a_version_dll_that_is_not_ours_is_never_overwritten_and_is_named_and_the_libraries_wait(self):
        self.installed(b"MZ another mod")
        code, _, err = self.install(self.archive())
        self.assertEqual(code, 1)
        self.assertEqual((self.sim / "version.dll").read_bytes(), b"MZ another mod")
        self.assertIn(str(self.sim / "version.dll"), err)
        self.assertIn(sha256(b"MZ another mod"), err)
        self.assertFalse(self.xess.exists())

    def test_an_intel_file_that_is_not_ours_is_never_overwritten_and_is_named_and_the_dll_waits(self):
        for name in XESS_IMAGES:
            with self.subTest(library=name):
                self.runtime(**{name: b"MZ somebody else's build"})
                code, _, err = self.install(self.archive())
                self.assertEqual(code, 1)
                self.assertEqual((self.xess / name).read_bytes(), b"MZ somebody else's build")
                self.assertIn(str(self.xess / name), err)
                self.assertIn(sha256(b"MZ somebody else's build"), err)
                self.assertFalse((self.sim / "version.dll").exists())

    def test_everything_already_there_is_left_alone(self):
        self.installed()
        self.runtime()
        before = [path.stat().st_mtime_ns for path in (self.sim / "version.dll", *sorted(self.xess.iterdir()))]
        code, out, _ = self.install(self.archive())
        self.assertEqual(code, 0)
        self.assertIn("already in", out)
        after = [path.stat().st_mtime_ns for path in (self.sim / "version.dll", *sorted(self.xess.iterdir()))]
        self.assertEqual(after, before)

    def test_the_dll_already_there_stays_and_only_the_libraries_are_added(self):
        self.installed()
        before = (self.sim / "version.dll").stat().st_mtime_ns
        code, out, err = self.install(self.archive())
        self.assertEqual(code, 0, err)
        self.assertEqual((self.sim / "version.dll").stat().st_mtime_ns, before)
        self.assertEqual(sorted(path.name for path in self.xess.iterdir()), sorted(XESS_IMAGES))
        self.assertNotIn("already in", out)

    def test_the_libraries_already_there_stay_and_only_the_dll_is_added(self):
        self.runtime()
        before = [path.stat().st_mtime_ns for path in sorted(self.xess.iterdir())]
        code, _, err = self.install(self.archive())
        self.assertEqual(code, 0, err)
        self.assertEqual((self.sim / "version.dll").read_bytes(), PROXY_IMAGE)
        self.assertEqual([path.stat().st_mtime_ns for path in sorted(self.xess.iterdir())], before)

    def test_a_folder_with_other_files_keeps_them(self):
        self.xess.mkdir()
        write(self.xess / "notes.txt", "mine")
        self.install(self.archive())
        self.assertEqual(read(self.xess / "notes.txt"), "mine")

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
        self.nothing_written()
        self.assertEqual(list((self.root / "cache" / "downloads").iterdir()), [])

    def test_the_sdk_is_not_downloaded_when_the_release_already_failed(self):
        self.script("install")
        self.assertNotIn(SDK_URL, self.curl_log.read_text())

    def test_a_file_given_by_hand_with_another_hash_is_refused_without_downloading(self):
        file = make_zip(self.root / "newer.zip", {"MFGUnlocker.dll": b"MZ a newer release"})
        result = self.script("install", FSOL_INSTALLER=str(file))
        self.assertEqual(result.returncode, 1)
        self.assertIn("not the release this add-on was tested with", result.stderr)
        self.assertIn(str(file), result.stderr)
        self.assertFalse(self.curl_log.exists())
        self.nothing_written()

    @unittest.skipUnless(os.environ.get("MFG_UNLOCKER_ZIP") and os.environ.get("XESS_SDK_ZIP"), "set MFG_UNLOCKER_ZIP and XESS_SDK_ZIP to the two real zips to run the real install")
    def test_the_real_zips_install_the_pinned_files(self):
        cache = self.root / "cache" / "downloads"
        cache.mkdir(parents=True)
        shutil.copy(os.environ["XESS_SDK_ZIP"], cache / "XeSS_SDK_3.0.2.zip")
        result = self.script("install", FSOL_INSTALLER=os.environ["MFG_UNLOCKER_ZIP"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.curl_log.exists())
        self.assertEqual(sha256((self.sim / "version.dll").read_bytes()), DLL_SHA256)
        self.assertEqual(sha256((self.xess / "libxess_fg.dll").read_bytes()), XESS_FG_SHA256)
        self.assertEqual(sha256((self.xess / "libxell.dll").read_bytes()), XELL_SHA256)


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

    def test_a_file_given_by_hand_with_the_right_hash_is_accepted_in_silence(self):
        write(self.target, self.asset.decode())
        result = self.bash(f'mfg_require "{self.target}" {self.sha}')
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_a_file_given_by_hand_with_another_hash_is_refused_and_named(self):
        write(self.target, self.asset.decode())
        result = self.bash(f'mfg_require "{self.target}" {"0" * 64}')
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(self.target), result.stderr)
        self.assertIn("not the release this add-on was tested with", result.stderr)

    def test_a_file_given_by_hand_that_is_missing_is_refused_and_named(self):
        result = self.bash(f'mfg_require "{self.root / "none.zip"}" {self.sha}')
        self.assertEqual(result.returncode, 1)
        self.assertIn("none.zip", result.stderr)


class MfgCheckTest(MfgCase):
    def setUp(self):
        super().setUp()
        self.fixed_machine()

    def failing(self, lines):
        return [line for line in lines if line.startswith("FAIL")]

    def only_failure(self, code, lines):
        self.assertEqual(code, 1, lines)
        self.assertEqual(len(self.failing(lines)), 1, lines)

    def test_a_fixed_machine_passes_with_fourteen_ok_lines(self):
        code, lines = self.check()
        self.assertEqual(code, 0, lines)
        self.assertEqual([line.split()[0] for line in lines], ["ok"] * 14)

    def test_without_a_log_the_log_lines_are_left_out(self):
        self.log.unlink()
        code, lines = self.check()
        self.assertEqual(code, 0, lines)
        self.assertEqual(len(lines), 11)

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

    def test_the_dll_line_says_it_is_the_fork(self):
        _, lines = self.check()
        self.assertIn("1.7.1-xmx.1", lines[1])

    def test_without_the_intel_folder_both_libraries_fail_and_say_to_install(self):
        shutil.rmtree(self.xess)
        code, lines = self.check()
        self.assertEqual(code, 1, lines)
        self.assertEqual(len(self.failing(lines)), 2, lines)
        for line, name in zip(lines[2:4], XESS_IMAGES):
            self.assertTrue(line.startswith("FAIL"), line)
            self.assertIn(name, line)
            self.assertIn("addon install mfg-unlocker", line)

    def test_a_missing_library_fails_alone_and_is_named(self):
        for index, name in enumerate(XESS_IMAGES):
            with self.subTest(library=name):
                self.runtime()
                (self.xess / name).unlink()
                code, lines = self.check()
                self.only_failure(code, lines)
                self.assertTrue(lines[2 + index].startswith("FAIL"), lines)
                self.assertIn(name, lines[2 + index])

    def test_a_library_that_is_not_the_one_tested_fails_alone_and_gives_its_hash(self):
        for index, name in enumerate(XESS_IMAGES):
            with self.subTest(library=name):
                self.runtime(**{name: b"MZ another build"})
                code, lines = self.check()
                self.only_failure(code, lines)
                self.assertTrue(lines[2 + index].startswith("FAIL"), lines)
                self.assertIn(sha256(b"MZ another build"), lines[2 + index])
                self.assertIn("does not overwrite it", lines[2 + index])

    def test_the_library_lines_name_the_sdk_the_files_come_from(self):
        _, lines = self.check()
        for line in lines[2:4]:
            self.assertIn("XeSS SDK 3.0.2", line)

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

    def test_the_frame_generation_override_is_not_needed_and_not_named_with_or_without_it(self):
        code, without = self.check()
        self.assertEqual(code, 0, without)
        self.options(FULL_OPTIONS.replace("ngx_dlss_sr_override=on", "ngx_dlss_fg_override=on,ngx_dlss_sr_override=on"))
        code, with_it = self.check()
        self.assertEqual(code, 0, with_it)
        self.assertNotIn("ngx_dlss_fg_override", "\n".join(without + with_it))

    def test_the_low_latency_extension_and_the_driver_key_are_not_needed_and_not_named_with_or_without_them(self):
        code, without = self.check()
        self.assertEqual(code, 0, without)
        self.options(FULL_OPTIONS.replace(" %command%", " __GL_0xcfcfa1=0 VKD3D_DISABLE_EXTENSIONS=VK_NV_low_latency2 %command%"))
        code, with_them = self.check()
        self.assertEqual(code, 0, with_them)
        for name in ("VK_NV_low_latency2", "0xcfcfa1"):
            self.assertNotIn(name, "\n".join(without + with_them))

    def test_a_word_after_the_command_is_not_a_launch_option(self):
        self.option_failure(FULL_OPTIONS.replace('WINEDLLOVERRIDES="version=n,b" ', "") + " WINEDLLOVERRIDES=version=n,b", 'WINEDLLOVERRIDES="version=n,b"')

    def test_no_launch_options_saved_fails_the_three_launch_lines_and_nothing_else(self):
        self.options("")
        code, lines = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.failing(lines)), 3)
        self.assertTrue(all("launch options" in line for line in self.failing(lines)))

    def test_options_held_by_any_steam_user_count(self):
        self.options("", user="1234")
        self.options(FULL_OPTIONS, user="5678")
        self.assertEqual(self.check()[0], 0)

    def test_the_present_mode_variable_is_not_needed_and_not_named_with_or_without_it(self):
        code, without = self.check()
        self.assertEqual(code, 0, without)
        self.options(FULL_OPTIONS.replace(" %command%", f" {MAILBOX} %command%"))
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

    def test_the_xmx_key_left_off_fails_alone_and_says_what_it_does(self):
        self.configured(FIXED.replace('"xmx":1', '"xmx":0'))
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn('"xmx" is 0', self.failing(lines)[0])
        self.assertIn("NVIDIA's generator", self.failing(lines)[0])

    def test_a_config_without_the_xmx_key_fails_alone(self):
        self.configured(FIXED.replace('"xmx":1,', ""))
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn('"xmx" is null', self.failing(lines)[0])

    def test_the_xmx_key_written_as_true_fails_because_the_mod_reads_only_the_number(self):
        self.configured(FIXED.replace('"xmx":1', '"xmx":true'))
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn('"xmx" is true', self.failing(lines)[0])

    def test_the_xell_key_left_off_fails_alone_and_says_what_it_does(self):
        self.configured(FIXED.replace('"xmxXell":1', '"xmxXell":0'))
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn('"xmxXell" is 0', self.failing(lines)[0])
        self.assertIn("XeLL", self.failing(lines)[0])

    def test_a_config_without_the_xell_key_fails_alone(self):
        self.configured(FIXED.replace(',"xmxXell":1', ""))
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn('"xmxXell" is null', self.failing(lines)[0])

    def test_the_xell_key_written_as_true_fails_because_the_mod_reads_only_the_number(self):
        self.configured(FIXED.replace('"xmxXell":1', '"xmxXell":true'))
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn('"xmxXell" is true', self.failing(lines)[0])

    def test_the_stand_in_key_is_not_asked_for_either_way(self):
        for text in (FIXED, FIXED.replace('"xmxXell":1', '"xmxXell":1,"xmxStandIn":0'), FIXED.replace('"xmxXell":1', '"xmxXell":1,"xmxStandIn":1')):
            self.configured(text)
            code, lines = self.check()
            self.assertEqual(code, 0, lines)
            self.assertNotIn("xmxStandIn", "\n".join(lines))

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

    def test_a_log_without_the_runtime_line_fails_alone_and_names_the_folder(self):
        self.log.write_text(LOG_NO_RUNTIME)
        code, lines = self.check()
        self.only_failure(code, lines)
        self.assertIn("XMX runtime", self.failing(lines)[0])
        self.assertIn(XESS_FOLDER, self.failing(lines)[0])

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
        self.assertEqual((code, len(lines)), (0, 11))

    def test_check_changes_nothing(self):
        before = tree(self.root)
        self.check()
        self.assertEqual(tree(self.root), before)


class MfgApplyTest(MfgCase):
    def setUp(self):
        super().setUp()
        self.installed()
        self.runtime()

    def test_it_sets_the_four_keys_and_keeps_every_other_byte(self):
        self.configured(FIRST_RUN)
        code, out, err = self.apply()
        self.assertEqual(code, 0, err)
        self.assertEqual(read(self.config), FIXED)
        self.assertIn("Backspace", out)
        self.assertIn("next start of the simulator", out)

    def test_the_multiplier_the_reader_chose_stays(self):
        self.configured(FIRST_RUN.replace('"multiplier":2', '"multiplier":5'))
        self.apply()
        self.assertEqual(json.loads(read(self.config))["multiplier"], 5)

    def test_a_pretty_printed_file_with_crlf_keeps_its_layout(self):
        text = json.dumps(json.loads(FIRST_RUN), indent=2).replace("\n", "\r\n") + "\r\n"
        self.configured(text)
        self.apply()
        expected = text.replace('"followGame": true', '"followGame": false').replace('"mode": "follow"', '"mode": "fixed"').replace('"xmx": 0', '"xmx": 1').replace('"xmxXell": 0', '"xmxXell": 1')
        self.assertEqual(self.config.read_bytes(), expected.encode())

    def test_a_missing_key_is_added_and_the_others_stay(self):
        self.configured('{"multiplier":3,"version":13}')
        code, _, err = self.apply()
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(read(self.config)), {"multiplier": 3, "version": 13, "followGame": False, "mode": "fixed", "xmx": 1, "xmxXell": 1})

    def test_the_two_xmx_keys_are_written_as_numbers_and_the_stand_in_the_reader_chose_stays(self):
        self.configured(FIRST_RUN.replace('"xmxXell":0', '"xmxXell":0,"xmxStandIn":0'))
        self.apply()
        text = read(self.config)
        self.assertIn('"xmx":1', text)
        self.assertIn('"xmxXell":1', text)
        self.assertIn('"xmxStandIn":0', text)
        self.assertNotIn('"xmx":true', text)

    def test_the_xmx_key_the_reader_turned_off_is_turned_on_and_the_original_is_kept(self):
        self.configured(FIRST_RUN)
        self.apply()
        self.assertEqual(json.loads(read(self.backup_folder / "RTXMFG-Universal.json.orig"))["xmx"], 0)
        self.assertEqual(json.loads(read(self.config))["xmx"], 1)

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

    def test_without_the_dll_it_says_to_install_and_changes_nothing(self):
        (self.sim / "version.dll").unlink()
        self.configured(FIRST_RUN)
        code, _, err = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("addon install mfg-unlocker", err)
        self.assertEqual(read(self.config), FIRST_RUN)
        self.assertFalse(self.backup_folder.exists())

    def test_without_the_intel_files_it_says_to_install_and_changes_nothing(self):
        shutil.rmtree(self.xess)
        self.configured(FIRST_RUN)
        code, _, err = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("addon install mfg-unlocker", err)
        self.assertIn("libxess_fg.dll", err)
        self.assertEqual(read(self.config), FIRST_RUN)
        self.assertFalse(self.backup_folder.exists())

    def test_an_intel_file_that_is_not_ours_stops_apply_and_is_named(self):
        for name in XESS_IMAGES:
            with self.subTest(library=name):
                self.runtime(**{name: b"MZ another build"})
                self.configured(FIRST_RUN)
                code, _, err = self.apply()
                self.assertEqual(code, 1)
                self.assertIn(sha256(b"MZ another build"), err)
                self.assertIn(name, err)
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
        for text in ('WINEDLLOVERRIDES="version=n,b"', "PROTON_NVIDIA_NVCUDA=1", "PROTON_DLSS_UPGRADE=1"):
            self.assertIn(text, out)
        self.assertNotIn("ngx_dlss_fg_override", out)
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

    def test_it_does_not_ask_for_the_present_mode_variable(self):
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
    def test_it_removes_the_dll_and_the_intel_files_and_restores_the_config_byte_for_byte(self):
        self.installed()
        self.runtime()
        self.configured(FIRST_RUN)
        self.apply()
        self.assertNotEqual(read(self.config), FIRST_RUN)
        code, out, _ = self.undo()
        self.assertEqual(code, 0)
        self.assertFalse((self.sim / "version.dll").exists())
        self.assertFalse(self.xess.exists())
        self.assertEqual(read(self.config), FIRST_RUN)
        self.assertFalse((self.backup_folder / "RTXMFG-Universal.json.orig").exists())
        self.assertIn("as it was before apply", out)
        self.assertIn("libxess_fg.dll", out)

    def test_an_intel_file_that_is_not_ours_stays_with_its_folder_and_is_named(self):
        for name in XESS_IMAGES:
            with self.subTest(library=name):
                self.runtime(**{name: b"MZ somebody else's build"})
                _, out, _ = self.undo()
                self.assertEqual((self.xess / name).read_bytes(), b"MZ somebody else's build")
                self.assertIn(sha256(b"MZ somebody else's build"), out)
                self.assertIn("so it stays", out)
                others = [other for other in XESS_IMAGES if other != name]
                self.assertEqual([other for other in others if (self.xess / other).exists()], [])

    def test_a_file_the_reader_keeps_in_the_intel_folder_stays_and_so_does_the_folder(self):
        self.runtime()
        write(self.xess / "notes.txt", "mine")
        self.undo()
        self.assertEqual(read(self.xess / "notes.txt"), "mine")
        self.assertEqual([path.name for path in self.xess.iterdir()], ["notes.txt"])

    def test_the_intel_folder_that_stays_is_not_listed_among_the_mod_files_to_delete(self):
        self.configured()
        self.runtime()
        write(self.xess / "notes.txt", "mine")
        _, out, _ = self.undo()
        listing = out.split("delete them if you want them gone: ")[1].splitlines()[0]
        self.assertIn("RTXMFG-Universal.json", listing)
        self.assertNotIn(XESS_FOLDER, listing)

    def test_without_the_intel_folder_it_says_so_and_goes_on(self):
        self.installed()
        code, out, _ = self.undo()
        self.assertEqual(code, 0)
        self.assertIn("is not in the simulator folder", out)
        self.assertFalse((self.sim / "version.dll").exists())

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
        self.runtime()
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
        self.assertEqual([line.split()[0] for line in lines], ["ok", "FAIL", "FAIL", "FAIL"] + ["ok"] * 10)
        self.assertIn("addon install mfg-unlocker", lines[1])

    def test_check_without_a_simulator_folder_fails_with_the_next_step(self):
        result = self.script("check", FSOL_SIM_INSTALL="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL no simulator install folder found", result.stdout)

    def test_undo_leaves_foreign_intel_files_and_their_folder_where_they_are(self):
        self.runtime()
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({path.name: path.read_bytes() for path in self.xess.iterdir()}, XESS_IMAGES)
        self.assertIn("so it stays", result.stdout)

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

    def test_it_gives_the_pinned_downloads_and_every_hash(self):
        for needed in (ZIP_URL, ZIP_SHA256, DLL_SHA256, SDK_URL, SDK_SHA256, XESS_FG_SHA256, XELL_SHA256, "1.3.1.78", "RTXMFG-XeSS"):
            self.assertIn(needed, self.text)

    def test_it_says_the_check_prints_fourteen_lines_and_eleven_before_the_first_run(self):
        self.assertIn("fourteen lines", self.text)
        self.assertIn("eleven", self.text)

    def launch_line(self):
        return re.search(r"<pre><code>(WINEDLLOVERRIDES=.*?)</code></pre>", self.text).group(1)

    def test_it_gives_the_three_launch_options_the_check_looks_for_and_no_more(self):
        line = self.launch_line()
        for needed in ('WINEDLLOVERRIDES="version=n,b"', "PROTON_NVIDIA_NVCUDA=1", "PROTON_DLSS_UPGRADE=1"):
            self.assertIn(needed, line)
        for gone in ("DXVK_NVAPI_DRS_SETTINGS", "ngx_dlss_fg_override", "VKD3D_SWAPCHAIN", "__GL_", "VK_NV_low_latency2"):
            self.assertNotIn(gone, line)

    def test_it_names_the_optional_options_outside_the_launch_line(self):
        for needed in ("__GL_0xcfcfa1=0", "620.70.06", "VKD3D_SWAPCHAIN_LATENCY_FRAMES=2", "8.3 ms", "launch-options.html"):
            self.assertIn(needed, self.text)

    def test_it_says_the_low_latency_extension_is_not_needed_with_xell_sleep(self):
        self.assertIn("you don't need <code>VKD3D_DISABLE_EXTENSIONS=VK_NV_low_latency2</code>", self.text)
        self.assertIn("42.8 ms to 33.3 ms", self.text)

    def test_it_names_the_config_keys_the_menu_key_and_the_other_add_on(self):
        for needed in ("RTXMFG-Universal.json", "followGame", "fixed", '"xmx"', '"xmxXell"', '"xmxStandIn"', "Backspace", "dlss5-nr.html", "glass-cockpit-dlss-fix.html", "reshade.html"):
            self.assertIn(needed, self.text)

    def test_it_says_what_the_fork_is_and_where_it_lives(self):
        for needed in ("https://github.com/brunofgmag/MFG-Unlocker", "releases/tag/v1.7.1-xmx.1", "dashdogy", "mavismmg", "XMX", "XeLL sleep", "940 MB", "PAGE_WRITECOPY", "Michael Robles"):
            self.assertIn(needed, self.text)

    def test_it_says_how_to_go_back_to_upstream(self):
        section = self.text.split("<h2>Go back to upstream</h2>")[1]
        for needed in ("addon undo mfg-unlocker", "version.dll", "dashdogy", "patches.json"):
            self.assertIn(needed, section)

    def tearing_item(self):
        return next(item for item in re.findall(r"<li>(.*?)</li>", self.text) if MAILBOX in item and "top and bottom" in item)

    def test_it_offers_mailbox_for_tearing_outside_the_launch_line(self):
        self.assertNotIn("VKD3D_SWAPCHAIN_PRESENT_MODE", self.launch_line())
        for needed in ("optional", "top and bottom", f"<code>{MAILBOX}</code> before <code>%command%</code>", "V-Sync to Off in the mod's menu has no effect"):
            self.assertIn(needed, self.tearing_item())

    def test_it_says_the_tearing_was_seen_with_the_upstream_release(self):
        self.assertIn("upstream 1.7.0", self.tearing_item())

    def test_it_says_fifo_holds_the_game_and_the_frame_rate_fell_with_it(self):
        for needed in ("<code>FIFO</code>", "holds the game until the screen refreshes", "frame rate fell", "Steam performance monitor turned red"):
            self.assertIn(needed, self.tearing_item())

    def test_it_says_vrr_took_away_the_hitch_left_without_it(self):
        for needed in ("without VRR", "small hitch every few seconds", "about 120 FPS", "went away once VRR was on", "Adaptive sync to Automatic"):
            self.assertIn(needed, self.tearing_item())

    def test_it_says_the_variable_is_ignored_where_the_driver_lacks_mailbox(self):
        for needed in ("has to offer MAILBOX", "ignores the variable", "Wayland window", "<code>PROTON_ENABLE_WAYLAND=1</code>", "not for an X11 one"):
            self.assertIn(needed, self.tearing_item())

    def test_it_no_longer_offers_fifo_or_a_frame_limit_below_the_refresh_rate(self):
        for gone in ("VKD3D_SWAPCHAIN_PRESENT_MODE=FIFO", "below the refresh rate", "Reflex FPS limit"):
            self.assertNotIn(gone, self.text)

    def test_it_says_the_repository_ships_no_dll(self):
        self.assertIn("ships no DLL", self.text)

    def test_it_no_longer_describes_byte_patches_as_something_the_install_does(self):
        for gone in ("123 bytes", "seven patches", "The seven patches", "patched at install", "RtlIsThreadWithinLoaderCallout"):
            self.assertNotIn(gone, self.text)
        self.assertIn("patches nothing any more", self.text)


if __name__ == "__main__":
    unittest.main()
