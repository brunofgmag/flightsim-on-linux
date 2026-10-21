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
ADDON = ROOT / "addons" / "fenix-a320"
PARTS = ADDON / "parts"
BOOTSTRAPPER = r"C:\Program Files\FenixSim A320\deps\FenixBootstrapper.exe"
BOM = b"\xef\xbb\xbf"


def load_module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


exe_xml = load_module("fsol_exe_xml", ROOT / "fsol" / "exe_xml.py")
dotnet48 = load_module("fenix_dotnet48", PARTS / "dotnet48.py")


def run_exe_xml(*args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = exe_xml.main(list(args))
    return code, output.getvalue().strip()


def run_dotnet48(*args):
    parsed = dotnet48.parser().parse_args(list(args))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = parsed.run(parsed)
    return code, output.getvalue().strip()


def entry(name, path, disabled="false", newline="\n", indent="    "):
    inner = indent * 2
    lines = [f"{indent}<Launch.Addon>", f"{inner}<Name>{name}</Name>", f"{inner}<Disabled>{disabled}</Disabled>", f"{inner}<Path>{path}</Path>", f"{indent}</Launch.Addon>"]
    return newline.join(lines) + newline


def document(entries, newline="\n", bom=b""):
    head = f'<?xml version="1.0" encoding="windows-1252"?>{newline}<SimBase.Document Type="Launch" version="1,0">{newline}    <Filename>exe.xml</Filename>{newline}'
    return bom + (head + "".join(entries) + "</SimBase.Document>" + newline).encode("latin-1")


class ExeXmlTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "base" / "exe.xml"

    def put(self, data):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_bytes(data)

    def status(self):
        return run_exe_xml("status", str(self.file), BOOTSTRAPPER)[1]

    def add(self):
        return run_exe_xml("add", "--name", "FenixA320", str(self.file), BOOTSTRAPPER)[1]

    def remove(self, *flags):
        return run_exe_xml("remove", *flags, str(self.file), BOOTSTRAPPER)[1]

    def test_a_missing_file_is_created_with_the_standard_wrapper_and_the_entry(self):
        self.assertEqual(self.status(), "no-file")
        self.assertEqual(self.add(), "created")
        self.assertEqual(self.file.read_bytes(), document([entry("FenixA320", BOOTSTRAPPER)]))
        self.assertEqual(self.status(), "enabled")

    def test_an_entry_that_is_present_is_left_alone_whatever_the_case_of_its_path(self):
        original = document([entry("Fenix", BOOTSTRAPPER.lower())], bom=BOM)
        self.put(original)
        self.assertEqual(self.status(), "enabled")
        self.assertEqual(self.add(), "present")
        self.assertEqual(self.file.read_bytes(), original)

    def test_an_entry_among_others_keeps_every_other_byte_the_bom_and_the_crlf(self):
        others = [entry("ChasePlane", r"C:\ChasePlane\cp.exe", newline="\r\n", indent="  "), entry("FSRealistic", r"C:\fsr.exe", disabled="True", newline="\r\n", indent="  ")]
        original = document(others, newline="\r\n", bom=BOM)
        self.put(original)
        self.assertEqual(self.status(), "absent")
        self.assertEqual(self.add(), "added")
        expected = document(others + [entry("FenixA320", BOOTSTRAPPER, newline="\r\n", indent="  ")], newline="\r\n", bom=BOM)
        self.assertEqual(self.file.read_bytes(), expected)
        self.assertTrue(self.file.read_bytes().startswith(BOM))

    def test_a_disabled_entry_is_reported_and_never_enabled(self):
        original = document([entry("FenixA320", BOOTSTRAPPER, disabled="True")])
        self.put(original)
        self.assertEqual(self.status(), "disabled")
        self.assertEqual(self.add(), "present")
        self.assertEqual(self.file.read_bytes(), original)

    def test_the_disabled_flag_may_come_after_the_path(self):
        self.put(b"<SimBase.Document>\n<Launch.Addon><Path>" + BOOTSTRAPPER.encode() + b"</Path><Disabled>TRUE</Disabled></Launch.Addon>\n</SimBase.Document>\n")
        self.assertEqual(self.status(), "disabled")

    def test_an_entry_inside_a_comment_does_not_count(self):
        commented = f"<!--\n{entry('FenixA320', BOOTSTRAPPER)}-->\n"
        self.put(document([commented]))
        self.assertEqual(self.status(), "absent")
        self.assertEqual(self.add(), "added")
        self.assertEqual(self.status(), "enabled")

    def test_removing_the_added_entry_gives_back_the_original_bytes(self):
        original = document([entry("ChasePlane", r"C:\cp.exe", newline="\r\n", indent="\t")], newline="\r\n", bom=BOM)
        self.put(original)
        self.add()
        self.assertEqual(self.remove(), "removed")
        self.assertEqual(self.file.read_bytes(), original)

    def test_removing_from_a_file_the_tool_created_deletes_it_when_nothing_else_is_in_it(self):
        self.add()
        self.assertEqual(self.remove("--delete-if-empty"), "deleted")
        self.assertFalse(self.file.exists())

    def test_the_created_file_stays_when_another_entry_was_added_after_it(self):
        self.add()
        text = self.file.read_bytes().decode("latin-1").replace("</SimBase.Document>", entry("Other", r"C:\o.exe") + "</SimBase.Document>")
        self.file.write_bytes(text.encode("latin-1"))
        self.assertEqual(self.remove("--delete-if-empty"), "removed")
        self.assertEqual(self.status(), "absent")
        self.assertIn(b"C:\\o.exe", self.file.read_bytes())

    def test_removing_an_entry_that_is_not_there_changes_nothing(self):
        original = document([entry("Other", r"C:\o.exe")])
        self.put(original)
        self.assertEqual(self.remove(), "absent")
        self.assertEqual(self.file.read_bytes(), original)

    def test_a_utf16_file_is_refused(self):
        self.put(b"\xff\xfe<\x00")
        with contextlib.redirect_stderr(io.StringIO()):
            code, _ = run_exe_xml("add", "--name", "FenixA320", str(self.file), BOOTSTRAPPER)
        self.assertEqual(code, 2)


def section(path, values):
    lines = [f"[{path.replace(chr(92), chr(92) * 2)}] 1792018458", "#time=1dd5c2ef1c5b6e6"]
    lines.extend(f'"{name}"="{value.replace(chr(92), chr(92) * 2)}"' for name, value in values.items())
    return "\n".join(lines) + "\n\n"


def registry_file(*sections):
    return "WINE REGISTRY Version 2\n;; All keys relative to \\\\User\\\\S-1-5-21-0-0-0-1000\n\n" + "".join(sections)


def fenix_user_sections():
    sections = [section(r"Software\Wine\DllOverrides", {"mscoree": "builtin"}), section(r"Software\Wine\Mono\AsmOverrides", {"*": "gac=n"})]
    sections.extend(section(rf"Software\Wine\AppDefaults\{exe}\DllOverrides", {"mscoree": "native"}) for exe in dotnet48.FENIX_EXECUTABLES)
    return sections


def fenix_system_sections():
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
        (self.prefix / "user.reg").write_text(registry_file(*(fenix_user_sections() if user is None else user)))
        (self.prefix / "system.reg").write_text(registry_file(*(fenix_system_sections() if system is None else system)))

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
        self.assertEqual(len(output.splitlines()), 9)
        self.assertTrue(all(line.startswith("ok ") for line in output.splitlines()))

    def test_each_missing_override_is_a_fail_line_naming_the_executable(self):
        sections = [item for item in fenix_user_sections() if "FenixDisplay.exe" not in item]
        self.write_registry(user=sections)
        code, output = self.check("registry")
        self.assertEqual(code, 1)
        fails = [line for line in output.splitlines() if line.startswith("FAIL ")]
        self.assertEqual(len(fails), 1)
        self.assertIn("FenixDisplay.exe", fails[0])

    def test_a_global_native_override_is_a_fail(self):
        sections = fenix_user_sections()
        sections[0] = section(r"Software\Wine\DllOverrides", {"mscoree": "native"})
        self.write_registry(user=sections)
        code, output = self.check("registry")
        self.assertEqual(code, 1)
        self.assertIn("FAIL mscoree stays builtin", output)

    def test_the_32_bit_install_root_is_read_from_the_wow6432node_key(self):
        self.write_registry(system=fenix_system_sections()[:1])
        code, output = self.check("registry")
        self.assertEqual(code, 1)
        self.assertIn("FAIL .NETFramework InstallRoot (32-bit view)", output)
        self.assertIn("ok .NETFramework InstallRoot (64-bit view)", output)

    def test_the_wow6432node_key_alone_satisfies_only_the_32_bit_line(self):
        self.write_registry(system=fenix_system_sections()[1:])
        code, output = self.check("registry")
        self.assertEqual(code, 1)
        self.assertIn("ok .NETFramework InstallRoot (32-bit view)", output)
        self.assertIn("FAIL .NETFramework InstallRoot (64-bit view)", output)

    def test_a_prefix_without_registry_files_fails_every_line(self):
        code, output = self.check("registry")
        self.assertEqual(code, 1)
        self.assertEqual([line[:5] for line in output.splitlines()], ["FAIL "] * 9)

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

    def test_only_the_app_overrides_that_exist_are_listed_for_removal(self):
        self.write_registry(user=fenix_user_sections()[:4])
        _, output = run_dotnet48("remove-overrides", str(self.prefix))
        keys = [line.split("\t")[2] for line in output.splitlines()]
        self.assertEqual(keys, [rf"HKCU\Software\Wine\AppDefaults\{exe}\DllOverrides" for exe in dotnet48.FENIX_EXECUTABLES[:2]])

    def test_no_setting_touches_the_windows_version(self):
        for setting in dotnet48.SETTINGS:
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


STUB_COMMON = '#!/usr/bin/env bash\necho "source:common" >> "$LOG"\n'
STUB_PART = """#!/usr/bin/env bash
echo "source:{part}" >> "$LOG"
{part}_apply() {{
  echo "{part}:apply" >> "$LOG"
  [ "${{BREAK:-}}" != "{part}" ] || {{ false; echo "{part}:after-break" >> "$LOG"; }}
}}
{part}_check() {{
  echo "{part}:check" >> "$LOG"
  [ "${{BREAK:-}}" != "{part}" ] || return 1
}}
{part}_undo() {{
  echo "{part}:undo" >> "$LOG"
}}
"""
PART_ORDER = ["dotnet48", "geometry", "dxvk", "exe_xml"]


class ScriptOrderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "fenix-a320"
        (self.folder / "parts").mkdir(parents=True)
        for action in ("apply", "check", "undo"):
            shutil.copy2(ADDON / action, self.folder / action)
        (self.folder / "parts" / "common.sh").write_text(STUB_COMMON)
        for part in PART_ORDER:
            (self.folder / "parts" / f"{part}.sh").write_text(STUB_PART.format(part=part))
        self.log = Path(self.temp.name) / "calls.log"

    def run_action(self, action, **env):
        result = subprocess.run([str(self.folder / action)], cwd=self.folder, env={**os.environ, "LOG": str(self.log), **env}, capture_output=True, text=True)
        calls = self.log.read_text().split() if self.log.exists() else []
        return result.returncode, calls

    def test_apply_sources_common_then_the_parts_and_applies_them_in_order(self):
        code, calls = self.run_action("apply")
        self.assertEqual(code, 0)
        self.assertEqual(calls[:5], ["source:common"] + [f"source:{part}" for part in PART_ORDER])
        self.assertEqual(calls[5:], [f"{part}:apply" for part in PART_ORDER])

    def test_apply_stops_at_the_first_failing_part_even_inside_the_function(self):
        code, calls = self.run_action("apply", BREAK="geometry")
        self.assertNotEqual(code, 0)
        applied = [call for call in calls if call.endswith(":apply") or call.endswith(":after-break")]
        self.assertEqual(applied, ["dotnet48:apply", "geometry:apply"])

    def test_check_runs_every_part_and_fails_if_any_failed(self):
        code, calls = self.run_action("check", BREAK="dotnet48")
        self.assertEqual(code, 1)
        self.assertEqual([call for call in calls if call.endswith(":check")], [f"{part}:check" for part in PART_ORDER])

    def test_check_passes_when_every_part_passes(self):
        code, _ = self.run_action("check")
        self.assertEqual(code, 0)

    def test_undo_runs_the_parts_in_reverse_order(self):
        code, calls = self.run_action("undo")
        self.assertEqual(code, 0)
        self.assertEqual([call for call in calls if call.endswith(":undo")], [f"{part}:undo" for part in reversed(PART_ORDER)])


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
            "FSOL_CACHE": str(self.cache),
            "WINE_LOG": str(self.wine_log),
            "WINEDLLOVERRIDES": "winemenubuilder.exe=d",
        }
        self.env.pop("STEAM_DIR", None)
        self.env.pop("FSOL_STEAM", None)

    def bash(self, script):
        text = f'set -euo pipefail\ncd "{ADDON}"\n. parts/common.sh\n. parts/dotnet48.sh\n. parts/exe_xml.sh\n{script}\n'
        return subprocess.run(["bash", "-c", text], env=self.env, capture_output=True, text=True)

    def wine_calls(self):
        if not self.wine_log.exists():
            return []
        return [line.rstrip("\x1f").split("\x1f") for line in self.wine_log.read_text().splitlines()]

    def wine_sequence(self):
        return ["kill" if call[0].startswith("wineserver|") else call[2:] for call in self.wine_calls()]

    def wine_arguments(self):
        return [call[2:] for call in self.wine_calls() if not call[0].startswith("wineserver|")]

    def test_common_defines_the_fenix_folders(self):
        result = self.bash('echo "$FENIX_DEPS"; echo "$FENIX_CONFIG"; echo "$FENIX_BACKUP"; echo "$FSOL_ROOT"')
        self.assertEqual(result.stdout.splitlines(), [
            f"{self.prefix}/drive_c/Program Files/FenixSim A320/deps",
            f"{self.prefix}/drive_c/ProgramData/Fenix/FenixSim A320/fenixConfig.xml",
            f"{self.compatdata}/flightsim-on-linux-backup/fenix-a320",
            str(ROOT),
        ])

    def test_a_download_is_cached_checked_and_printed_as_a_path(self):
        source = self.root / "payload.bin"
        source.write_bytes(b"payload")
        sha = hashlib.sha256(b"payload").hexdigest()
        result = self.bash(f'fenix_download "file://{source}" {sha} payload.bin; rm "{source}"; fenix_download "file://{source}" {sha} payload.bin')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [f"{self.cache}/downloads/payload.bin"] * 2)

    def test_a_download_with_the_wrong_sha256_dies_with_exit_2(self):
        source = self.root / "payload.bin"
        source.write_bytes(b"payload")
        result = self.bash(f'fenix_download "file://{source}" {"0" * 64} payload.bin')
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not match", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_cache_path_too_long_for_windows_installers_dies_before_downloading(self):
        source = self.root / "payload.bin"
        source.write_bytes(b"payload")
        long_cache = self.root / ("c" * 160)
        result = self.bash(f'FSOL_CACHE="{long_cache}"\nfenix_download "file://{source}" {"0" * 64} payload.bin')
        self.assertEqual(result.returncode, 2)
        self.assertIn("set FSOL_CACHE to a shorter folder", result.stderr)
        self.assertFalse((long_cache / "downloads").exists())

    def test_wine_runs_the_proton_wine_in_the_given_prefix_and_keeps_the_callers_overrides(self):
        result = self.bash('fenix_wine "$FSOL_PREFIX" reg query HKCU')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.wine_calls(), [[str(self.prefix), "winemenubuilder.exe=d", "reg", "query", "HKCU"]])

    def test_check_lines_are_ok_or_fail_for_the_bootstrapper_entry(self):
        failing = self.bash("exe_xml_check || echo status=$?")
        self.assertIn("FAIL exe.xml does not exist", failing.stdout)
        self.assertIn("status=1", failing.stdout)
        (self.base / "exe.xml").write_bytes(document([entry("FenixA320", BOOTSTRAPPER)]))
        self.assertEqual(self.bash("exe_xml_check").stdout.strip(), "ok exe.xml starts the Fenix bootstrapper")
        (self.base / "exe.xml").write_bytes(document([entry("FenixA320", BOOTSTRAPPER, disabled="true")]))
        self.assertIn("FAIL exe.xml has the Fenix bootstrapper entry with Disabled true", self.bash("exe_xml_check || true").stdout)

    def test_an_exe_xml_that_cannot_be_read_fails_check_and_stops_apply_without_touching_it(self):
        unreadable = b"\xff\xfe<\x00"
        (self.base / "exe.xml").write_bytes(unreadable)
        checked = self.bash("exe_xml_check")
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("FAIL exe.xml cannot be read: ", checked.stdout)
        self.assertIn("UTF-16", checked.stdout)
        applied = self.bash("exe_xml_apply")
        self.assertEqual(applied.returncode, 2)
        self.assertIn("exe.xml cannot be read: ", applied.stderr)
        self.assertEqual((self.base / "exe.xml").read_bytes(), unreadable)
        self.assertFalse((self.compatdata / "flightsim-on-linux-backup/fenix-a320/exe.xml").exists())

    def test_exe_xml_apply_adds_once_and_undo_restores_the_file(self):
        original = document([entry("Other", r"C:\o.exe", newline="\r\n")], newline="\r\n", bom=BOM)
        (self.base / "exe.xml").write_bytes(original)
        first = self.bash("exe_xml_apply")
        self.assertIn("changed exe.xml: added", first.stdout)
        added = (self.base / "exe.xml").read_bytes()
        self.assertEqual((self.compatdata / "flightsim-on-linux-backup/fenix-a320/exe.xml").read_bytes(), original)
        second = self.bash("exe_xml_apply")
        self.assertIn("already", second.stdout)
        self.assertEqual((self.base / "exe.xml").read_bytes(), added)
        self.assertIn("removed", self.bash("exe_xml_undo").stdout)
        self.assertEqual((self.base / "exe.xml").read_bytes(), original)

    def test_exe_xml_undo_keeps_an_entry_the_fenix_installer_wrote(self):
        original = document([entry("FenixA320", BOOTSTRAPPER)])
        (self.base / "exe.xml").write_bytes(original)
        self.assertIn("already", self.bash("exe_xml_apply").stdout)
        self.assertIn("already", self.bash("exe_xml_undo").stdout)
        self.assertEqual((self.base / "exe.xml").read_bytes(), original)

    def test_exe_xml_undo_deletes_a_file_that_apply_created(self):
        self.bash("exe_xml_apply")
        self.assertTrue((self.base / "exe.xml").is_file())
        self.bash("exe_xml_undo")
        self.assertFalse((self.base / "exe.xml").exists())

    def backup_folder(self):
        return self.compatdata / "flightsim-on-linux-backup/fenix-a320"

    def outside_link(self, version, name):
        target = self.root / "outside" / f"{version}-{name}"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(b"builtin")
        return target

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

    def test_dotnet48_undo_removes_the_app_overrides_restores_mscoree_and_leaves_net_and_gac(self):
        self.write_registry()
        self.write_mscoree("system32", b"microsoft 64")
        self.write_mscoree("syswow64", b"microsoft 32")
        saved = self.compatdata / "flightsim-on-linux-backup/fenix-a320/mscoree"
        (saved / "system32").mkdir(parents=True)
        (saved / "syswow64").mkdir(parents=True)
        (saved / "system32/mscoree.dll").write_bytes(b"wine 64")
        (saved / "syswow64/mscoree.dll").write_bytes(b"wine 32")
        result = self.bash("dotnet48_undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        deleted = [call for call in self.wine_arguments() if call[:2] == ["reg", "delete"]]
        self.assertEqual(deleted, [["reg", "delete", rf"HKCU\Software\Wine\AppDefaults\{exe}\DllOverrides", "/v", "mscoree", "/f"] for exe in dotnet48.FENIX_EXECUTABLES])
        self.assertEqual((self.prefix / "drive_c/windows/system32/mscoree.dll").read_bytes(), b"wine 64")
        self.assertEqual((self.prefix / "drive_c/windows/syswow64/mscoree.dll").read_bytes(), b"wine 32")
        self.assertFalse((saved / "system32/mscoree.dll").exists())
        self.assertIn("left .NET Framework 4.8 installed and gac=n", result.stdout)
        self.assertFalse(any("gac" in argument or "NETFramework" in argument for call in self.wine_arguments() for argument in call))

    def test_dotnet48_check_prints_one_line_per_thing_and_fails_on_an_empty_prefix(self):
        result = self.bash("dotnet48_check || echo status=$?")
        lines = result.stdout.splitlines()
        self.assertEqual(lines[-1], "status=1")
        self.assertEqual(len(lines) - 1, 13)
        self.assertTrue(all(line.startswith(("ok ", "FAIL ")) for line in lines[:-1]))


if __name__ == "__main__":
    unittest.main()
