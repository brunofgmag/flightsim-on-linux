import contextlib
import hashlib
import importlib.util
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addons" / "fenix-a320"
PARTS = ADDON / "parts"
BOOTSTRAPPER = r"C:\Program Files\FenixSim A320\deps\FenixBootstrapper.exe"
FENIX_PROTON_FIXES = [
    ("windows-storage-applicationdata-no-package.patch", "lib/wine/x86_64-windows/windows.storage.applicationdata.dll",
     "Proton's windows.storage.applicationdata.dll lets the Fenix Installer open its package database"),
    ("wintrust-create-indirect-data.patch", "lib/wine/x86_64-windows/wintrust.dll",
     "Proton's wintrust.dll lets the Fenix 3 runner check its own signature"),
    ("ntdll-globalroot-device-paths.patch", "lib/wine/x86_64-unix/ntdll.so",
     "Proton's ntdll.so opens the device paths the Fenix 3 runner starts its programs from"),
    ("ntdll-device-path-current-directory.patch", "lib/wine/x86_64-windows/ntdll.dll",
     "Proton's ntdll.dll keeps the device path FenixSystem 3 starts in as its current folder"),
    ("bcrypt-pbkdf2-pseudo-handle.patch", "lib/wine/x86_64-windows/bcrypt.dll",
     "Proton's bcrypt.dll gives FenixSystem 3 the PBKDF2 handle its authorization uses"),
    ("ncrypt-ecdh-secret-agreement.patch", "lib/wine/x86_64-windows/ncrypt.dll",
     "Proton's ncrypt.dll lets FenixSystem 3 and the runner agree on a session key"),
    ("ntdll-fenix-gateway-loader-case.patch", "lib/wine/x86_64-windows/ntdll.dll",
     "Proton's ntdll.dll gives the Fenix 3 EFB gateway the Windows spelling of KERNEL32.DLL"),
]
BOM = b"\xef\xbb\xbf"


from tests.test_dotnet48_addon import FAKE_WINE, FAKE_WINESERVER, PrefixCase, section  # noqa: E402


def fenix_user_sections():
    return [section(rf"Software\Wine\AppDefaults\{exe}\DllOverrides", {"mscoree": "native"}) for exe in overrides.FENIX_EXECUTABLES]


def load_module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


exe_xml = load_module("fsol_exe_xml", ROOT / "fsol" / "exe_xml.py")
overrides = load_module("fenix_overrides", PARTS / "overrides.py")


def run_exe_xml(*args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = exe_xml.main(list(args))
    return code, output.getvalue().strip()


def run_overrides(*args):
    parsed = overrides.parser().parse_args(list(args))
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


class FenixOverridesTest(PrefixCase):
    def check(self):
        return run_overrides("check", str(self.prefix))

    def test_a_complete_registry_is_all_ok(self):
        self.write_registry(user=fenix_user_sections())
        code, output = self.check()
        self.assertEqual(code, 0)
        self.assertEqual(len(output.splitlines()), 5)
        self.assertTrue(all(line.startswith("ok ") for line in output.splitlines()))

    def test_each_missing_override_is_a_fail_line_naming_the_executable(self):
        sections = [item for item in fenix_user_sections() if "FenixDisplay.exe" not in item]
        self.write_registry(user=sections)
        code, output = self.check()
        self.assertEqual(code, 1)
        fails = [line for line in output.splitlines() if line.startswith("FAIL ")]
        self.assertEqual(len(fails), 1)
        self.assertIn("FenixDisplay.exe", fails[0])

    def test_a_prefix_without_registry_files_fails_every_line(self):
        code, output = self.check()
        self.assertEqual(code, 1)
        self.assertEqual([line[:5] for line in output.splitlines()], ["FAIL "] * 5)

    def test_the_missing_overrides_come_back_as_reg_add_arguments(self):
        self.write_registry(user=fenix_user_sections()[:3])
        _, output = run_overrides("missing", str(self.prefix))
        keys = [line.split("\t")[2] for line in output.splitlines()]
        self.assertEqual(keys, [rf"HKCU\Software\Wine\AppDefaults\{exe}\DllOverrides" for exe in overrides.FENIX_EXECUTABLES[3:]])
        self.assertTrue(all(line.split("\t")[7:9] == ["/d", "native"] for line in output.splitlines()))

    def test_nothing_is_missing_when_the_registry_is_complete(self):
        self.write_registry(user=fenix_user_sections())
        self.assertEqual(run_overrides("missing", str(self.prefix)), (0, ""))

    def test_only_the_overrides_that_exist_are_listed_for_removal(self):
        self.write_registry(user=fenix_user_sections()[:2])
        _, output = run_overrides("remove", str(self.prefix))
        keys = [line.split("\t")[2] for line in output.splitlines()]
        self.assertEqual(keys, [rf"HKCU\Software\Wine\AppDefaults\{exe}\DllOverrides" for exe in overrides.FENIX_EXECUTABLES[:2]])

    def test_the_overrides_are_the_only_settings_and_none_touches_the_general_ones(self):
        self.assertEqual(len(overrides.SETTINGS), len(overrides.FENIX_EXECUTABLES))
        for setting in overrides.SETTINGS:
            self.assertIn("AppDefaults", setting.path)
            self.assertEqual((setting.name, setting.value), ("mscoree", "native"))



STUB_COMMON = '#!/usr/bin/env bash\necho "source:common" >> "$LOG"\naddon_prefix_free_or_say() { [ -z "${BUSY:-}" ] || exit 1; }\n'
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
PART_ORDER = ["dotnet", "geometry", "dxvk", "exe_xml"]
CHECK_ORDER = PART_ORDER + ["proton", "community"]


class ScriptOrderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "fenix-a320"
        (self.folder / "parts").mkdir(parents=True)
        for action in ("apply", "check", "undo"):
            shutil.copy2(ADDON / action, self.folder / action)
        (self.folder / "parts" / "common.sh").write_text(STUB_COMMON)
        for part in CHECK_ORDER:
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

    def test_apply_changes_nothing_while_the_prefix_is_busy(self):
        code, calls = self.run_action("apply", BUSY="1")
        self.assertNotEqual(code, 0)
        self.assertEqual([call for call in calls if call.endswith(":apply")], [])

    def test_apply_stops_at_the_first_failing_part_even_inside_the_function(self):
        code, calls = self.run_action("apply", BREAK="geometry")
        self.assertNotEqual(code, 0)
        applied = [call for call in calls if call.endswith(":apply") or call.endswith(":after-break")]
        self.assertEqual(applied, ["dotnet:apply", "geometry:apply"])

    def test_check_runs_every_part_and_fails_if_any_failed(self):
        code, calls = self.run_action("check", BREAK="dotnet")
        self.assertEqual(code, 1)
        self.assertEqual([call for call in calls if call.endswith(":check")], [f"{part}:check" for part in CHECK_ORDER])

    def test_check_passes_when_every_part_passes(self):
        code, _ = self.run_action("check")
        self.assertEqual(code, 0)

    def test_check_also_looks_at_proton_and_fails_when_only_proton_fails(self):
        code, calls = self.run_action("check", BREAK="proton")
        self.assertEqual(code, 1)
        self.assertIn("proton:check", calls)

    def test_apply_and_undo_leave_proton_alone(self):
        self.run_action("apply")
        self.run_action("undo")
        self.assertFalse([call for call in self.log.read_text().split() if call.startswith("proton")])

    def test_undo_runs_the_parts_in_reverse_order(self):
        code, calls = self.run_action("undo")
        self.assertEqual(code, 0)
        self.assertEqual([call for call in calls if call.endswith(":undo")], [f"{part}:undo" for part in reversed(PART_ORDER)])


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
        text = f'set -euo pipefail\ncd "{ADDON}"\n. parts/common.sh\n. parts/dotnet.sh\n. parts/exe_xml.sh\n{script}\n'
        return subprocess.run(["bash", "-c", text], env=self.env, capture_output=True, text=True)

    def proton_check(self):
        text = f'set -euo pipefail\ncd "{ADDON}"\n. parts/common.sh\n. parts/proton.sh\nproton_check\n'
        return subprocess.run(["bash", "-c", text], env=self.env, capture_output=True, text=True)

    def community_check(self):
        text = f'set -uo pipefail\ncd "{ADDON}"\n. parts/common.sh\n. parts/community.sh\ncommunity_check\n'
        return subprocess.run(["bash", "-c", text], env=self.env, capture_output=True, text=True)

    def test_community_passes_when_the_fenix_packages_are_real_folders(self):
        for name in ("fnx-aircraft-320", "fnx-aircraft-319-321"):
            (self.community / name).mkdir()
        result = self.community_check()
        self.assertEqual((result.returncode, result.stdout), (0, "ok no package named fnx-* in Community is a Linux link\n"))

    def test_community_passes_without_any_fenix_package(self):
        self.assertEqual(self.community_check().returncode, 0)

    def test_a_fenix_package_that_is_a_link_fails_and_the_line_says_why(self):
        target = self.root / "organiser/fnx-aircraft-320"
        target.mkdir(parents=True)
        (self.community / "fnx-aircraft-320").symlink_to(target)
        (self.community / "fnx-aircraft-319-321").mkdir()
        result = self.community_check()
        self.assertEqual(result.returncode, 1)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("FAIL fnx-aircraft-320 in Community is a Linux link"))
        for expected in ("replaces every file of the package", "deleted the folder behind a link", "delete the link with rm, not the folder"):
            self.assertIn(expected, lines[0])

    def test_every_linked_fenix_package_gets_its_own_line(self):
        target = self.root / "organiser"
        target.mkdir()
        for name in ("fnx-aircraft-320", "fnx-aircraft-319-321"):
            (self.community / name).symlink_to(target)
        result = self.community_check()
        self.assertEqual([line.split()[1] for line in result.stdout.splitlines()], ["fnx-aircraft-319-321", "fnx-aircraft-320"])

    def test_a_link_to_another_package_is_not_the_checks_business(self):
        target = self.root / "organiser"
        target.mkdir()
        (self.community / "gsx-profiles").symlink_to(target)
        self.assertEqual(self.community_check().returncode, 0)

    def test_a_community_folder_that_was_not_found_fails(self):
        self.env["FSOL_COMMUNITY"] = ""
        result = self.community_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL the Community folder was not found", result.stdout)

    def write_proton_build(self, stock=()):
        record = []
        for patch, module, _ in FENIX_PROTON_FIXES:
            path = self.proton / "files" / module
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"stock" if module in stock else module.encode())
            patch_sha = hashlib.sha256((ROOT / "patches" / "wine" / patch).read_bytes()).hexdigest()
            record += [f"patch {patch_sha} {patch}", f"module {hashlib.sha256(module.encode()).hexdigest()} {module}"]
        (self.proton / "fs2024-build.txt").write_text("\n".join(record) + "\n")

    def test_the_proton_check_passes_on_a_build_with_every_fenix_fix(self):
        self.write_proton_build()
        result = self.proton_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines(), [f"ok {text}" for _, _, text in FENIX_PROTON_FIXES])

    def test_the_proton_check_fails_on_a_build_without_the_installer_fix(self):
        self.write_proton_build(stock={"lib/wine/x86_64-windows/windows.storage.applicationdata.dll"})
        result = self.proton_check()
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stdout.startswith("FAIL "), result.stdout)
        self.assertIn("proton build", result.stdout)

    def test_the_proton_check_names_the_one_fenix_3_fix_a_build_lacks(self):
        self.write_proton_build(stock={"lib/wine/x86_64-windows/ncrypt.dll"})
        result = self.proton_check()
        self.assertEqual(result.returncode, 1)
        failures = [line for line in result.stdout.splitlines() if line.startswith("FAIL ")]
        self.assertEqual(len(failures), 1, result.stdout)
        self.assertIn("ncrypt.dll", failures[0])
        self.assertEqual(len(result.stdout.splitlines()), len(FENIX_PROTON_FIXES))

    def test_the_proton_check_fails_when_the_machine_found_no_proton(self):
        self.env["FSOL_PROTON"] = ""
        result = self.proton_check()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.splitlines(), ["FAIL no Proton found; run: flightsim-on-linux machine"])

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

    def test_the_overrides_are_written_for_the_fenix_programs_only_after_the_base_add_on_did_its_part(self):
        self.write_registry(user=[], system=[])
        result = self.bash("dotnet48_apply() { echo base >> \"$WINE_LOG.order\"; }\ndotnet_apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "wine.log.order").read_text().split(), ["base"])
        added = [call[call.index("/v") - 1] for call in self.wine_arguments() if call[:2] == ["reg", "add"]]
        self.assertEqual(added, [rf"HKCU\Software\Wine\AppDefaults\{exe}\DllOverrides" for exe in overrides.FENIX_EXECUTABLES])
        self.assertIn("changed 5 registry values", result.stdout)

    def test_the_overrides_are_not_written_again_and_the_user_registry_is_exported_first(self):
        self.write_registry(user=[], system=[])
        self.bash("dotnet48_apply() { :; }\ndotnet_apply")
        exported = [call for call in self.wine_arguments() if call[:2] == ["reg", "export"]]
        self.assertEqual([call[2] for call in exported], [r"HKCU\Software\Wine\AppDefaults"])
        self.write_registry(user=fenix_user_sections())
        self.wine_log.unlink()
        result = self.bash("dotnet48_apply() { :; }\ndotnet_apply")
        self.assertEqual(self.wine_arguments(), [])
        self.assertIn("already mscoree native for the Fenix executables", result.stdout)

    def test_check_has_the_base_lines_and_the_override_lines_and_fails_on_an_empty_prefix(self):
        result = self.bash("dotnet_check || echo status=$?")
        lines = result.stdout.splitlines()
        self.assertEqual(lines[-1], "status=1")
        self.assertEqual(len(lines) - 1, 13)
        self.assertTrue(all(line.startswith(("ok ", "FAIL ")) for line in lines[:-1]))

    def test_undo_removes_the_fenix_overrides_and_leaves_the_base_to_its_own_add_on(self):
        self.write_registry(user=fenix_user_sections())
        self.write_mscoree("system32", b"microsoft 64")
        saved = self.compatdata / "flightsim-on-linux-backup/dotnet48/mscoree/system32"
        saved.mkdir(parents=True)
        (saved / "mscoree.dll").write_bytes(b"wine 64")
        result = self.bash("dotnet_undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        deleted = [call for call in self.wine_arguments() if call[:2] == ["reg", "delete"]]
        self.assertEqual(deleted, [["reg", "delete", rf"HKCU\Software\Wine\AppDefaults\{exe}\DllOverrides", "/v", "mscoree", "/f"] for exe in overrides.FENIX_EXECUTABLES])
        self.assertEqual((self.prefix / "drive_c/windows/system32/mscoree.dll").read_bytes(), b"microsoft 64")
        self.assertIn("addon undo dotnet48", result.stdout)
        self.assertFalse(any("gac" in argument or "NETFramework" in argument for call in self.wine_arguments() for argument in call))


from fsol import addons as fsol_addons, desktop as fsol_desktop  # noqa: E402
from tests.test_addons import RecordingTools  # noqa: E402
from tests.test_machine import FakeMachine, write as write_text  # noqa: E402

SETUP_NAME = "FenixInstaller-1.0.286-0763ce3.exe"


def recording_stub(path, record, after=""):
    write_text(path, f'#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "{record}"\nenv >> "{record}.env"\n{after}')
    path.chmod(0o755)


class InstallAndShortcutTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fake = FakeMachine(self.root).build()
        self.found = self.fake.discover()
        self.record = self.root / "record"
        recording_stub(self.fake.tool / "files/bin/wine", self.record)
        recording_stub(self.fake.tool / "files/bin/wineserver", Path(f"{self.record}.server"))
        self.setup = self.root / "downloads" / SETUP_NAME
        write_text(self.setup, "MZ")

    def install(self, **extra):
        env = {
            **os.environ,
            "HOME": str(self.root / "home"),
            "FSOL_PREFIX": str(self.found.prefix),
            "FSOL_PROTON": str(self.found.proton),
            "FSOL_CACHE": str(self.root / "cache"),
            "FSOL_CLI": str(self.root / "cli"),
            "FSOL_ADDON": "fenix-a320",
            **extra,
        }
        return subprocess.run([str(ADDON / "install")], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def recorded(self):
        return self.record.read_text().splitlines() if self.record.exists() else []

    def test_the_manifest_reports_the_versions_the_test_machine_has_and_where_to_get_the_installer(self):
        addon = fsol_addons.load(ADDON)
        self.assertEqual((addon.tested_version, addon.tested_on), ("3.0.0.7613", "2026-10-29"))
        self.assertEqual(addon.install, {"from": "https://fenixsim.com", "kind": "installer"})
        self.assertTrue(addon.can_install)

    def test_the_setup_runs_alone_in_the_prefix_and_the_script_waits_for_the_prefix_to_end(self):
        result = self.install(FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.fake.tool}/files/bin/wine {self.setup}"])
        self.assertTrue(Path(f"{self.record}.server").exists())
        self.assertIn(f"WINEPREFIX={self.found.prefix}", Path(f"{self.record}.env").read_text().splitlines())

    def test_the_setup_exit_code_is_the_script_exit_code(self):
        recording_stub(self.fake.tool / "files/bin/wine", self.record, "exit 4\n")
        self.assertEqual(self.install(FSOL_INSTALLER=str(self.setup)).returncode, 4)

    def test_without_a_setup_file_the_script_says_how_to_give_one_and_starts_nothing(self):
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("flightsim-on-linux addon install fenix-a320", result.stderr)
        self.assertEqual(self.recorded(), [])

    def test_the_variable_that_breaks_wine_installers_does_not_reach_the_setup(self):
        self.install(FSOL_INSTALLER=str(self.setup), NoDefaultCurrentDirectoryInExePath="1")
        names = {line.split("=", 1)[0] for line in Path(f"{self.record}.env").read_text().splitlines()}
        self.assertIn("WINEPREFIX", names)
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", names)

    def test_the_menu_entry_runs_the_existing_installer_script_and_passes_the_desktop_validator(self):
        addon = fsol_addons.load(ADDON)
        self.assertEqual([(entry["title"], entry["script"]) for entry in addon.shortcuts], [("Fenix Installer", "installer")])
        written = fsol_desktop.write_shortcuts(addon, self.found, ROOT / "bin" / "flightsim-on-linux", self.root / "data", RecordingTools(), lambda line: None)
        self.assertIn(f'Exec="{ROOT / "bin" / "flightsim-on-linux"}" addon launch fenix-a320 installer', written[0].read_text())
        if shutil.which("desktop-file-validate"):
            checked = subprocess.run(["desktop-file-validate", str(written[0])], capture_output=True, text=True)
            self.assertEqual((checked.returncode, checked.stdout + checked.stderr), (0, ""))

    def test_the_icon_comes_from_the_program_the_installer_script_starts(self):
        glob = fsol_addons.load(ADDON).shortcuts[0]["icon"]["glob"]
        script = (ADDON / "installer").read_text()
        self.assertIn('install_dir="$prefix/drive_c/users/steamuser/AppData/Local/FenixApp"', script)
        self.assertIn('app_dir="$install_dir/current"', script)
        self.assertIn('"$app_dir/FenixApp.exe"', script)
        self.assertEqual(glob, "drive_c/users/*/AppData/Local/FenixApp/current/FenixApp.exe")


class FenixPageTest(unittest.TestCase):
    def setUp(self):
        from tests.test_maddog_addon import page_problems

        self.problems = page_problems("fenix-a320")
        self.text = (ROOT / "site" / "pages" / "fenix-a320.html").read_text()

    def test_the_page_follows_the_template(self):
        self.assertEqual(self.problems, [])

    def test_the_page_reports_the_versions_of_the_test_machine(self):
        for needed in ("Fenix 3.0.0.7613", "Fenix Installer 1.0.359", "FenixInstaller-1.0.286-0763ce3.exe"):
            self.assertIn(needed, self.text)
        self.assertNotIn("3.0.0.7438 on 21 October", self.text)

    def test_the_install_section_covers_the_installer_the_updater_and_the_linked_packages(self):
        for needed in (
            "addon install fenix-a320",
            "addon launch fenix-a320 installer",
            "addon shortcut fenix-a320",
            "Velopack",
            "Update.exe apply",
            "Checking application pre-requisites",
            "1.0.353",
            "fnx-aircraft-320",
            "fnx-aircraft-319-321",
            "-name 'fnx-*' -type l",
            "windows-storage-applicationdata-no-package.patch",
        ):
            self.assertIn(needed, self.text)

    def test_the_page_names_no_personal_path(self):
        for word in ("/home/", "brunom", "Jogos", "FlightAddons"):
            self.assertNotIn(word, self.text)


if __name__ == "__main__":
    unittest.main()
