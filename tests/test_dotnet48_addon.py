import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addons" / "dotnet48"
PARTS = ADDON / "parts"
PAGES = ROOT / "site" / "pages"


def load_module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dotnet48 = load_module("dotnet48_base", PARTS / "dotnet48.py")


def run_dotnet48(*args):
    parsed = dotnet48.parser().parse_args(list(args))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = parsed.run(parsed)
    return code, output.getvalue().strip()


def section(path, values):
    lines = [f"[{path.replace(chr(92), chr(92) * 2)}] 1792018458", "#time=1dd5c2ef1c5b6e6"]
    lines.extend(f'"{name}"="{value.replace(chr(92), chr(92) * 2)}"' for name, value in values.items())
    return "\n".join(lines) + "\n\n"


def registry_file(*sections):
    return "WINE REGISTRY Version 2\n;; All keys relative to \\\\User\\\\S-1-5-21-0-0-0-1000\n\n" + "".join(sections)



def user_sections():
    return [section(r"Software\Wine\DllOverrides", {"mscoree": "builtin"}), section(r"Software\Wine\Mono\AsmOverrides", {"*": "gac=n"})]


def system_sections():
    return [
        section(r"Software\Microsoft\.NETFramework", {"InstallRoot": "C:\\windows\\Microsoft.NET\\Framework64\\"}),
        section(r"Software\Wow6432Node\Microsoft\.NETFramework", {"InstallRoot": "C:\\windows\\Microsoft.NET\\Framework\\"}),
    ]


def version_resource(version):
    return b"MZ" + "ProductVersion".encode("utf-16le") + b"\0\0" + version.encode("utf-16le") + b"\0\0"




class PrefixCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prefix = self.root / "pfx"
        self.prefix.mkdir()

    def write_registry(self, user=None, system=None):
        (self.prefix / "user.reg").write_text(registry_file(*(user_sections() if user is None else user)))
        (self.prefix / "system.reg").write_text(registry_file(*(system_sections() if system is None else system)))

    def write_clr(self, folder, version):
        path = self.prefix / "drive_c/windows/Microsoft.NET" / folder / "v4.0.30319/clr.dll"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(version_resource(version))

    def write_mscoree(self, folder, data):
        path = self.prefix / "drive_c/windows" / folder / "mscoree.dll"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def check(self, *group):
        return run_dotnet48("check", str(self.prefix), *group)




class Dotnet48RegistryTest(PrefixCase):
    def test_a_complete_registry_is_all_ok(self):
        self.write_registry()
        code, output = self.check("registry")
        self.assertEqual(code, 0)
        self.assertEqual(len(output.splitlines()), 4)
        self.assertTrue(all(line.startswith("ok ") for line in output.splitlines()))

    def test_a_missing_gac_override_is_a_fail_line(self):
        self.write_registry(user=user_sections()[:1])
        code, output = self.check("registry")
        self.assertEqual(code, 1)
        fails = [line for line in output.splitlines() if line.startswith("FAIL ")]
        self.assertEqual(len(fails), 1)
        self.assertIn("wine-mono ignores the Windows GAC", fails[0])

    def test_a_global_native_override_is_a_fail(self):
        sections = user_sections()
        sections[0] = section(r"Software\Wine\DllOverrides", {"mscoree": "native"})
        self.write_registry(user=sections)
        code, output = self.check("registry")
        self.assertEqual(code, 1)
        self.assertIn("FAIL mscoree stays builtin", output)

    def test_the_32_bit_install_root_is_read_from_the_wow6432node_key(self):
        self.write_registry(system=system_sections()[:1])
        code, output = self.check("registry")
        self.assertEqual(code, 1)
        self.assertIn("FAIL .NETFramework InstallRoot (32-bit view)", output)
        self.assertIn("ok .NETFramework InstallRoot (64-bit view)", output)

    def test_the_wow6432node_key_alone_satisfies_only_the_32_bit_line(self):
        self.write_registry(system=system_sections()[1:])
        code, output = self.check("registry")
        self.assertEqual(code, 1)
        self.assertIn("ok .NETFramework InstallRoot (32-bit view)", output)
        self.assertIn("FAIL .NETFramework InstallRoot (64-bit view)", output)

    def test_a_prefix_without_registry_files_fails_every_line(self):
        code, output = self.check("registry")
        self.assertEqual(code, 1)
        self.assertEqual([line[:5] for line in output.splitlines()], ["FAIL "] * 4)

    def test_the_missing_settings_come_back_as_reg_add_arguments_with_the_32_bit_view_marked(self):
        self.write_registry(system=[])
        _, output = run_dotnet48("missing", str(self.prefix))
        lines = [line.split("\t") for line in output.splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertIn(["reg", "add", r"HKLM\Software\Microsoft\.NETFramework", "/v", "InstallRoot", "/t", "REG_SZ", "/d", "C:\\windows\\Microsoft.NET\\Framework\\", "/f", "/reg:32"], lines)
        self.assertIn(["reg", "add", r"HKLM\Software\Microsoft\.NETFramework", "/v", "InstallRoot", "/t", "REG_SZ", "/d", "C:\\windows\\Microsoft.NET\\Framework64\\", "/f"], lines)

    def test_nothing_is_missing_when_the_registry_is_complete(self):
        self.write_registry()
        self.assertEqual(run_dotnet48("missing", str(self.prefix)), (0, ""))

    def test_no_setting_names_a_program_of_an_add_on(self):
        for setting in dotnet48.SETTINGS:
            self.assertNotIn("AppDefaults", setting.path)

    def test_no_setting_touches_the_windows_version(self):
        for setting in dotnet48.SETTINGS:
            self.assertNotIn("Windows NT", setting.path)
            self.assertNotEqual((setting.path.lower(), setting.name.lower()), (r"software\wine", "version"))


            self.assertNotIn("Windows NT", setting.path)
            self.assertNotEqual((setting.path.lower(), setting.name.lower()), (r"software\wine", "version"))


WINDOWS_11 = {"ProductName": "Microsoft Windows 11", "CurrentVersion": "6.3", "CurrentBuild": "22000", "UBR": 588}
WINDOWS_7 = {"ProductName": "Microsoft Windows 7", "CurrentVersion": "6.1", "CurrentBuild": "7601", "CSDVersion": "Service Pack 1"}
CURRENT_VERSION = r"Software\Microsoft\Windows NT\CurrentVersion"


def typed_section(path, values):
    lines = [f"[{path.replace(chr(92), chr(92) * 2)}] 1792018458", "#time=1dd5c2ef1c5b6e6"]
    for name, value in values.items():
        lines.append(f'"{name}"=dword:{value:08x}' if isinstance(value, int) else f'"{name}"="{value}"')
    return "\n".join(lines) + "\n\n"



class WindowsVersionTest(PrefixCase):
    def write_version(self, system_values, wine_version=None):
        system = [typed_section(CURRENT_VERSION, system_values), typed_section(CURRENT_VERSION.replace("Software\\", "Software\\Wow6432Node\\", 1), system_values)]
        user = [] if wine_version is None else [section(r"Software\Wine", {"Version": wine_version})]
        self.write_registry(user=user, system=system)

    def restore(self, snapshot):
        _, output = run_dotnet48("winver-restore", str(self.prefix), str(snapshot))
        return [line.split("\t") for line in output.splitlines()]

    def test_the_snapshot_restores_changed_values_deletes_added_ones_and_brings_back_removed_ones(self):
        snapshot = self.root / "windows-version.json"
        self.write_version(WINDOWS_11)
        run_dotnet48("winver-save", str(self.prefix), str(snapshot))
        self.assertEqual(self.restore(snapshot), [])
        self.write_version(WINDOWS_7, wine_version="win7")
        lines = self.restore(snapshot)
        key = rf"HKLM\{CURRENT_VERSION}"
        self.assertIn(["reg", "add", key, "/v", "CurrentBuild", "/t", "REG_SZ", "/d", "22000", "/f"], lines)
        self.assertIn(["reg", "add", key, "/v", "UBR", "/t", "REG_DWORD", "/d", "588", "/f", "/reg:32"], lines)
        self.assertIn(["reg", "delete", key, "/v", "CSDVersion", "/f"], lines)
        self.assertIn(["reg", "delete", key, "/v", "CSDVersion", "/f", "/reg:32"], lines)
        self.assertIn(["reg", "delete", r"HKCU\Software\Wine", "/v", "Version", "/f"], lines)

    def test_a_wine_version_that_was_set_comes_back_as_it_was(self):
        snapshot = self.root / "windows-version.json"
        self.write_version(WINDOWS_11, wine_version="win10")
        run_dotnet48("winver-save", str(self.prefix), str(snapshot))
        self.write_version(WINDOWS_11, wine_version="win7")
        self.assertEqual(self.restore(snapshot), [["reg", "add", r"HKCU\Software\Wine", "/v", "Version", "/t", "REG_SZ", "/d", "win10", "/f"]])

    def test_other_values_of_the_wine_key_are_not_watched(self):
        snapshot = self.root / "windows-version.json"
        self.write_version(WINDOWS_11)
        run_dotnet48("winver-save", str(self.prefix), str(snapshot))
        self.write_registry(user=[section(r"Software\Wine", {"ShowDotFiles": "Y"})], system=[typed_section(CURRENT_VERSION, WINDOWS_11), typed_section(CURRENT_VERSION.replace("Software\\", "Software\\Wow6432Node\\", 1), WINDOWS_11)])
        self.assertEqual(self.restore(snapshot), [])


class Dotnet48FilesTest(PrefixCase):
    def test_clr_dll_must_be_a_4_8_build_in_both_folders(self):
        self.write_clr("Framework64", "4.8.3761.0")
        self.write_clr("Framework", "4.7.3190.0")
        code, output = self.check("clr")
        self.assertEqual(code, 1)
        self.assertIn("ok Framework64/v4.0.30319/clr.dll is .NET 4.8.x (4.8.3761.0)", output)
        self.assertIn("FAIL Framework/v4.0.30319/clr.dll is .NET 4.8.x (found 4.7.3190.0)", output)

    def test_a_missing_clr_dll_is_a_fail(self):
        code, output = self.check("clr")
        self.assertEqual(code, 1)
        self.assertEqual(output.count("(found no file)"), 2)

    def test_mscoree_is_judged_by_sha256(self):
        good = b"microsoft"
        wanted = {"system32": hashlib.sha256(good).hexdigest(), "syswow64": "0" * 64}
        self.write_mscoree("system32", good)
        self.write_mscoree("syswow64", b"wine builtin")
        with mock.patch.dict(dotnet48.MSCOREE_SHA256, wanted):
            code, output = self.check("mscoree")
        self.assertEqual(code, 1)
        self.assertIn("ok system32/mscoree.dll", output)
        self.assertIn("FAIL syswow64/mscoree.dll", output)


FAKE_WINE = '''#!/bin/sh
printf '%s\\037' "$WINEPREFIX" "$WINEDLLOVERRIDES" "$@" >> "$WINE_LOG"
printf '\\n' >> "$WINE_LOG"
case "$1" in
  *ndp48*)
    for folder in Framework64 Framework; do
      mkdir -p "$WINEPREFIX/drive_c/windows/Microsoft.NET/$folder/v4.0.30319"
      printf 'installed' > "$WINEPREFIX/drive_c/windows/Microsoft.NET/$folder/v4.0.30319/fusion.dll"
    done
    exit "${INSTALLER_EXIT:-0}"
    ;;
esac
'''
FAKE_WINESERVER = '''#!/bin/sh
printf 'wineserver|%s\\n' "$*" >> "$WINE_LOG"
'''


class PartsInBashTest(PrefixCase):
    def setUp(self):
        super().setUp()
        self.compatdata = self.root / "compatdata"
        self.base = self.root / "base"
        self.base.mkdir()
        self.community = self.root / "community"
        self.community.mkdir()
        self.cache = self.root / "cache"
        self.proton = self.root / "proton"
        wine = self.proton / "files/bin/wine"
        wine.parent.mkdir(parents=True)
        wine.write_text(FAKE_WINE)
        wine.chmod(0o755)
        wineserver = self.proton / "files/bin/wineserver"
        wineserver.write_text(FAKE_WINESERVER)
        wineserver.chmod(0o755)
        self.wine_log = self.root / "wine.log"
        self.env = {
            **os.environ,
            "HOME": str(self.root / "home"),
            "FSOL_PREFIX": str(self.prefix),
            "FSOL_COMPATDATA": str(self.compatdata),
            "FSOL_PROTON": str(self.proton),
            "FSOL_BASE": str(self.base),
            "FSOL_COMMUNITY": str(self.community),
            "FSOL_CACHE": str(self.cache),
            "WINE_LOG": str(self.wine_log),
            "WINEDLLOVERRIDES": "winemenubuilder.exe=d",
        }
        self.env.pop("STEAM_DIR", None)
        self.env.pop("FSOL_STEAM", None)


    def bash(self, script):
        text = f'set -euo pipefail\ncd "{ADDON}"\n. parts/common.sh\n. parts/dotnet48.sh\n{script}\n'
        return subprocess.run(["bash", "-c", text], env=self.env, capture_output=True, text=True)

    def wine_calls(self):
        if not self.wine_log.exists():
            return []
        return [line.rstrip("\x1f").split("\x1f") for line in self.wine_log.read_text().splitlines()]

    def wine_sequence(self):
        return ["kill" if call[0].startswith("wineserver|") else call[2:] for call in self.wine_calls()]

    def wine_arguments(self):
        return [call[2:] for call in self.wine_calls() if not call[0].startswith("wineserver|")]


    def test_a_download_is_cached_checked_and_printed_as_a_path(self):
        source = self.root / "payload.bin"
        source.write_bytes(b"payload")
        sha = hashlib.sha256(b"payload").hexdigest()
        result = self.bash(f'dotnet48_download "file://{source}" {sha} payload.bin; rm "{source}"; dotnet48_download "file://{source}" {sha} payload.bin')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [f"{self.cache}/downloads/payload.bin"] * 2)

    def test_a_download_with_the_wrong_sha256_dies_with_exit_2(self):
        source = self.root / "payload.bin"
        source.write_bytes(b"payload")
        result = self.bash(f'dotnet48_download "file://{source}" {"0" * 64} payload.bin')
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not match", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_cache_path_too_long_for_windows_installers_dies_before_downloading(self):
        source = self.root / "payload.bin"
        source.write_bytes(b"payload")
        long_cache = self.root / ("c" * 160)
        result = self.bash(f'FSOL_CACHE="{long_cache}"\ndotnet48_download "file://{source}" {"0" * 64} payload.bin')
        self.assertEqual(result.returncode, 2)
        self.assertIn("set FSOL_CACHE to a shorter folder", result.stderr)
        self.assertFalse((long_cache / "downloads").exists())

    def test_wine_runs_the_proton_wine_in_the_given_prefix_and_keeps_the_callers_overrides(self):
        result = self.bash('dotnet48_wine "$FSOL_PREFIX" reg query HKCU')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.wine_calls(), [[str(self.prefix), "winemenubuilder.exe=d", "reg", "query", "HKCU"]])

    def backup_folder(self):
        return self.compatdata / "flightsim-on-linux-backup/dotnet48"

    def outside_link(self, version, name):
        target = self.root / "outside" / f"{version}-{name}"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(b"builtin")
        return target

    def test_a_reboot_exit_code_is_not_a_failure_when_clr_is_48(self):
        with tempfile.TemporaryDirectory() as temp:
            mark = Path(temp) / "installed"
            script = f"""
dotnet48_py() {{ [ -f "{mark}" ]; }}
dotnet48_download() {{ echo /cache/installer.exe; }}
dotnet48_run_installer() {{ : > "{mark}"; return 194; }}
dotnet48_install_runtime
"""
            result = self.bash(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("changed installed .NET Framework 4.8", result.stdout)

    def test_the_installer_runs_after_windows_7_is_set_and_without_the_proton_symlinks(self):
        self.write_registry()
        outside = []
        for version in ("Framework64", "Framework"):
            folder = self.prefix / "drive_c/windows/Microsoft.NET" / version / "v4.0.30319"
            folder.mkdir(parents=True)
            builtin = self.outside_link(version, "fusion.dll")
            (folder / "fusion.dll").symlink_to(builtin)
            outside.append((folder / "fusion.dll", builtin))
        result = self.bash("dotnet48_run_installer /cache/ndp48-x86-x64-allos-enu.exe")
        self.assertEqual(result.returncode, 0, result.stderr)
        installer = "/cache/ndp48-x86-x64-allos-enu.exe"
        self.assertEqual(self.wine_sequence(), [["winecfg", "-v", "win7"], "kill", [installer, "/q", "/norestart"], "kill", "kill"])
        self.assertEqual(self.wine_calls()[2][1], "fusion=b")
        for installed, builtin in outside:
            self.assertEqual(builtin.read_bytes(), b"builtin")
            self.assertFalse(installed.is_symlink())
            self.assertEqual(installed.read_bytes(), b"installed")
        self.assertFalse((self.backup_folder() / "windows-version.json").exists())

    def test_the_windows_version_saved_by_an_earlier_run_is_restored_before_a_new_one_is_saved(self):
        (self.prefix / "system.reg").write_text(registry_file(typed_section(CURRENT_VERSION, WINDOWS_7)))
        (self.prefix / "user.reg").write_text(registry_file())
        self.backup_folder().mkdir(parents=True)
        saved = self.backup_folder() / "windows-version.json"
        saved.write_text(json.dumps([WINDOWS_11, {}, {}]))
        result = self.bash("dotnet48_run_installer /cache/ndp48-x86-x64-allos-enu.exe")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.wine_calls()[0][2:5], ["reg", "add", rf"HKLM\{CURRENT_VERSION}"])
        self.assertIn(["reg", "add", rf"HKLM\{CURRENT_VERSION}", "/v", "CurrentBuild", "/t", "REG_SZ", "/d", "22000", "/f"], self.wine_arguments())
        self.assertNotIn(["winecfg", "-v", "win7"], self.wine_sequence())
        self.assertEqual(json.loads(saved.read_text())[0], WINDOWS_11)

    def test_a_symlink_to_the_builtin_mscoree_is_replaced_and_never_written_through(self):
        outside = self.outside_link("system32", "mscoree.dll")
        target = self.prefix / "drive_c/windows/system32/mscoree.dll"
        target.parent.mkdir(parents=True)
        target.symlink_to(outside)
        source = self.root / "microsoft-mscoree.dll"
        source.write_bytes(b"microsoft")
        saved = self.backup_folder() / "mscoree/system32/mscoree.dll"
        result = self.bash(f'dotnet48_verify_mscoree() {{ :; }}\ndotnet48_place_mscoree system32 "{source}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outside.read_bytes(), b"builtin")
        self.assertFalse(target.is_symlink())
        self.assertEqual(target.read_bytes(), b"microsoft")
        self.assertTrue(saved.is_symlink())
        self.assertEqual(os.readlink(saved), str(outside))
        result = self.bash("dotnet48_restore_mscoree")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(target.is_symlink())
        self.assertEqual(os.readlink(target), str(outside))
        self.assertEqual(outside.read_bytes(), b"builtin")

    def test_a_failing_installer_still_gives_back_the_windows_version_and_fails_apply(self):
        self.write_registry()
        self.env["INSTALLER_EXIT"] = "3"
        result = self.bash("dotnet48_run_installer /cache/ndp48-x86-x64-allos-enu.exe")
        self.assertEqual(result.returncode, 3)
        self.assertFalse((self.backup_folder() / "windows-version.json").exists())

    def test_a_run_that_was_interrupted_restores_the_saved_windows_version_first(self):
        (self.prefix / "system.reg").write_text(registry_file(typed_section(CURRENT_VERSION, WINDOWS_7)))
        (self.prefix / "user.reg").write_text(registry_file())
        self.backup_folder().mkdir(parents=True)
        (self.backup_folder() / "windows-version.json").write_text(json.dumps([WINDOWS_11, {}, {}]))
        result = self.bash("dotnet48_restore_windows_version")
        self.assertEqual(result.returncode, 2)
        self.assertIn("did not go back", result.stderr)
        self.assertIn(["reg", "add", rf"HKLM\{CURRENT_VERSION}", "/v", "CurrentBuild", "/t", "REG_SZ", "/d", "22000", "/f"], self.wine_arguments())

    def test_undo_restores_mscoree_leaves_net_gac_and_the_overrides_of_other_add_ons(self):
        self.write_registry(user=user_sections() + [section(r"Software\Wine\AppDefaults\Fenix.exe\DllOverrides", {"mscoree": "native"})])
        self.write_mscoree("system32", b"microsoft 64")
        self.write_mscoree("syswow64", b"microsoft 32")
        saved = self.backup_folder() / "mscoree"
        (saved / "system32").mkdir(parents=True)
        (saved / "syswow64").mkdir(parents=True)
        (saved / "system32/mscoree.dll").write_bytes(b"wine 64")
        (saved / "syswow64/mscoree.dll").write_bytes(b"wine 32")
        result = self.bash("dotnet48_undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.wine_arguments(), [])
        self.assertEqual((self.prefix / "drive_c/windows/system32/mscoree.dll").read_bytes(), b"wine 64")
        self.assertEqual((self.prefix / "drive_c/windows/syswow64/mscoree.dll").read_bytes(), b"wine 32")
        self.assertFalse((saved / "system32/mscoree.dll").exists())
        self.assertIn("left .NET Framework 4.8 installed and gac=n", result.stdout)

    def test_the_backups_of_a_machine_that_ran_the_fenix_add_on_first_stay_where_they_were(self):
        legacy = self.compatdata / "flightsim-on-linux-backup/fenix-a320/mscoree"
        (legacy / "system32").mkdir(parents=True)
        (legacy / "syswow64").mkdir(parents=True)
        (legacy / "system32/mscoree.dll").write_bytes(b"wine 64")
        (legacy / "syswow64/mscoree.dll").write_bytes(b"wine 32")
        self.write_mscoree("system32", b"microsoft 64")
        self.write_mscoree("syswow64", b"microsoft 32")
        result = self.bash('echo "$DOTNET48_BACKUP"; dotnet48_undo')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], str(self.compatdata / "flightsim-on-linux-backup/fenix-a320"))
        self.assertEqual((self.prefix / "drive_c/windows/system32/mscoree.dll").read_bytes(), b"wine 64")
        self.assertFalse((self.compatdata / "flightsim-on-linux-backup/dotnet48").exists())

    def test_a_new_machine_keeps_its_backups_under_the_add_on_name(self):
        result = self.bash('echo "$DOTNET48_BACKUP"')
        self.assertEqual(result.stdout.strip(), str(self.compatdata / "flightsim-on-linux-backup/dotnet48"))

    def test_once_the_new_folder_exists_it_wins_over_an_old_fenix_one(self):
        (self.compatdata / "flightsim-on-linux-backup/fenix-a320/mscoree").mkdir(parents=True)
        (self.compatdata / "flightsim-on-linux-backup/dotnet48").mkdir(parents=True)
        result = self.bash('echo "$DOTNET48_BACKUP"')
        self.assertEqual(result.stdout.strip(), str(self.compatdata / "flightsim-on-linux-backup/dotnet48"))

    def test_check_prints_one_line_per_thing_and_fails_on_an_empty_prefix(self):
        result = self.bash("dotnet48_check || echo status=$?")
        lines = result.stdout.splitlines()
        self.assertEqual(lines[-1], "status=1")
        self.assertEqual(len(lines) - 1, 8)
        self.assertTrue(all(line.startswith(("ok ", "FAIL ")) for line in lines[:-1]))


class ScriptTest(PrefixCase):
    def setUp(self):
        super().setUp()
        self.repo = self.root / "repo"
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(ROOT / "fsol", self.repo / "fsol", ignore=ignore)
        shutil.copytree(ROOT / "tools", self.repo / "tools", ignore=ignore)
        shutil.copytree(ADDON, self.repo / "addons" / "dotnet48", ignore=ignore)
        self.mscoree = {"system32": b"microsoft 64", "syswow64": b"microsoft 32"}
        part = self.repo / "addons" / "dotnet48" / "parts" / "dotnet48.py"
        text = part.read_text()
        for folder, data in self.mscoree.items():
            text = text.replace(dotnet48.MSCOREE_SHA256[folder], hashlib.sha256(data).hexdigest())
            self.write_mscoree(folder, data)
        part.write_text(text)
        self.write_clr("Framework64", "4.8.3761.0")
        self.write_clr("Framework", "4.8.3761.0")
        self.compatdata = self.root / "compatdata"
        self.proton = self.root / "proton"
        self.wine_log = self.root / "wine.log"
        self.machine = {"prefix_in_use": False, "prefix_programs": []}
        wine = self.proton / "files/bin/wine"
        wine.parent.mkdir(parents=True)
        wine.write_text(FAKE_WINE)
        wine.chmod(0o755)
        wineserver = self.proton / "files/bin/wineserver"
        wineserver.write_text(FAKE_WINESERVER)
        wineserver.chmod(0o755)

    def script(self, name, **extra):
        cli = self.root / "cli"
        cli.write_text(f"#!/bin/sh\ncat <<'JSON'\n{json.dumps(self.machine)}\nJSON\n")
        cli.chmod(0o755)
        env = {
            **os.environ,
            "HOME": str(self.root / "home"),
            "FSOL_PREFIX": str(self.prefix),
            "FSOL_COMPATDATA": str(self.compatdata),
            "FSOL_PROTON": str(self.proton),
            "FSOL_CACHE": str(self.root / "cache"),
            "FSOL_CLI": str(cli),
            "WINE_LOG": str(self.wine_log),
            **extra,
        }
        return subprocess.run([str(self.repo / "addons" / "dotnet48" / name)], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def reg_adds(self):
        if not self.wine_log.exists():
            return []
        calls = [line.rstrip("\x1f").split("\x1f")[2:] for line in self.wine_log.read_text().splitlines() if not line.startswith("wineserver|")]
        return [call for call in calls if call[:2] == ["reg", "add"]]

    def test_apply_writes_the_four_settings_of_the_base_and_none_for_a_program(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.reg_adds()), 4)
        self.assertFalse(any("AppDefaults" in call[2] for call in self.reg_adds()))
        self.assertIn("already .NET Framework 4.8 in the prefix", result.stdout)
        self.assertIn("already Microsoft's mscoree.dll", result.stdout)

    def test_apply_with_the_registry_complete_changes_nothing(self):
        self.write_registry()
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.reg_adds(), [])
        self.assertIn("already the mscoree overrides, InstallRoot and gac=n", result.stdout)

    def test_apply_stops_when_another_program_holds_the_prefix(self):
        self.machine = {"prefix_in_use": True, "prefix_programs": ["Navigraph Hub.exe"]}
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Navigraph Hub.exe", result.stderr)
        self.assertEqual(self.reg_adds(), [])

    def test_check_passes_on_a_complete_prefix_and_names_what_is_missing_on_an_incomplete_one(self):
        self.write_registry()
        passed = self.script("check")
        self.assertEqual((passed.returncode, [line.split()[0] for line in passed.stdout.splitlines()]), (0, ["ok"] * 8))
        self.write_registry(user=[])
        failed = self.script("check")
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(sum(line.startswith("FAIL ") for line in failed.stdout.splitlines()), 2)

    def test_undo_puts_nothing_but_mscoree_back_and_says_what_it_leaves(self):
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("left .NET Framework 4.8 installed and gac=n", result.stdout)
        self.assertEqual(self.reg_adds(), [])


class ManifestTest(unittest.TestCase):
    def test_it_is_a_base_add_on_that_waits_for_a_closed_simulator_and_changes_no_file_of_an_add_on(self):
        from fsol import addons

        addon = addons.load(ADDON)
        self.assertEqual((addon.id, addon.needs, addon.touches_addon_files, addon.install), ("dotnet48", ["simulator-closed"], False, None))
        for script in ("apply", "check", "undo"):
            self.assertTrue(os.access(ADDON / script, os.X_OK), script)

    def test_the_scripts_name_no_program_of_an_add_on(self):
        for path in sorted(ADDON.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                text = path.read_text()
                for word in ("Fenix", "MDClient", "si_telemetry", "AppDefaults"):
                    self.assertNotIn(word, text, f"{path.relative_to(ROOT)} names {word}")


class PageTest(unittest.TestCase):
    def setUp(self):
        self.text = (PAGES / "dotnet48.html").read_text()

    def test_the_page_follows_the_template(self):
        from tests.test_maddog_addon import page_problems

        self.assertEqual(page_problems("dotnet48"), [])

    def test_the_page_says_what_the_scripts_do(self):
        for needed in (
            "ndp48-x86-x64-allos-enu.exe",
            "dotNetFx40_Full_x86_x64.exe",
            "winecfg -v win7",
            "addon apply dotnet48",
            "addon check dotnet48",
            "addon undo dotnet48",
            "gac=n",
            "18 October 2026",
            "flightsim-on-linux-backup/dotnet48",
            "flightsim-on-linux-backup/fenix-a320",
        ):
            self.assertIn(needed, self.text)

    def test_the_page_names_no_personal_path(self):
        self.assertNotIn("/home/", self.text)

    def test_the_add_on_list_and_the_installers_page_link_it(self):
        self.assertIn('href="dotnet48.html"', (PAGES / "addons.html").read_text())
        self.assertIn('href="dotnet48.html"', (PAGES / "installers.html").read_text())

    def test_the_add_on_list_gives_the_base_pieces_their_own_heading_with_simconnect_in_it(self):
        text = (PAGES / "addons.html").read_text()
        base = text[text.index("<h2>Base pieces</h2>"):text.index("<h2>Doesn't work</h2>")]
        self.assertIn('href="dotnet48.html"', base)
        self.assertIn('href="simconnect-tcp.html"', base)
        self.assertEqual(text.count('href="simconnect-tcp.html"'), 1)


if __name__ == "__main__":
    unittest.main()
