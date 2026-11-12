import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import zipfile
from base64 import b64encode
from datetime import date
from pathlib import Path
from unittest import mock

from fsol import addons, cli, desktop
from tests.support import process_stubs, stand_in_name, start_stand_in
from tests.test_addons import RecordingTools
from tests.test_machine import FakeMachine, write

ROOT = Path(__file__).resolve().parent.parent
ADDONS = ROOT / "addons"
PAGES = ROOT / "site" / "pages"
IDS = (
    "pmdg-oc3",
    "ifly-737-max8",
    "just-flight-rj-professional",
    "tfdi-md-11",
    "fss-e-jets",
    "fss-727",
    "aerosoft-a346-pro",
    "fsrealistic-plus",
)
PREFIX_USER = "drive_c/users/steamuser/AppData"
PAGE_SECTIONS = ("Get it", "Install it", "Make it run", "Start it", "Check it", "Known problems", "Undo")
BANNED = ("seamless", "robust", "leverage", "simply", "\u2014", "&mdash;")
OWNER_ONLY = ("/home/", "brunom", "Bruno", "Jogos", "SteamLibrary", "FlightAddons", "Community2024", "WAYLAND_DISPLAY", "XAUTHORITY", "^S:", "DISPLAY=")
PAGES_OF_OTHERS = {"contrail.html", "aerosoft-one.html", "navigraph-hub.html"}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")

EXPECTED = {
    "pmdg-oc3": {"version": "3.0.289", "on": "2026-10-13", "needs": ["simulator-closed"], "touches": False, "kind": "installer", "shortcuts": ["PMDG OC3"]},
    "ifly-737-max8": {"version": "1.1.0.3", "on": "2026-10-16", "needs": [], "touches": False, "kind": "installer", "shortcuts": ["iFly Manager"]},
    "just-flight-rj-professional": {"version": "0.1.6", "on": "2026-10-16", "needs": [], "touches": False, "kind": "installer", "shortcuts": []},
    "tfdi-md-11": {"version": "1.1.32", "on": "2026-10-13", "needs": ["simulator-closed"], "touches": True, "kind": "installer", "shortcuts": ["MD-11 Extended Simulation"]},
    "fss-e-jets": {"version": "1.0.2", "on": "2026-10-15", "needs": [], "touches": False, "kind": "store", "shortcuts": []},
    "fss-727": {"version": "1.1.7", "on": "2026-10-15", "needs": [], "touches": False, "kind": "store", "shortcuts": []},
    "aerosoft-a346-pro": {"version": "1.0.5", "on": "2026-10-15", "needs": [], "touches": False, "kind": "store", "shortcuts": []},
    "fsrealistic-plus": {"version": "1.1.9", "on": "2026-10-14", "needs": ["simulator-closed"], "touches": False, "kind": "installer", "shortcuts": []},
}
INSTALLED = {
    "pmdg-oc3": ("prefix", f"{PREFIX_USER}/Local/Programs/pmdg-oc3/PMDG OC3.exe"),
    "ifly-737-max8": ("community", "ifly-aircraft-737max8/manifest.json"),
    "just-flight-rj-professional": ("community", "justflight-aircraft-rj/manifest.json"),
    "tfdi-md-11": ("prefix", f"{PREFIX_USER}/Local/TFDi Design MD-11/installation.xml"),
    "fss-e-jets": ("community", "fss-aircraft-e19xfreighter/manifest.json"),
    "fss-727": ("community", "fss-aircraft-boeing-727-200re/manifest.json"),
    "aerosoft-a346-pro": ("community", "aerosoft-aircraft-a346-pro/manifest.json"),
    "fsrealistic-plus": ("community", "rkapps-fsrealistic/manifest.json"),
}

REGISTRY_WINE = r'''#!/usr/bin/env python3
import os
import sys
from pathlib import Path

record = os.environ["FSOL_TEST_RECORD"]
args = sys.argv[1:]
with open(record, "a") as file:
    file.write(" ".join([sys.argv[0]] + args) + "\n")
with open(record + ".env", "a") as file:
    file.write("".join(f"{name}={value}\n" for name, value in os.environ.items()))
if args[:1] == ["reg"] and args[1] in ("add", "delete"):
    registry = Path(os.environ["WINEPREFIX"]) / "user.reg"
    header = "[" + args[2].split("\\", 1)[1].replace("\\", "\\\\") + "]"
    name = args[args.index("/v") + 1]
    lines = registry.read_text().split("\n")
    start = next((index for index, line in enumerate(lines) if line.lower().startswith(header.lower())), None)
    if start is None:
        lines += [header + " 1", "#time=0", ""]
        start = len(lines) - 3
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("[")), len(lines))
    lines = [line for index, line in enumerate(lines) if not (start < index < end and line.startswith(f'"{name}"='))]
    if args[1] == "add":
        lines.insert(start + 2, f'"{name}"="{args[args.index("/d") + 1]}"')
    registry.write_text("\n".join(lines))
'''
CURL = r'''#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$0 $*" >> "$FSOL_TEST_RECORD"
out=""
url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
name="${url##*/}"
if [ -n "$out" ]; then cp "$FSOL_TEST_SERVER/$name" "$out"; else cat "$FSOL_TEST_SERVER/$name"; fi
'''


def recording(file, after=""):
    return f'#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "{file}"\nenv >> "{file}.env"\n{after}'


def stub(path, text):
    write(path, text)
    path.chmod(0o755)
    return path


def windows(path):
    return "Z:" + str(path).replace("/", "\\")


class AircraftCase(unittest.TestCase):
    ID = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake = FakeMachine(self.root).build()
        self.found = self.fake.discover()
        self.community = self.found.community
        self.proton = self.fake.tool
        self.record = self.root / "record"
        self.processes = []
        self.addon_folder = ADDONS / self.ID if self.ID else None
        write(self.found.prefix / "user.reg", "WINE REGISTRY Version 2\n;; All keys relative to REGISTRY\\\\User\\\\S-1-5-21-0-0-0-1000\n\n#arch=win64\n\n")
        stub(self.root / "bin" / "notify-send", recording(self.record))
        self.token = str(self.root)
        process_stubs(self.root / "bin", self.token)
        (self.root / "tmp").mkdir()

    def tearDown(self):
        for pid_file in (self.found.compatdata / "flightsim-on-linux-backup").glob("*/watch_*.pid"):
            with contextlib.suppress(ValueError, ProcessLookupError, PermissionError):
                os.kill(int(pid_file.read_text()), 9)
        for process in self.processes:
            process.kill()
            process.wait()
        self.temp.cleanup()

    def environment(self, **extra):
        env = {
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "TMPDIR": str(self.root / "tmp"),
            "XDG_DATA_HOME": str(self.root / "data"),
            "FSOL_PREFIX": str(self.found.prefix),
            "FSOL_COMPATDATA": str(self.found.compatdata),
            "FSOL_PROTON": str(self.found.proton),
            "FSOL_COMMUNITY": str(self.community),
            "FSOL_BASE": str(self.found.base),
            "FSOL_CACHE": str(self.root / "cache"),
            "FSOL_CLI": str(self.root / "cli"),
            "FSOL_ADDON": self.ID or "",
            "FSOL_SIM_RUNNING": "0",
            "FSOL_CLOSE_WAIT": "1",
            "FSOL_TEST_RECORD": str(self.record),
        }
        env.update(extra)
        return env

    def script(self, name, *args, **extra):
        return subprocess.run([str(self.addon_folder / name), *args], env=self.environment(**extra), capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def stubs(self, wine_exit=0, after="", registry=False):
        stub(self.root / "cli", recording(self.record, after))
        if registry:
            stub(self.proton / "files/bin/wine", REGISTRY_WINE)
        else:
            stub(self.proton / "files/bin/wine", recording(self.record, after) + f"exit {wine_exit}\n")
        stub(self.proton / "files/bin/wineserver", recording(f"{self.record}.server"))

    def recorded(self):
        return self.record.read_text().splitlines() if self.record.exists() else []

    def recorded_env(self):
        path = Path(f"{self.record}.env")
        return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line) if path.exists() else {}

    def wait_for(self, condition, seconds=5):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.05)
        return condition()

    def wait_for_record(self):
        self.wait_for(self.record.exists)

    def stand_in(self, argv0):
        process = start_stand_in(self.root, self.token, stand_in_name(argv0), 60)
        self.processes.append(process)
        time.sleep(0.2)
        return process

    def write_package(self, name, version="1.0.0", folder=None):
        target = (folder or self.community) / name
        write(target / "manifest.json", json.dumps({"package_version": version}))
        return target

    def assert_only_failure(self, result, word, lines=None):
        out = result.stdout.splitlines()
        self.assertEqual(result.returncode, 1, out)
        failures = [line for line in out if line.startswith("FAIL")]
        self.assertEqual(len(failures), 1, out)
        self.assertIn(word, failures[0])
        if lines is not None:
            self.assertEqual(len(out), lines, out)


class ShapeTest(unittest.TestCase):
    def test_every_manifest_loads_and_says_what_was_tested(self):
        for id_, want in EXPECTED.items():
            with self.subTest(id_):
                addon = addons.load(ADDONS / id_)
                self.assertEqual((addon.id, addon.tested_version, addon.tested_on, addon.needs, addon.touches_addon_files), (id_, want["version"], want["on"], want["needs"], want["touches"]))
                self.assertEqual([shortcut["title"] for shortcut in addon.shortcuts], want["shortcuts"])
                self.assertEqual((addon.install or {}).get("kind"), want["kind"])
                self.assertIsNone(addon.watcher)

    def test_a_vendor_whose_page_is_unknown_is_named_as_your_account_and_never_guessed(self):
        for id_, start in (("ifly-737-max8", "your vendor account"), ("tfdi-md-11", "your vendor account"), ("fsrealistic-plus", "your vendor account"), ("just-flight-rj-professional", "your Just Flight account")):
            with self.subTest(id_):
                source = addons.load(ADDONS / id_).install["from"]
                self.assertTrue(source.startswith(start), source)
                self.assertNotIn("http", source)

    def test_stores_have_no_install_script_so_the_command_prints_where_to_get_them(self):
        for id_ in ("fss-e-jets", "fss-727", "aerosoft-a346-pro"):
            with self.subTest(id_):
                self.assertFalse(addons.load(ADDONS / id_).can_install)

    def test_the_windows_installers_have_an_install_script(self):
        for id_ in ("pmdg-oc3", "ifly-737-max8", "just-flight-rj-professional", "tfdi-md-11", "fsrealistic-plus"):
            with self.subTest(id_):
                self.assertTrue(addons.load(ADDONS / id_).can_install)

    def test_nothing_in_the_folders_carries_the_owners_machine_or_a_comment(self):
        for id_ in IDS:
            for path in sorted(p for p in (ADDONS / id_).rglob("*") if p.is_file() and "__pycache__" not in p.parts):
                text = path.read_text()
                with self.subTest(str(path.relative_to(ROOT))):
                    for word in OWNER_ONLY:
                        self.assertNotIn(word, text)
                    comments = [line for line in text.splitlines() if line.lstrip().startswith("#") and not line.startswith("#!") and "shellcheck" not in line]
                    self.assertEqual(comments, [])

    def test_each_add_on_counts_as_installed_where_its_package_or_program_is(self):
        for id_, (place, relative) in INSTALLED.items():
            with self.subTest(id_), tempfile.TemporaryDirectory() as folder:
                addon = addons.load(ADDONS / id_)
                found = FakeMachine(Path(folder)).build().discover()
                self.assertFalse(addons.is_installed(addon, found))
                write(addons.place_folder(found, place) / relative, "x")
                self.assertTrue(addons.is_installed(addon, found))

    def test_the_menu_entries_pass_the_desktop_validator(self):
        for id_, want in EXPECTED.items():
            if not want["shortcuts"]:
                continue
            with self.subTest(id_), tempfile.TemporaryDirectory() as folder:
                addon = addons.load(ADDONS / id_)
                found = FakeMachine(Path(folder)).build().discover()
                written = desktop.write_shortcuts(addon, found, ROOT / "bin" / "flightsim-on-linux", Path(folder) / "data", RecordingTools(), lambda line: None)
                text = written[0].read_text()
                self.assertIn(f'addon launch {id_} {addon.shortcuts[0].get("script", "launch")}', text)
                if shutil.which("desktop-file-validate"):
                    result = subprocess.run(["desktop-file-validate", str(written[0])], capture_output=True, text=True)
                    self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""))

class StoreCliTest(unittest.TestCase):
    def run_cli(self, *args):
        with tempfile.TemporaryDirectory() as folder:
            addons_folder = Path(folder) / "addons"
            addons_folder.mkdir()
            for id_ in IDS:
                os.symlink(ADDONS / id_, addons_folder / id_)
            fake = FakeMachine(Path(folder)).build()
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"FSOL_ADDONS": str(addons_folder)}), mock.patch.object(cli.machine, "discover", return_value=fake.discover()):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = cli.main(list(args))
            return code, out.getvalue(), err.getvalue()

    def test_install_without_a_file_prints_the_store_and_runs_nothing(self):
        for id_, store in (("fss-e-jets", "the Contrail store"), ("fss-727", "the Aerosoft One store"), ("aerosoft-a346-pro", "the Aerosoft One store")):
            with self.subTest(id_):
                code, out, _ = self.run_cli("addon", "install", id_)
                self.assertEqual(code, 0)
                self.assertIn(f"get it from: {store}", out)
                self.assertIn("kind: store", out)
                self.assertNotIn("FAIL", out)

    def test_install_with_a_file_for_a_store_add_on_is_refused(self):
        code, _, err = self.run_cli("addon", "install", "fss-727", __file__)
        self.assertEqual(code, 2)
        self.assertIn("has no install script", err)

    def test_the_a340_performance_tool_is_gone_from_the_add_ons_and_the_site(self):
        self.assertFalse((ADDONS / "a340-performance-tool").exists())
        self.assertFalse((PAGES / "a340-performance-tool.html").exists())
        for page in (*PAGES.glob("*.html"), ROOT / "site" / "llms.txt"):
            text = page.read_text()
            self.assertNotIn("a340-performance-tool", text, page.name)
            self.assertNotIn("A340 Performance Tool", text, page.name)

    def test_the_windows_installers_name_the_vendor_account_and_ask_for_the_file(self):
        code, out, _ = self.run_cli("addon", "install", "ifly-737-max8")
        self.assertEqual(code, 0)
        self.assertIn("get it from: your vendor account", out)
        self.assertIn("then run: flightsim-on-linux addon install ifly-737-max8 <the file you downloaded>", out)


class PackageCheckTest(AircraftCase):
    PACKAGES = {
        "fss-e-jets": ("fss-aircraft-e19x", "fss-aircraft-e19xfreighter"),
        "fss-727": ("fss-aircraft-boeing-727-200f", "fss-aircraft-boeing-727-200re"),
        "aerosoft-a346-pro": ("aerosoft-aircraft-a346-pro",),
        "just-flight-rj-professional": ("justflight-aircraft-rj",),
    }

    def each(self):
        for id_, packages in self.PACKAGES.items():
            with self.subTest(id_):
                self.ID = id_
                self.addon_folder = ADDONS / id_
                shutil.rmtree(self.community)
                self.community.mkdir()
                yield id_, packages

    def test_a_package_in_community_passes_with_its_version(self):
        for id_, packages in self.each():
            self.write_package(packages[0], "2.3.4")
            result = self.script("check")
            self.assertEqual((result.returncode, result.stdout.strip()), (0, f"ok {packages[0]} 2.3.4 is in the Community folder"))

    def test_every_package_present_gets_its_own_ok_line(self):
        for id_, packages in self.each():
            for name in packages:
                self.write_package(name)
            lines = self.script("check").stdout.splitlines()
            self.assertEqual([line.split()[0] for line in lines], ["ok"] * len(packages))

    def test_nothing_in_community_fails_with_one_line(self):
        for id_, packages in self.each():
            self.assert_only_failure(self.script("check"), "none of", lines=1)

    def test_a_link_into_a_library_that_was_moved_fails(self):
        for id_, packages in self.each():
            os.symlink(self.root / "gone", self.community / packages[0])
            self.assert_only_failure(self.script("check"), "is a link to", lines=1)

    def test_a_package_without_a_readable_manifest_fails(self):
        for id_, packages in self.each():
            (self.community / packages[0]).mkdir()
            self.assert_only_failure(self.script("check"), "no readable manifest.json", lines=1)

    def test_a_link_that_resolves_is_a_package_like_any_other(self):
        for id_, packages in self.each():
            target = self.write_package(packages[0], "1.2.3", folder=self.root / "library")
            os.symlink(target, self.community / packages[0])
            self.assertEqual(self.script("check").returncode, 0)

    def test_a_dangling_link_fails_even_when_another_package_is_fine(self):
        for id_, packages in self.each():
            if len(packages) < 2:
                continue
            self.write_package(packages[0])
            os.symlink(self.root / "gone", self.community / packages[1])
            self.assert_only_failure(self.script("check"), "is a link to", lines=2)

    def test_without_a_community_folder_it_fails_instead_of_reading_the_root(self):
        for id_, packages in self.each():
            result = self.script("check", FSOL_COMMUNITY="")
            self.assertEqual((result.returncode, result.stdout.strip()), (1, "FAIL no Community folder found; run: flightsim-on-linux machine"))

    def test_apply_and_undo_say_there_is_nothing_to_do_and_touch_nothing(self):
        for id_, packages in self.each():
            self.stubs()
            for action in ("apply", "undo"):
                result = self.script(action)
                self.assertEqual((result.returncode, result.stdout.startswith("already nothing to")), (0, True))
            self.assertEqual(self.recorded(), [])


class PmdgTest(AircraftCase):
    ID = "pmdg-oc3"
    KEY = r"HKCU\Software\Wine\AppDefaults\PMDG OC3.exe\X11 Driver"
    EXE = "drive_c/users/steamuser/AppData/Local/Programs/pmdg-oc3/PMDG OC3.exe"
    PROGRAM = "C:\\users\\steamuser\\AppData\\Local\\Programs\\pmdg-oc3\\PMDG OC3.exe"

    def registry(self):
        return (self.found.prefix / "user.reg").read_text()

    def backup(self):
        return self.found.compatdata / "flightsim-on-linux-backup" / "pmdg-oc3"

    def test_without_the_key_the_check_fails_and_names_the_fix(self):
        result = self.script("check")
        self.assert_only_failure(result, "addon apply pmdg-oc3", lines=1)
        self.assertIn("Decorated for PMDG OC3.exe is None", result.stdout)

    def test_apply_sets_decorated_n_for_that_one_program_and_the_check_then_passes(self):
        self.stubs(registry=True)
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine reg add {self.KEY} /v Decorated /t REG_SZ /d N /f"])
        self.assertIn('[Software\\\\Wine\\\\AppDefaults\\\\PMDG OC3.exe\\\\X11 Driver]', self.registry())
        self.assertIn('"Decorated"="N"', self.registry())
        check = self.script("check")
        self.assertEqual((check.returncode, check.stdout.split()[0]), (0, "ok"))

    def test_apply_waits_for_the_prefix_to_save_its_registry(self):
        self.stubs(registry=True)
        self.script("apply")
        self.assertTrue(Path(f"{self.record}.server").read_text().startswith(f"{self.proton}/files/bin/wineserver -w"))

    def test_a_second_apply_changes_nothing_and_does_not_start_wine(self):
        self.stubs(registry=True)
        self.script("apply")
        before = self.recorded()
        result = self.script("apply")
        self.assertEqual((result.returncode, result.stdout.startswith("already")), (0, True))
        self.assertEqual(self.recorded(), before)

    def test_undo_takes_the_value_out_again_and_the_check_fails_again(self):
        self.stubs(registry=True)
        self.script("apply")
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Decorated", self.registry())
        self.assertEqual(self.script("check").returncode, 1)
        self.assertFalse((self.backup() / "decorated.absent").exists())

    def test_a_value_that_was_there_before_comes_back_on_undo(self):
        self.stubs(registry=True)
        write(self.found.prefix / "user.reg", self.registry() + '[Software\\\\Wine\\\\AppDefaults\\\\PMDG OC3.exe\\\\X11 Driver] 1\n#time=0\n"Decorated"="Y"\n\n')
        self.script("apply")
        self.assertIn('"Decorated"="N"', self.registry())
        self.assertEqual((self.backup() / "decorated").read_text(), "Y")
        self.script("undo")
        self.assertIn('"Decorated"="Y"', self.registry())
        self.assertFalse((self.backup() / "decorated").exists())

    def test_a_value_you_set_yourself_is_left_alone_by_undo(self):
        self.stubs(registry=True)
        write(self.found.prefix / "user.reg", self.registry() + '[Software\\\\Wine\\\\AppDefaults\\\\PMDG OC3.exe\\\\X11 Driver] 1\n#time=0\n"Decorated"="N"\n\n')
        self.script("apply")
        result = self.script("undo")
        self.assertEqual((result.returncode, result.stdout.startswith("already")), (0, True))
        self.assertIn('"Decorated"="N"', self.registry())
        self.assertEqual(self.recorded(), [])

    def test_install_runs_the_installer_alone_with_powershell_off_and_the_trap_variable_gone(self):
        self.stubs()
        setup = self.root / "oc3_setup_3.0.289.exe"
        write(setup, "MZ")
        result = self.script("install", FSOL_INSTALLER=str(setup), NoDefaultCurrentDirectoryInExePath="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {setup}"])
        env = self.recorded_env()
        self.assertEqual(env.get("WINEDLLOVERRIDES"), "powershell.exe=d")
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", env)

    def test_the_installer_exit_code_is_the_script_exit_code(self):
        self.stubs(wine_exit=3)
        self.assertEqual(self.script("install", FSOL_INSTALLER="/tmp/x.exe").returncode, 3)

    def test_install_without_a_file_points_at_the_update_command(self):
        result = self.script("install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("addon launch pmdg-oc3 update", result.stderr)

    def test_launch_opens_oc3_with_both_electron_options(self):
        self.stubs()
        write(self.found.prefix / self.EXE, "MZ")
        result = self.script("launch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {self.PROGRAM} --disable-gpu-sandbox --disable-gpu"])

    def test_launch_inside_a_running_simulator_goes_through_the_container(self):
        self.stubs()
        write(self.found.prefix / self.EXE, "MZ")
        self.script("launch", FSOL_SIM_RUNNING="1")
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.root}/cli run {self.PROGRAM} --disable-gpu-sandbox --disable-gpu"])

    def test_a_copy_already_running_is_left_alone(self):
        self.stubs()
        write(self.found.prefix / self.EXE, "MZ")
        self.stand_in(self.PROGRAM)
        result = self.script("launch")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(any("it is already running" in line for line in self.recorded()))
        time.sleep(0.5)
        self.assertFalse(any("wine" in line for line in self.recorded()))

    def test_launch_without_oc3_says_how_to_get_it(self):
        self.stubs()
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon launch pmdg-oc3 update", result.stderr)


class PmdgUpdateTest(AircraftCase):
    ID = "pmdg-oc3"

    def setUp(self):
        super().setUp()
        self.stubs()
        self.server = self.root / "server"
        self.data = b"setup bytes"
        self.publish("oc3_setup_3.0.290.exe", self.data)
        stub(self.root / "bin" / "curl", CURL)

    def publish(self, name, data, sha=None, manifest=None):
        write(self.server / name, "")
        (self.server / name).write_bytes(data)
        sha = sha or b64encode(hashlib.sha512(data).digest()).decode()
        text = manifest or f"version: 3.0.290\nfiles:\n  - url: {name}\n    sha512: {sha}\n    size: {len(data)}\npath: {name}\nsha512: {sha}\nreleaseDate: '2026-10-30T00:00:00.000Z'\n"
        (self.server / "prod.yml").write_text(text)

    def update(self, *args):
        return self.script("update", *args, FSOL_TEST_SERVER=str(self.server))

    def installer(self):
        return self.root / "cache" / "downloads" / "oc3_setup_3.0.290.exe"

    def test_it_downloads_checks_the_hash_and_hands_the_file_to_the_install_command(self):
        result = self.update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("version published by PMDG: 3.0.290", result.stdout)
        self.assertIn("sha512 matches PMDG's manifest", result.stdout)
        self.assertEqual(self.installer().read_bytes(), self.data)
        self.assertEqual(self.recorded()[-1], f"{self.root}/cli addon install pmdg-oc3 {self.installer()}")

    def test_a_file_that_does_not_match_the_manifest_is_deleted_and_never_installed(self):
        self.publish("oc3_setup_3.0.290.exe", b"other bytes", sha=b64encode(hashlib.sha512(self.data).digest()).decode())
        result = self.update()
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not match PMDG's manifest", result.stderr)
        self.assertFalse(self.installer().exists())
        self.assertFalse(any("addon install" in line for line in self.recorded()))

    def test_download_only_prints_the_path_and_installs_nothing(self):
        result = self.update("--download-only")
        self.assertEqual((result.returncode, result.stdout.splitlines()[-1]), (0, str(self.installer())))
        self.assertFalse(any("addon install" in line for line in self.recorded()))

    def test_a_file_already_downloaded_is_checked_again_and_not_fetched_twice(self):
        self.update("--download-only")
        self.update("--download-only")
        downloads = [line for line in self.recorded() if "oc3_setup" in line and " -o " in line]
        self.assertEqual(len(downloads), 1)

    def test_a_manifest_in_another_shape_is_refused_before_anything_is_downloaded(self):
        for text in ("nothing useful\n", "path: ../evil.exe\nsha512: abc\n", "path: oc3_setup_1.exe\n"):
            with self.subTest(text):
                self.publish("oc3_setup_3.0.290.exe", self.data, manifest=text)
                result = self.update()
                self.assertEqual(result.returncode, 1)
                self.assertIn("not in the shape", result.stderr)
        self.assertFalse(self.installer().exists())


class IflyTest(AircraftCase):
    ID = "ifly-737-max8"
    PLUGIN = "ifly-aircraft-737max8/Data/Tool/737MAX_Plugin.exe"
    MANAGER = "drive_c/ProgramData/iFlyManager/iFlyManager.exe"
    PROGRAM = "C:\\ProgramData\\iFlyManager\\iFlyManager.exe"

    def exe_xml(self, disabled="false", path=None, name="iFly Plugin"):
        path = path or windows(self.community / self.PLUGIN)
        entry = f"    <Launch.Addon>\n        <Name>{name}</Name>\n        <Disabled>{disabled}</Disabled>\n        <Path>{path}</Path>\n    </Launch.Addon>\n"
        write(self.found.base / "exe.xml", f'<?xml version="1.0" encoding="windows-1252"?>\n<SimBase.Document Type="Launch" version="1,0">\n{entry}</SimBase.Document>\n')

    def whole(self):
        self.write_package("ifly-aircraft-737max8", "1.1.0.3")
        write(self.community / self.PLUGIN, "MZ")
        self.exe_xml()

    def test_a_machine_with_the_aircraft_and_the_plugin_entry_passes_with_two_ok_lines(self):
        self.whole()
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.splitlines(), ["ok the iFly 737 MAX8 1.1.0.3 is in the Community folder", "ok exe.xml starts the iFly plugin with the simulator"])

    def test_the_entry_is_found_in_the_prefix_whatever_the_case_the_manager_wrote(self):
        self.whole()
        write(self.found.prefix / "drive_c/Games/ifly-aircraft-737max8/Data/Tool/737MAX_Plugin.exe", "MZ")
        self.exe_xml(path="C:\\GAMES\\IFLY-AIRCRAFT-737MAX8\\DATA\\TOOL\\737MAX_PLUGIN.EXE")
        self.assertEqual(self.script("check").returncode, 0)

    def test_the_entry_may_use_the_z_drive_with_the_case_of_the_files_on_disk(self):
        self.whole()
        self.exe_xml(path=windows(self.community / self.PLUGIN).upper())
        self.assertEqual(self.script("check").returncode, 0)

    def test_no_entry_in_exe_xml_fails_and_says_who_writes_it(self):
        self.whole()
        self.exe_xml(path="C:\\Other\\other.exe")
        result = self.script("check")
        self.assert_only_failure(result, "no entry for the iFly plugin", lines=2)
        self.assertIn("install the aircraft with the iFly Manager, which writes the entry", result.stdout)

    def test_an_entry_with_disabled_true_fails(self):
        self.whole()
        self.exe_xml(disabled="True")
        self.assert_only_failure(self.script("check"), "Disabled true", lines=2)

    def test_an_entry_that_points_at_a_file_that_is_gone_fails(self):
        self.whole()
        (self.community / self.PLUGIN).unlink()
        self.assert_only_failure(self.script("check"), "does not exist", lines=2)

    def test_no_exe_xml_fails(self):
        self.whole()
        (self.found.base / "exe.xml").unlink()
        self.assert_only_failure(self.script("check"), "exe.xml does not exist", lines=2)

    def test_a_missing_aircraft_fails_on_its_own_line(self):
        self.whole()
        (self.community / "ifly-aircraft-737max8" / "manifest.json").unlink()
        self.assert_only_failure(self.script("check"), "no readable manifest.json", lines=2)

    def test_a_commented_out_entry_does_not_count(self):
        self.whole()
        text = (self.found.base / "exe.xml").read_text().replace("<Launch.Addon>", "<!--<Launch.Addon>").replace("</Launch.Addon>", "</Launch.Addon>-->")
        write(self.found.base / "exe.xml", text)
        self.assert_only_failure(self.script("check"), "no entry for the iFly plugin", lines=2)

    def test_install_gives_the_setup_to_the_prefix_alone_without_the_trap_variable(self):
        self.stubs()
        setup = self.root / "iFlyManagerSetup_v107.exe"
        write(setup, "MZ")
        result = self.script("install", FSOL_INSTALLER=str(setup), NoDefaultCurrentDirectoryInExePath="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {setup}"])
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", self.recorded_env())
        self.assertNotIn("WINEDLLOVERRIDES", self.recorded_env())

    def test_launch_opens_the_manager_in_the_prefix_or_in_the_container(self):
        self.stubs()
        write(self.found.prefix / self.MANAGER, "MZ")
        self.script("launch")
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {self.PROGRAM}"])
        self.record.unlink()
        self.script("launch", FSOL_SIM_RUNNING="1")
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.root}/cli run {self.PROGRAM}"])

    def test_launch_without_the_manager_says_how_to_install_it(self):
        self.stubs()
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install ifly-737-max8", result.stderr)

    def test_a_manager_already_open_is_left_alone(self):
        self.stubs()
        write(self.found.prefix / self.MANAGER, "MZ")
        self.stand_in(self.PROGRAM)
        self.assertEqual(self.script("launch").returncode, 0)
        self.assertTrue(any("it is already running" in line for line in self.recorded()))
        time.sleep(0.5)
        self.assertFalse(any("wine" in line for line in self.recorded()))

    def test_apply_and_undo_have_nothing_to_do(self):
        self.stubs()
        for action in ("apply", "undo"):
            result = self.script(action)
            self.assertEqual((result.returncode, result.stdout.startswith("already nothing to")), (0, True))
        self.assertEqual(self.recorded(), [])


class RjTest(AircraftCase):
    ID = "just-flight-rj-professional"

    def zip_with(self, *names):
        archive = self.root / "Install_RJ_Professional_MSFS2024_0.1.6.zip"
        with zipfile.ZipFile(archive, "w") as file:
            for name in names:
                file.writestr(name, "MZ")
        return archive

    def test_the_setup_exe_is_run_alone_in_the_prefix(self):
        self.stubs()
        setup = self.root / "setup.exe"
        write(setup, "MZ")
        result = self.script("install", FSOL_INSTALLER=str(setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {setup}"])

    def test_the_variable_that_breaks_the_activation_never_reaches_the_installer(self):
        self.stubs()
        setup = self.root / "setup.exe"
        write(setup, "MZ")
        self.script("install", FSOL_INSTALLER=str(setup), NoDefaultCurrentDirectoryInExePath="1")
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", self.recorded_env())

    def test_a_zip_is_unpacked_to_a_private_folder_its_setup_runs_and_the_folder_goes_away(self):
        self.stubs()
        result = self.script("install", FSOL_INSTALLER=str(self.zip_with("Disk1/setup.exe", "Disk1/data1.cab")))
        self.assertEqual(result.returncode, 0, result.stderr)
        line = self.recorded()[0]
        self.assertRegex(line, rf"^{re.escape(str(self.proton))}/files/bin/wine {re.escape(str(self.root / 'tmp'))}/fsol-rj\.[A-Za-z0-9]+/Disk1/setup\.exe$")
        self.assertEqual(list((self.root / "tmp").iterdir()), [])

    def test_a_zip_without_a_setup_is_refused_and_leaves_nothing_behind(self):
        self.stubs()
        result = self.script("install", FSOL_INSTALLER=str(self.zip_with("Disk1/readme.txt")))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no setup.exe inside", result.stderr)
        self.assertEqual((self.recorded(), list((self.root / "tmp").iterdir())), ([], []))

    def test_the_installer_exit_code_is_the_script_exit_code_and_the_folder_still_goes_away(self):
        self.stubs(wine_exit=5)
        result = self.script("install", FSOL_INSTALLER=str(self.zip_with("setup.exe")))
        self.assertEqual(result.returncode, 5)
        self.assertEqual(list((self.root / "tmp").iterdir()), [])

    def test_without_an_installer_the_script_says_how_to_give_one(self):
        result = self.script("install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("addon install just-flight-rj-professional", result.stderr)


class FsrealisticTest(AircraftCase):
    ID = "fsrealistic-plus"
    SERVICE = "rkapps-fsrealistic/service/FSRealistic-Plus.exe"

    def setUp(self):
        super().setUp()
        self.exe_xml_path = self.found.base / "exe.xml"
        self.backup = self.found.compatdata / "flightsim-on-linux-backup" / "fsrealistic-plus"

    def install_program(self):
        write(self.community / self.SERVICE, "MZ")

    def entry(self, disabled="false", path=None):
        path = path or windows(self.community / self.SERVICE)
        return f"    <Launch.Addon>\n        <Name>FSRealistic+</Name>\n        <Disabled>{disabled}</Disabled>\n        <Path>{path}</Path>\n    </Launch.Addon>\n"

    def write_exe_xml(self, body=""):
        write(self.exe_xml_path, f'<?xml version="1.0" encoding="windows-1252"?>\n<SimBase.Document Type="Launch" version="1,0">\n    <Filename>exe.xml</Filename>\n{body}</SimBase.Document>\n')

    def test_without_an_entry_the_check_fails_and_after_apply_it_passes(self):
        self.install_program()
        self.write_exe_xml()
        failed = self.script("check")
        self.assert_only_failure(failed, "no entry for FSRealistic+", lines=1)
        self.assertIn("run: flightsim-on-linux addon apply fsrealistic-plus", failed.stdout)
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("changed exe.xml: added", result.stdout)
        text = self.exe_xml_path.read_text()
        self.assertIn(f"<Path>{windows(self.community / self.SERVICE)}</Path>", text)
        self.assertIn("<Name>FSRealistic+</Name>", text)
        self.assertEqual(self.script("check").stdout.strip(), "ok exe.xml starts FSRealistic+ with the simulator")

    def test_apply_keeps_every_other_entry_and_backs_the_file_up(self):
        self.install_program()
        self.write_exe_xml(self.entry(path="C:\\Other\\other.exe").replace("FSRealistic+", "Other"))
        before = self.exe_xml_path.read_text()
        self.script("apply")
        self.assertIn("<Name>Other</Name>", self.exe_xml_path.read_text())
        self.assertEqual((self.backup / "exe.xml").read_text(), before)

    def test_apply_creates_exe_xml_when_there_is_none_and_undo_deletes_it_again(self):
        self.install_program()
        self.script("apply")
        self.assertTrue(self.exe_xml_path.is_file())
        self.assertEqual(self.script("check").returncode, 0)
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.exe_xml_path.exists())

    def test_undo_takes_out_only_the_entry_apply_added(self):
        self.install_program()
        self.write_exe_xml(self.entry(path="C:\\Other\\other.exe").replace("FSRealistic+", "Other"))
        before = self.exe_xml_path.read_text()
        self.script("apply")
        self.script("undo")
        self.assertEqual(self.exe_xml_path.read_text(), before)
        self.assertEqual(self.script("check").returncode, 1)

    def test_an_entry_the_user_wrote_is_left_alone_by_apply_and_by_undo(self):
        self.install_program()
        self.write_exe_xml(self.entry())
        before = self.exe_xml_path.read_text()
        for action in ("apply", "undo"):
            result = self.script(action)
            self.assertEqual((result.returncode, result.stdout.startswith("already")), (0, True))
        self.assertEqual(self.exe_xml_path.read_text(), before)

    def test_an_entry_written_through_the_packages_link_counts_as_the_same_program(self):
        self.install_program()
        os.symlink(self.fake.packages, self.found.base / "Packages")
        through_link = "C:\\users\\steamuser\\AppData\\Roaming\\Microsoft Flight Simulator 2024\\Packages\\Community\\" + self.SERVICE.replace("/", "\\")
        self.write_exe_xml(self.entry(path=through_link))
        self.assertEqual(self.script("check").stdout.strip(), "ok exe.xml starts FSRealistic+ with the simulator")
        self.assertTrue(self.script("apply").stdout.startswith("already"))

    def test_a_disabled_entry_is_reported_and_left_as_it_is(self):
        self.install_program()
        self.write_exe_xml(self.entry(disabled="True"))
        before = self.exe_xml_path.read_text()
        result = self.script("apply")
        self.assertIn("left exe.xml as it is", result.stdout)
        self.assertEqual(self.exe_xml_path.read_text(), before)
        self.assert_only_failure(self.script("check"), "Disabled true", lines=1)

    def test_an_entry_that_points_at_a_program_that_is_gone_fails(self):
        self.write_exe_xml(self.entry())
        self.assert_only_failure(self.script("check"), "does not exist", lines=1)

    def test_apply_refuses_to_write_an_entry_for_a_program_that_is_not_installed(self):
        self.write_exe_xml()
        before = self.exe_xml_path.read_text()
        result = self.script("apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn("install FSRealistic+ first", result.stderr)
        self.assertEqual(self.exe_xml_path.read_text(), before)

    def test_install_runs_the_installer_alone(self):
        self.stubs()
        setup = self.root / "FSRealistic_Plus_v1.1.9.exe"
        write(setup, "MZ")
        result = self.script("install", FSOL_INSTALLER=str(setup), NoDefaultCurrentDirectoryInExePath="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {setup}"])
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", self.recorded_env())


NAVDATA_ZIP = "TFDi_Design_MD11.NavData_FS24.1.1.22.0.zip"
NAVDATA_FILES = {
    "tfdidesign-aircraft-md11/Data/Primary/Airports/KJFK.json": "{}",
    "tfdidesign-aircraft-md11/Data/Primary/Terminals.json": "[1,2,3]",
    "tfdidesign-aircraft-md11/Data/Primary/ProcedureLegs/TermID_1.json": "[]",
}
INSTALLATION = """<Installations>
  <ActivationCode>TEST-ONLY-NOT-A-REAL-CODE</ActivationCode>
  <Installation InstallDir="{install}" Platform="FS24">
    <Package Name="Core_FS24" Version="1.1.32.0" />
    <Package Name="NavData_FS24" Version="1.1.20.0" />
  </Installation>
  <Installation InstallDir="TFDi Design\\MD-11" Platform="Common">
    <Package Name="Docs" Version="1.1.24.0" Target="@Documents" />
  </Installation>
</Installations>"""


def load_navdata():
    spec = importlib.util.spec_from_file_location("tfdi_navdata_parts", ADDONS / "tfdi-md-11" / "parts" / "navdata.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TfdiTest(AircraftCase):
    ID = "tfdi-md-11"
    KEPT = [{"path": "manifest.json", "size": 10, "date": 1}, {"path": "systems/core.wasm", "size": 20, "date": 2}]
    STALE = [{"path": "data/primary/old.json", "size": 5, "date": 3}]

    def setUp(self):
        super().setUp()
        local = self.found.prefix / PREFIX_USER / "Local"
        self.local_temp = local / "Temp"
        self.state = local / "TFDi Design MD-11"
        self.package = self.community / "tfdidesign-aircraft-md11"
        self.backup_root = self.found.compatdata / "flightsim-on-linux-backup" / "tfdi-md-11"
        self.write_package("tfdidesign-aircraft-md11", "1.1.32")
        self.layout(self.KEPT + self.STALE)
        self.installation(INSTALLATION.format(install=windows(self.community)))
        write(self.state / "Cache" / "Core_FS24.cache", "core")
        self.local_temp.mkdir(parents=True)
        self.navdata_zip(self.local_temp / NAVDATA_ZIP)

    def layout(self, content):
        write(self.package / "layout.json", json.dumps({"content": content}, indent=2))

    def installation(self, text):
        write(self.state / "installation.xml", text)

    def navdata_zip(self, path, files=NAVDATA_FILES):
        with zipfile.ZipFile(path, "w") as file:
            for name, text in files.items():
                info = zipfile.ZipInfo(name, (2026, 10, 1, 12, 0, 0))
                file.writestr(info, text)

    def content(self):
        return json.loads((self.package / "layout.json").read_text())["content"]

    def primary(self):
        return [entry for entry in self.content() if entry["path"].startswith("data/primary/")]

    def fix(self, **extra):
        return self.script("fix-navdata", **extra)

    def test_filetime_counts_hundred_nanoseconds_since_1601(self):
        navdata = load_navdata()
        self.assertEqual((navdata.filetime(0), navdata.filetime(1_700_000_000)), (116444736000000000, 133444736000000000))

    def test_before_the_fix_a_layout_without_the_files_on_disk_fails_the_check(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(result.stdout.splitlines()[:2], ["ok installation.xml lists NavData_FS24 1.1.20.0", f"ok the MD-11 package is in {self.community}"])
        self.assertIn("FAIL the package has no Data/Primary files on disk", result.stdout)

    def test_the_fix_unpacks_lists_every_file_and_the_check_then_passes(self):
        result = self.fix()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        on_disk = sorted(path.relative_to(self.package).as_posix().lower() for path in (self.package / "Data" / "Primary").rglob("*") if path.is_file())
        self.assertEqual(on_disk, ["data/primary/airports/kjfk.json", "data/primary/procedurelegs/termid_1.json", "data/primary/terminals.json"])
        self.assertEqual([entry["path"] for entry in self.primary()], on_disk)
        self.assertEqual([entry for entry in self.content() if not entry["path"].startswith("data/primary/")], self.KEPT)
        self.assertEqual({entry["path"]: entry["size"] for entry in self.primary()}["data/primary/terminals.json"], len("[1,2,3]"))
        check = self.script("check")
        self.assertEqual((check.returncode, check.stdout.splitlines()[-1]), (0, "ok layout.json lists the 3 NavData files that are on disk"))

    def test_the_layout_entries_carry_the_real_size_and_the_windows_file_time_of_each_file(self):
        navdata = load_navdata()
        self.fix()
        files = {path.relative_to(self.package).as_posix().lower(): path for path in (self.package / "Data" / "Primary").rglob("*") if path.is_file()}
        for entry in self.primary():
            real = files[entry["path"]].stat()
            self.assertEqual((entry["size"], entry["date"]), (real.st_size, navdata.filetime(real.st_mtime)))

    def test_a_layout_that_lacks_a_file_on_disk_fails_the_check_with_both_counts(self):
        self.fix()
        content = [entry for entry in self.content() if entry["path"] != "data/primary/terminals.json"]
        self.layout(content)
        self.assert_only_failure(self.script("check"), "lists 2 NavData files and the disk holds 3", lines=3)

    def test_a_layout_that_lists_more_than_the_disk_holds_passes_and_says_how_many(self):
        self.fix()
        sorted(path for path in (self.package / "Data" / "Primary").rglob("*") if path.is_file())[0].unlink()
        check = self.script("check")
        self.assertEqual((check.returncode, check.stdout.splitlines()[-1]), (0, "ok layout.json lists the 2 NavData files that are on disk, and 1 more that are not"))

    def test_the_version_in_installation_xml_follows_the_zip_and_nothing_else_changes(self):
        before = (self.state / "installation.xml").read_text()
        self.fix()
        after = (self.state / "installation.xml").read_text()
        self.assertEqual(after, before.replace("1.1.20.0", "1.1.22.0"))

    def test_a_missing_navdata_entry_is_added_to_the_fs24_block_and_not_to_the_common_one(self):
        self.installation(INSTALLATION.format(install=windows(self.community)).replace('    <Package Name="NavData_FS24" Version="1.1.20.0" />\n', ""))
        self.fix()
        text = (self.state / "installation.xml").read_text()
        self.assertIn('<Package Name="NavData_FS24" Version="1.1.22.0" />', text)
        self.assertLess(text.index("NavData_FS24"), text.index('Platform="Common"'))
        self.assertEqual(self.script("check").returncode, 0)

    def test_crlf_line_endings_of_installation_xml_are_kept(self):
        text = INSTALLATION.format(install=windows(self.community)).replace('    <Package Name="NavData_FS24" Version="1.1.20.0" />\n', "").replace("\n", "\r\n")
        self.installation(text)
        self.fix()
        after = (self.state / "installation.xml").read_bytes().decode()
        self.assertEqual(after.count("\n"), after.count("\r\n"))

    def test_the_cache_file_lists_every_folder_and_file_with_windows_separators(self):
        self.fix()
        lines = (self.state / "Cache" / "NavData_FS24.cache").read_bytes().decode().split("\r\n")
        self.assertEqual(lines[0], "tfdidesign-aircraft-md11\\Data\\Primary\\")
        self.assertIn("tfdidesign-aircraft-md11\\Data\\Primary\\Airports\\", lines)
        self.assertIn("tfdidesign-aircraft-md11\\Data\\Primary\\ProcedureLegs\\TermID_1.json", lines)
        self.assertEqual(lines[-1], "")

    def test_the_package_is_found_where_installation_xml_says_even_when_community_is_elsewhere(self):
        elsewhere = self.root / "other" / "Community"
        elsewhere.mkdir(parents=True)
        result = self.script("check", FSOL_COMMUNITY=str(elsewhere))
        self.assertIn(f"ok the MD-11 package is in {self.community}", result.stdout)

    def test_the_community_folder_is_the_fallback_when_installation_xml_names_a_path_that_does_not_map(self):
        self.installation(INSTALLATION.format(install="Q:\\nowhere"))
        result = self.script("check")
        self.assertIn(f"ok the MD-11 package is in {self.community}", result.stdout)

    def test_the_activation_code_never_appears_in_any_output(self):
        outputs = [self.script("check"), self.fix(), self.script("check"), self.script("undo")]
        for result in outputs:
            self.assertNotIn("TEST-ONLY-NOT-A-REAL-CODE", result.stdout + result.stderr)

    def test_the_three_files_are_copied_aside_before_anything_changes(self):
        files = {name: (path.read_bytes() if path.exists() else None) for name, path in (("layout.json", self.package / "layout.json"), ("installation.xml", self.state / "installation.xml"))}
        self.fix()
        folder = next(self.backup_root.iterdir())
        self.assertEqual((folder / "layout.json").read_bytes(), files["layout.json"])
        self.assertEqual((folder / "installation.xml").read_bytes(), files["installation.xml"])
        self.assertTrue((folder / "NavData_FS24.cache.absent").exists())

    def test_undo_puts_the_three_files_back_and_leaves_the_unpacked_data(self):
        originals = ((self.package / "layout.json").read_bytes(), (self.state / "installation.xml").read_bytes())
        self.fix()
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(((self.package / "layout.json").read_bytes(), (self.state / "installation.xml").read_bytes()), originals)
        self.assertFalse((self.state / "Cache" / "NavData_FS24.cache").exists())
        self.assertTrue((self.package / "Data" / "Primary" / "Terminals.json").is_file())
        self.assertEqual(self.script("check").returncode, 1)

    def test_undo_twice_is_the_same_as_once(self):
        self.fix()
        self.script("undo")
        first = (self.package / "layout.json").read_bytes()
        self.assertEqual(self.script("undo").returncode, 0)
        self.assertEqual((self.package / "layout.json").read_bytes(), first)

    def test_undo_restores_a_cache_file_that_existed(self):
        write(self.state / "Cache" / "NavData_FS24.cache", "old cache")
        self.fix()
        self.script("undo")
        self.assertEqual((self.state / "Cache" / "NavData_FS24.cache").read_text(), "old cache")

    def test_undo_before_any_fix_has_nothing_to_restore(self):
        result = self.script("undo")
        self.assertEqual((result.returncode, result.stdout.startswith("already nothing to undo")), (0, True))

    def test_the_newest_zip_is_the_one_used(self):
        newer = self.local_temp / "TFDi_Design_MD11.NavData_FS24.1.1.23.0.zip"
        self.navdata_zip(newer)
        os.utime(newer, (time.time() + 100, time.time() + 100))
        self.fix()
        self.assertIn('Version="1.1.23.0"', (self.state / "installation.xml").read_text())

    def test_a_running_simulator_stops_the_fix_before_it_touches_anything(self):
        before = (self.package / "layout.json").read_bytes()
        result = self.fix(FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("close the simulator first", result.stderr)
        self.assertEqual(((self.package / "layout.json").read_bytes(), self.backup_root.exists()), (before, False))

    def test_an_open_install_manager_stops_the_fix(self):
        self.stand_in("C:\\users\\steamuser\\AppData\\Local\\TFDi Design MD-11\\MD11InstallManager.exe")
        result = self.fix()
        self.assertEqual(result.returncode, 1)
        self.assertIn("close the MD-11 Install Manager first", result.stderr)

    def test_no_zip_in_temp_says_to_let_the_manager_download_first(self):
        (self.local_temp / NAVDATA_ZIP).unlink()
        result = self.fix()
        self.assertEqual(result.returncode, 2)
        self.assertIn("let it download the NavData", result.stderr)

    def test_a_damaged_zip_is_refused_before_any_file_changes(self):
        (self.local_temp / NAVDATA_ZIP).write_bytes(b"PK\x03\x04 not a zip")
        before = (self.package / "layout.json").read_bytes()
        result = self.fix()
        self.assertEqual(result.returncode, 2)
        self.assertIn("is damaged", result.stderr)
        self.assertEqual(((self.package / "layout.json").read_bytes(), self.backup_root.exists()), (before, False))

    def test_a_zip_without_the_primary_folder_is_refused(self):
        self.navdata_zip(self.local_temp / NAVDATA_ZIP, {"tfdidesign-aircraft-md11/readme.txt": "x"})
        result = self.fix()
        self.assertEqual(result.returncode, 2)
        self.assertIn("did not hold tfdidesign-aircraft-md11/Data/Primary", result.stderr)

    def test_without_the_install_managers_state_the_fix_says_to_install_first(self):
        (self.state / "installation.xml").unlink()
        result = self.fix()
        self.assertEqual(result.returncode, 2)
        self.assertIn("install the MD-11 with its Install Manager first", result.stderr)
        self.assert_only_failure(self.script("check"), "install the MD-11", lines=1)

    def test_a_package_missing_from_every_folder_is_named(self):
        shutil.rmtree(self.package)
        result = self.script("check")
        self.assertIn("FAIL the MD-11 package is not in", result.stdout)

    def test_install_runs_the_manager_alone_and_launch_opens_the_extended_package(self):
        self.stubs()
        setup = self.root / "MD11InstallManager.exe"
        write(setup, "MZ")
        self.assertEqual(self.script("install", FSOL_INSTALLER=str(setup), NoDefaultCurrentDirectoryInExePath="1").returncode, 0)
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {setup}"])
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", self.recorded_env())
        self.record.unlink()
        program = self.found.prefix / PREFIX_USER / "Local/TFDi Design/MD-11 Extended Simulation/MD-11 Extended Simulation Package.exe"
        write(program, "MZ")
        self.script("launch", FSOL_SIM_RUNNING="1")
        self.wait_for_record()
        windows_path = "C:\\users\\steamuser\\AppData\\Local\\TFDi Design\\MD-11 Extended Simulation\\MD-11 Extended Simulation Package.exe"
        self.assertEqual(self.recorded(), [f"{self.root}/cli run {windows_path}"])

    def test_launch_without_the_package_says_to_install_the_aircraft(self):
        self.stubs()
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("install the MD-11 with its Install Manager first", result.stderr)

    def test_apply_points_at_the_fix_and_changes_nothing(self):
        before = (self.package / "layout.json").read_bytes()
        result = self.script("apply")
        self.assertEqual((result.returncode, result.stdout.startswith("already nothing to apply")), (0, True))
        self.assertIn("addon launch tfdi-md-11 fix-navdata", result.stdout)
        self.assertEqual((self.package / "layout.json").read_bytes(), before)


class PageTest(unittest.TestCase):
    def page(self, id_):
        head, _, body = (PAGES / f"{id_}.html").read_text().partition("\n---\n")
        return dict(line.split(": ", 1) for line in head.splitlines() if line.strip()), body

    def test_every_page_has_the_head_the_lead_and_the_sections_in_the_template_order(self):
        for id_ in IDS:
            with self.subTest(id_):
                meta, body = self.page(id_)
                self.assertTrue(meta["title"] and meta["description"])
                self.assertEqual(len(re.findall(r"<h1>", body)), 1)
                self.assertTrue(body.lstrip().startswith("<h1>"))
                lead = re.search(r'<p class="lead">(.*?)</p>', body, re.S).group(1)
                tested = date.fromisoformat(EXPECTED[id_]["on"])
                self.assertIn(f"{EXPECTED[id_]['version']}", lead)
                self.assertIn(f"{tested.day} {MONTHS[tested.month - 1]} {tested.year}", lead)
                headings = re.findall(r"<h2>(.*?)</h2>", body)
                self.assertEqual(headings, [name for name in PAGE_SECTIONS if name in headings])
                self.assertIn("Known problems", headings)

    def test_the_voice_rules_hold(self):
        for id_ in IDS:
            text = (PAGES / f"{id_}.html").read_text()
            with self.subTest(id_):
                for word in BANNED:
                    self.assertIsNone(re.search(re.escape(word) if not word.isalpha() else rf"\b{word}\b", text, re.I), word)
                for word in OWNER_ONLY:
                    self.assertNotIn(word, text)

    def test_every_command_on_a_page_exists_for_its_add_on(self):
        for id_ in IDS:
            addon = addons.load(ADDONS / id_)
            text = re.sub(r"<[^>]+>", "", (PAGES / f"{id_}.html").read_text())
            with self.subTest(id_):
                for verb, target, script in re.findall(r"flightsim-on-linux addon (\w+) ([a-z0-9-]+)(?: ([a-z-]+))?", text):
                    self.assertEqual(target, id_ if verb != "list" else target)
                    if verb == "launch" and script and script != "--":
                        self.assertTrue(addon.has_script(script), f"{verb} {target} {script}")
                    if verb == "install":
                        self.assertTrue(addon.install or addon.can_install)
                    if verb == "shortcut":
                        self.assertTrue(addon.shortcuts)
                    self.assertNotEqual(verb, "watcher")

    def test_a_page_that_changes_the_add_ons_files_says_so_before_the_first_section_with_the_undo_command(self):
        for id_ in IDS:
            if not EXPECTED[id_]["touches"]:
                continue
            with self.subTest(id_):
                _, body = self.page(id_)
                top = body.split("<h2>", 1)[0]
                self.assertIn(f"addon undo {id_}", top)
                self.assertIn("flightsim-on-linux-backup", top)

    def test_the_links_go_to_pages_that_exist_or_to_the_ones_other_writers_own(self):
        for id_ in IDS:
            _, body = self.page(id_)
            for target in re.findall(r'href="([^"#]+)"', body):
                with self.subTest(page=id_, link=target):
                    if target.startswith("http"):
                        continue
                    self.assertTrue((PAGES / target).exists() or target in PAGES_OF_OTHERS, target)

    def test_a_store_page_sends_the_reader_to_the_store_page_and_says_nothing_else_is_needed(self):
        links = {"fss-e-jets": "contrail.html", "fss-727": "aerosoft-one.html", "aerosoft-a346-pro": "aerosoft-one.html"}
        for id_, link in links.items():
            with self.subTest(id_):
                _, body = self.page(id_)
                self.assertIn(f'href="{link}"', body)
                self.assertIn("nothing", re.sub(r"<[^>]+>", "", body).lower())

    def test_the_tfdi_page_names_the_hub_for_airac_and_the_first_load_time(self):
        _, body = self.page("tfdi-md-11")
        self.assertIn('href="navigraph-hub.html"', body)
        self.assertIn("AIRAC", body)
        self.assertIn("about 4 minutes", body)

    def test_the_rj_page_explains_the_trap_variable_and_that_install_clears_it(self):
        _, body = self.page("just-flight-rj-professional")
        self.assertIn("NoDefaultCurrentDirectoryInExePath", body)
        self.assertIn("addon install just-flight-rj-professional", body)

    def test_the_pmdg_page_keeps_the_colours_as_an_option_with_the_old_values(self):
        _, body = self.page("pmdg-oc3")
        for name in ("ActiveBorder", "InactiveBorder", "ButtonHilight", "ButtonLight", "ButtonShadow", "ButtonDkShadow"):
            self.assertIn(name, body)
        self.assertIn("18 18 18", body)
        self.assertIn("227 227 227", body)


if __name__ == "__main__":
    unittest.main()
