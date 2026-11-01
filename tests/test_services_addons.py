import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from fsol import addons, desktop, exe_xml, registry, watcher
from tests.support import process_stubs, stand_in_name, start_stand_in
from tests.test_addons import RecordingTools
from tests.test_machine import FakeMachine, write

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "site" / "pages"
FOUR_OPTIONS = "--in-process-gpu --disable-gpu --disable-gpu-compositing --disable-software-rasterizer"
WEBVIEW2 = r"Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments"
REPLACEMENTS = r"Software\Wine\Fonts\Replacements"

WINE_REGISTRY_STUB = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with open("%(record)s", "a") as record:
    record.write(" ".join([sys.argv[0]] + args) + "\\n")
if args[:1] != ["reg"]:
    sys.exit(0)
file = Path(os.environ["WINEPREFIX"]) / "user.reg"
lines = file.read_text().splitlines() if file.exists() else []
header = "[" + args[2].split("\\\\", 1)[1].replace("\\\\", "\\\\\\\\") + "]"
name = args[args.index("/v") + 1]
start = next((i for i, line in enumerate(lines) if line == header or line.startswith(header + " ")), None)
if args[1] == "add":
    entry = '"' + name + '"="' + args[args.index("/d") + 1] + '"'
    if start is None:
        lines += ["", header + " 1", "#time=1", entry]
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i] == ""), len(lines))
        kept = [line for line in lines[start + 1:end] if not line.startswith('"' + name + '"=')]
        lines[start + 1:end] = kept + [entry]
elif start is not None:
    end = next((i for i in range(start + 1, len(lines)) if lines[i] == ""), len(lines))
    lines[start + 1:end] = [line for line in lines[start + 1:end] if not line.startswith('"' + name + '"=')]
file.write_text("\\n".join(lines) + "\\n")
"""

XDG_MIME_STUB = """#!/bin/sh
list="$XDG_CONFIG_HOME/mimeapps.list"
case "$1" in
  query) grep "^$3=" "$list" 2> /dev/null | head -n 1 | cut -d= -f2- | tr -d ';' ;;
  default)
    mkdir -p "$(dirname "$list")"
    { echo "[Default Applications]"; grep -v -e '^\\[Default Applications\\]' -e "^$3=" "$list" 2> /dev/null; echo "$3=$2"; } > "$list.new"
    mv "$list.new" "$list"
    ;;
esac
"""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def recording(file, after=""):
    return f'#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "{file}"\nenv >> "{file}.env"\npwd >> "{file}.cwd"\n{after}'


def stub(path, text):
    write(path, text)
    path.chmod(0o755)
    return path


def sim_xml(port):
    return f"<SimBase.Document>\r\n<SimConnect.Comm>\r\n<Protocol>IPv4</Protocol>\r\n<Port>{port}</Port>\r\n</SimConnect.Comm>\r\n</SimBase.Document>"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def page_text(name):
    return (PAGES / f"{name}.html").read_text()


class ServiceCase(unittest.TestCase):
    ID = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake = FakeMachine(self.root).build()
        self.found = self.fake.discover()
        self.proton = self.fake.tool
        self.record = self.root / "record"
        self.data = self.root / "data"
        self.config = self.root / "config"
        self.processes = []
        self.addon = ROOT / "addons" / self.ID
        self.addons_prefix = self.data / "flightsim-on-linux" / "addons-prefix"
        self.token = str(self.root)
        process_stubs(self.root / "bin", self.token)

    def tearDown(self):
        for process in self.processes:
            process.kill()
            process.wait()
        self.temp.cleanup()

    def environment(self, **extra):
        env = {
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "XDG_DATA_HOME": str(self.data),
            "XDG_CONFIG_HOME": str(self.config),
            "FSOL_PREFIX": str(self.found.prefix),
            "FSOL_COMPATDATA": str(self.found.compatdata),
            "FSOL_PROTON": str(self.found.proton),
            "FSOL_COMMUNITY": str(self.found.community),
            "FSOL_BASE": str(self.found.base),
            "FSOL_CACHE": str(self.root / "cache"),
            "FSOL_CLI": str(self.root / "cli"),
            "FSOL_ADDON": self.ID,
            "FSOL_SIM_RUNNING": "0",
            "FSOL_CLOSE_WAIT": "1",
            "FSOL_REGISTRY_WAIT": "1",
        }
        env.update(extra)
        return env

    def script(self, name, *args, **extra):
        return subprocess.run([str(self.addon / name), *args], env=self.environment(**extra), capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def recorded(self):
        return self.record.read_text().splitlines() if self.record.exists() else []

    def recorded_env(self):
        path = Path(f"{self.record}.env")
        return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line) if path.exists() else {}

    def recorded_cwd(self):
        path = Path(f"{self.record}.cwd")
        return path.read_text().splitlines() if path.exists() else []

    def wait_for_record(self, lines=1, seconds=5):
        deadline = time.monotonic() + seconds
        while (len(self.recorded()) < lines or len(self.recorded_cwd()) < lines) and time.monotonic() < deadline:
            time.sleep(0.05)

    def forget_records(self):
        for path in (self.record, Path(f"{self.record}.env"), Path(f"{self.record}.cwd")):
            path.unlink(missing_ok=True)

    def tree(self):
        return sorted(str(path) for path in self.root.rglob("*") if path != self.record and not path.name.startswith("record."))

    def stand_in(self, program):
        process = start_stand_in(self.root, self.token, stand_in_name(program), 60)
        self.processes.append(process)
        time.sleep(0.2)
        return process

    def write_build(self, modules, drop=(), tamper=()):
        record = ["release test x86_64"]
        for patch, module in modules.items():
            data = f"built {module}".encode()
            built = self.proton / "files" / module
            built.parent.mkdir(parents=True, exist_ok=True)
            if built.exists():
                data = built.read_bytes()
            else:
                built.write_bytes(data)
            if patch not in drop:
                record.append(f"patch {sha256((ROOT / 'patches' / 'wine' / patch).read_bytes())} {patch}")
            record.append(f"module {sha256(b'something else') if patch in tamper else sha256(data)} {module}")
        (self.proton / "fs2024-build.txt").write_text("\n".join(record) + "\n")

    def add_reg(self, file, key, values):
        old = file.read_text() if file.exists() else ""
        lines = ["", "[" + key.replace("\\", "\\\\") + "] 1", "#time=1"] + [f'"{name}"="{value}"' for name, value in values.items()]
        file.write_text(old + "\n".join(lines) + "\n")

    def user_reg(self, key, **values):
        self.add_reg(self.found.prefix / "user.reg", key, values)

    def addons_prefix_reg(self, key, **values):
        self.add_reg(self.addons_prefix / "user.reg", key, values)

    def reg_value(self, key, name):
        file = self.found.prefix / "user.reg"
        return registry.value(registry.load(file), key, name) if file.exists() else None

    def addons_prefix_value(self, key, name):
        file = self.addons_prefix / "user.reg"
        return registry.value(registry.load(file), key, name) if file.exists() else None

    def addons_prefix_ready(self):
        (self.found.prefix / "drive_c").mkdir(parents=True, exist_ok=True)
        for name in ("system.reg", "user.reg"):
            write(self.addons_prefix / name, "")
        write(self.addons_prefix / ".update-timestamp", "disable\n")
        (self.addons_prefix / "drive_c").symlink_to(self.found.prefix / "drive_c")
        write(self.found.base / "SimConnect.xml", sim_xml(5111))
        write(self.addons_prefix / "SimConnect.cfg", "[SimConnect]\r\nProtocol=IPv4\r\nAddress=127.0.0.1\r\nPort=5111\r\n")

    def wine_call(self, *args):
        return " ".join([f"{self.proton}/files/bin/wine", *args])

    def wine_that_edits_the_registry(self):
        stub(self.proton / "files/bin/wine", WINE_REGISTRY_STUB % {"record": self.record})
        stub(self.proton / "files/bin/wineserver", recording(f"{self.record}.server"))
        stub(self.root / "bin" / "notify-send", recording(self.record))

    def stubs(self, wine_exit=0, after=""):
        stub(self.root / "bin" / "notify-send", recording(self.record))
        stub(self.root / "cli", recording(self.record, after))
        stub(self.proton / "files/bin/wine", recording(self.record, after) + f"exit {wine_exit}\n")
        stub(self.proton / "files/bin/wineserver", recording(f"{self.record}.server"))

    def system_wine(self, wine_exit=0, after=""):
        stub(self.root / "bin" / "notify-send", recording(self.record))
        stub(self.root / "bin" / "wine", recording(self.record, after) + f"exit {wine_exit}\n")
        stub(self.root / "bin" / "wineserver", recording(f"{self.record}.server"))

    def install_program(self, relative):
        write(self.found.prefix / relative, "MZ")


class ShapeTest(unittest.TestCase):
    NAMES = ("active-sky", "rex-atmos-core", "vraas", "beyondatc", "volanta", "navigraph-simlink", "flightsim-dispatch")

    def test_every_manifest_loads_with_a_tested_version_and_date(self):
        for name in self.NAMES:
            addon = addons.load(ROOT / "addons" / name)
            self.assertEqual(addon.id, name)
            self.assertTrue(addon.tested_version and addon.tested_on, name)
            self.assertTrue(addon.install and addon.shortcuts, name)

    def test_what_each_addon_installs_and_where_it_comes_from(self):
        expected = {
            "active-sky": ("http://www.hifisimtech.com/", True, "installer", False),
            "rex-atmos-core": ("https://rexaxis.com", None, "installer", False),
            "vraas": ("the Contrail store (LAND3 Simulations)", None, "store", True),
            "beyondatc": ("https://www.beyondatc.net", None, "installer", False),
            "navigraph-simlink": (None, None, "installer", True),
            "flightsim-dispatch": (None, None, "installer", True),
        }
        for name, (source, paid, kind, scripted) in expected.items():
            addon = addons.load(ROOT / "addons" / name)
            self.assertEqual((addon.install["paid"], addon.install["kind"], addon.can_install), (paid, kind, scripted), name)
            if source:
                self.assertEqual(addon.install["from"], source)
        self.assertIn("api.volanta.app/api/v1/ClientUpdate/latest.yml", addons.load(ROOT / "addons" / "volanta").install["from"])

    def test_no_addon_hands_out_a_download_address_it_does_not_know(self):
        for name in ("navigraph-simlink", "flightsim-dispatch"):
            self.assertNotIn("http", addons.load(ROOT / "addons" / name).install["from"])

    def test_the_needs_of_each_addon(self):
        needs = {name: addons.load(ROOT / "addons" / name).needs for name in self.NAMES}
        self.assertEqual(needs["active-sky"], ["proton-fs2024", "simulator-closed"])
        self.assertEqual(needs["rex-atmos-core"], ["proton-fs2024", "simulator-closed"])
        self.assertEqual(needs["volanta"], ["simulator-closed"])
        self.assertEqual(needs["vraas"], ["simulator-closed"])
        for name in ("beyondatc", "navigraph-simlink", "flightsim-dispatch"):
            self.assertEqual(needs[name], [])

    def test_the_menu_entries_pass_the_desktop_validator(self):
        for name in self.NAMES:
            with tempfile.TemporaryDirectory() as folder:
                tools = RecordingTools()
                written = desktop.write_shortcuts(addons.load(ROOT / "addons" / name), FakeMachine(Path(folder)).build().discover(), ROOT / "bin" / "flightsim-on-linux", Path(folder) / "data", tools, lambda line: None)
                if shutil.which("desktop-file-validate"):
                    result = subprocess.run(["desktop-file-validate", str(written[0])], capture_output=True, text=True)
                    self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""), name)

    def test_active_sky_has_a_close_action_and_the_sign_in_entry_opens_the_link(self):
        active = addons.load(ROOT / "addons" / "active-sky")
        self.assertEqual(active.shortcuts[0]["close"], "close")
        simlink = addons.load(ROOT / "addons" / "navigraph-simlink")
        self.assertEqual(simlink.shortcuts[0]["mime"], ["x-scheme-handler/navigraph-traffic-desktop"])
        with tempfile.TemporaryDirectory() as folder:
            written = desktop.write_shortcuts(simlink, FakeMachine(Path(folder)).build().discover(), Path("/cli"), Path(folder) / "data", RecordingTools(), lambda line: None)
            text = written[0].read_text()
        self.assertIn("MimeType=x-scheme-handler/navigraph-traffic-desktop;", text)
        self.assertIn('addon launch navigraph-simlink launch -- %u', text)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "needs systemd-analyze")
    def test_the_services_they_write_pass_systemd_verify(self):
        for name in ("active-sky", "vraas", "volanta", "navigraph-simlink"):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "unit.service"
                path.write_text(watcher.unit_text(addons.load(ROOT / "addons" / name), ROOT / "bin" / "flightsim-on-linux"))
                result = subprocess.run(["systemd-analyze", "--user", "verify", str(path)], capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""), name)
                self.assertIn("KillMode=process", path.read_text())

    def test_the_watchers_match_their_program_and_nothing_else(self):
        programs = {
            "active-sky": "C:\\users\\steamuser\\AppData\\Roaming\\HiFi\\AS_FS\\AddonFolder\\ActiveSkyUtils.exe",
            "vraas": "C:\\users\\steamuser\\AppData\\Local\\LAND3.vRAAS\\current\\vRAAS.exe",
            "volanta": "C:\\users\\steamuser\\AppData\\Local\\Programs\\Volanta\\Volanta.exe --minimized",
            "navigraph-simlink": "C:\\Program Files\\Navigraph\\Simlink\\NavigraphSimlink.exe",
        }
        for name, program in programs.items():
            pattern = addons.load(ROOT / "addons" / name).watcher["app"]
            self.assertTrue(re.search(pattern, program), name)
            self.assertFalse(re.search(pattern, "C:\\Program Files\\Other\\Other.exe"), name)
            self.assertFalse(re.search(pattern, f"/usr/bin/python3 flightsim-on-linux addon watch {name}"), name)

    def test_the_watcher_settings_of_vraas_and_volanta(self):
        vraas = addons.load(ROOT / "addons" / "vraas").watcher
        self.assertEqual((vraas["grace"], vraas["args"], vraas["stop"], vraas.get("trigger", "simulator")), (20, ["-autostart"], "with_simulator_if_started", "simulator"))
        volanta = addons.load(ROOT / "addons" / "volanta").watcher
        self.assertEqual((volanta["args"], volanta["stop"], volanta.get("trigger", "simulator")), (["--minimized"], "never", "simulator"))

    def test_the_active_sky_watcher_keeps_activesky_utils_up_while_the_sentinel_is_open_and_never_closes_it(self):
        utils = addons.load(ROOT / "addons" / "active-sky").watcher
        sentinel = addons.load(ROOT / "addons" / "sentinel").watcher
        self.assertEqual((utils["script"], utils["trigger"], utils["stop"]), ("utils", {"process": sentinel["app"]}, "never"))

    def test_the_simlink_watcher_never_starts_it_and_restarts_it_after_sixty_seconds_without_a_connection(self):
        simlink = addons.load(ROOT / "addons" / "navigraph-simlink").watcher
        self.assertEqual(simlink["trigger"], "none")
        self.assertEqual(simlink["health"], {"connected_to": 5111, "lost_for": 60, "restart": "restart"})
        self.assertNotIn("stop", simlink)

    def test_the_port_the_simlink_watcher_watches_is_the_one_simconnect_tcp_moves_the_simulator_to(self):
        parts = load_module(ROOT / "addons" / "simconnect-tcp" / "parts" / "simconnect_tcp.py", "simconnect_tcp_parts")
        self.assertEqual(addons.load(ROOT / "addons" / "navigraph-simlink").watcher["health"]["connected_to"], parts.PORT)


class RegistryAddonCase(ServiceCase):
    def assert_lines(self, result, words, code):
        self.assertEqual(result.returncode, code, result.stdout)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], words, result.stdout)


class ActiveSkyCase(RegistryAddonCase):
    ID = "active-sky"
    PATCH = "gdiplus-font-family-after-substitution.patch"
    MODULE = "lib/wine/x86_64-windows/gdiplus.dll"
    PROGRAM = "C:\\Program Files\\HiFi\\AS_FS\\AS_FS.exe"
    EXE = "drive_c/Program Files/HiFi/AS_FS/AS_FS.exe"
    UTILS = "C:\\users\\steamuser\\AppData\\Roaming\\HiFi\\AS_FS\\AddonFolder\\ActiveSkyUtils.exe"
    UTILS_EXE = "drive_c/users/steamuser/AppData/Roaming/HiFi/AS_FS/AddonFolder/ActiveSkyUtils.exe"

    def setUp(self):
        super().setUp()
        self.write_build({self.PATCH: self.MODULE})
        stub(self.root / "bin" / "fc-list", '#!/bin/sh\n[ "$1" = Carlito ] && echo Carlito\nexit 0\n')
        (self.found.prefix / "user.reg").write_text("")
        self.addons_prefix_ready()

    def fix_in_place(self):
        (self.addons_prefix / "user.reg").write_text("")
        self.addons_prefix_reg(REPLACEMENTS, **{"Calibri": "Carlito", "Calibri Medium": "Carlito"})

    def exe_xml(self, disabled="False"):
        write(self.found.base / "exe.xml", '<?xml version="1.0" encoding="windows-1252"?>\n<SimBase.Document Type="Launch" version="1,0">\n    <Launch.Addon>\n        <Name>ActiveSky utils</Name>\n'
              f'        <Disabled>{disabled}</Disabled>\n        <Path>{self.UTILS}</Path>\n    </Launch.Addon>\n</SimBase.Document>\n')

    def utils_entry(self):
        return exe_xml.state_ending(self.found.base / "exe.xml", "\\AddonFolder\\ActiveSkyUtils.exe")[0]

    def without_carlito(self):
        stub(self.root / "bin" / "fc-list", "#!/bin/sh\nexit 0\n")

    def stub_curl(self, code):
        stub(self.root / "bin" / "curl", recording(self.record, f'printf "{code}"\n'))


class ActiveSkyCheckTest(ActiveSkyCase):
    LINES = 11

    def setUp(self):
        super().setUp()
        self.fix_in_place()

    def only_failure(self, word):
        result = self.script("check")
        failures = [line for line in result.stdout.splitlines() if line.startswith("FAIL")]
        self.assertEqual((result.returncode, len(failures)), (1, 1), result.stdout)
        self.assertIn(word, failures[0])
        self.assertEqual(len(result.stdout.splitlines()), self.LINES)

    def test_a_machine_with_the_patch_carlito_and_both_values_passes_on_every_line(self):
        self.assert_lines(self.script("check"), ["ok"] * self.LINES, 0)

    def test_the_utils_entry_of_exe_xml_turned_back_on_by_an_update_fails(self):
        self.exe_xml("False")
        self.only_failure("exe.xml still starts ActiveSky Utils")

    def test_a_disabled_utils_entry_passes(self):
        self.exe_xml("True")
        self.assert_lines(self.script("check"), ["ok"] * self.LINES, 0)

    def test_a_missing_add_ons_prefix_fails_with_the_command(self):
        shutil.rmtree(self.addons_prefix)
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("run: flightsim-on-linux addon apply active-sky", result.stdout)

    def test_the_fonts_are_read_from_the_add_ons_prefix_and_not_from_the_simulators(self):
        (self.addons_prefix / "user.reg").write_text("")
        self.user_reg(REPLACEMENTS, **{"Calibri": "Carlito", "Calibri Medium": "Carlito"})
        self.assertEqual(self.script("check").returncode, 1)

    def test_a_proton_without_the_font_family_patch_in_its_build_record_fails(self):
        self.write_build({self.PATCH: self.MODULE}, drop=(self.PATCH,))
        self.only_failure(self.PATCH)

    def test_a_gdiplus_that_differs_from_the_build_record_fails(self):
        self.write_build({self.PATCH: self.MODULE}, tamper=(self.PATCH,))
        self.only_failure("gdiplus.dll")

    def test_a_system_without_the_carlito_font_fails(self):
        self.without_carlito()
        self.only_failure("Carlito font is not installed")

    def test_calibri_not_replaced_fails(self):
        (self.addons_prefix / "user.reg").write_text("")
        self.addons_prefix_reg(REPLACEMENTS, **{"Calibri Medium": "Carlito"})
        self.only_failure("Calibri is not replaced")

    def test_calibri_medium_not_replaced_fails(self):
        (self.addons_prefix / "user.reg").write_text("")
        self.addons_prefix_reg(REPLACEMENTS, Calibri="Carlito")
        self.only_failure("Calibri Medium is not replaced")

    def test_a_replacement_by_another_font_fails(self):
        (self.addons_prefix / "user.reg").write_text("")
        self.addons_prefix_reg(REPLACEMENTS, **{"Calibri": "Arial", "Calibri Medium": "Carlito"})
        self.only_failure("Calibri is not replaced")

    def test_check_changes_nothing(self):
        before = self.tree()
        self.script("check")
        self.assertEqual(self.tree(), before)


class ActiveSkyWeatherApiTest(ActiveSkyCase):
    def setUp(self):
        super().setUp()
        self.fix_in_place()
        self.api = "http://127.0.0.1:19285/ActiveSky/API/GetWeatherInfoJson"

    def test_without_active_sky_running_the_api_is_not_asked(self):
        self.stub_curl(200)
        result = self.script("check", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("ok Active Sky is not running", result.stdout)
        self.assertEqual(self.recorded(), [])

    def test_running_without_the_simulator_does_not_ask_either(self):
        self.stand_in(self.PROGRAM)
        self.stub_curl(503)
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("simulator is not", result.stdout)
        self.assertEqual(self.recorded(), [])

    def test_a_200_from_the_api_passes_and_the_url_asked_is_the_documented_one(self):
        self.stand_in(self.PROGRAM)
        self.stub_curl(200)
        result = self.script("check", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"ok {self.api} answers 200", result.stdout)
        self.assertTrue(self.recorded()[0].endswith(self.api), self.recorded())

    def test_a_503_from_the_api_fails_and_says_what_it_answered(self):
        self.stand_in(self.PROGRAM)
        self.stub_curl(503)
        result = self.script("check", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"FAIL {self.api} answers 503", result.stdout)

    def test_no_answer_at_all_fails(self):
        self.stand_in(self.PROGRAM)
        self.stub_curl("")
        result = self.script("check", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("answers nothing", result.stdout)


class ActiveSkyApplyTest(ActiveSkyCase):
    def setUp(self):
        super().setUp()
        (self.found.prefix / "user.reg").write_text("")
        self.wine_that_edits_the_registry()

    def test_apply_replaces_both_fonts_with_carlito_and_the_check_then_passes(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.addons_prefix_value(REPLACEMENTS, "Calibri"), "Carlito")
        self.assertEqual(self.addons_prefix_value(REPLACEMENTS, "Calibri Medium"), "Carlito")
        self.assertEqual(self.script("check").returncode, 0)

    def test_apply_twice_changes_nothing_the_second_time(self):
        self.script("apply")
        calls = len(self.recorded())
        result = self.script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertIn("already Calibri is Carlito", result.stdout)
        self.assertEqual(len(self.recorded()), calls)

    def test_without_the_carlito_font_apply_refuses_and_writes_nothing(self):
        self.without_carlito()
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ttf-carlito", result.stdout)
        self.assertEqual(self.recorded(), [])
        self.assertIsNone(self.addons_prefix_value(REPLACEMENTS, "Calibri"))

    def test_the_registry_is_written_through_wine_in_the_add_ons_prefix_and_not_in_the_simulators(self):
        self.script("apply")
        self.assertEqual(self.recorded()[0], self.wine_call("reg", "add", f"HKCU\\{REPLACEMENTS}", "/v", "Calibri", "/t", "REG_SZ", "/d", "Carlito", "/f"))
        self.assertIsNone(self.reg_value(REPLACEMENTS, "Calibri"))

    def test_apply_makes_the_add_ons_prefix_when_it_is_not_there(self):
        shutil.rmtree(self.addons_prefix)
        write(self.found.prefix / "system.reg", "system\n")
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.addons_prefix / "system.reg").read_text(), "system\n")
        self.assertIn("Port=5111", (self.addons_prefix / "SimConnect.cfg").read_text())

    def test_apply_turns_off_the_utils_entry_of_exe_xml_and_undo_turns_it_back_on(self):
        self.exe_xml("False")
        self.assertIn("changed exe.xml", self.script("apply").stdout)
        self.assertEqual(self.utils_entry(), "disabled")
        self.assertEqual(self.script("check").returncode, 0)
        self.assertIn("changed exe.xml", self.script("undo").stdout)
        self.assertEqual(self.utils_entry(), "enabled")
        self.assertFalse((self.found.compatdata / "flightsim-on-linux-backup" / "active-sky").exists())

    def test_undo_leaves_an_entry_that_was_already_off_before_apply(self):
        self.exe_xml("True")
        self.script("apply")
        self.assertIn("already the ActiveSky Utils entry in exe.xml was not changed", self.script("undo").stdout)
        self.assertEqual(self.utils_entry(), "disabled")

    def test_a_machine_with_no_utils_entry_applies_and_says_so(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("exe.xml has no ActiveSky Utils entry", result.stdout)
        self.assertFalse((self.found.base / "exe.xml").exists())

    def test_undo_removes_the_values_from_the_add_ons_prefix(self):
        self.script("apply")
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(self.addons_prefix_value(REPLACEMENTS, "Calibri"))
        self.assertIsNone(self.addons_prefix_value(REPLACEMENTS, "Calibri Medium"))

    def test_undo_restores_what_an_older_apply_changed_in_the_simulators_prefix(self):
        self.user_reg(REPLACEMENTS, Calibri="Carlito")
        backup = self.found.compatdata / "flightsim-on-linux-backup" / "active-sky"
        write(backup / "Calibri", "Arial")
        write(backup / "Calibri Medium.absent", "")
        self.script("undo")
        self.assertEqual(self.reg_value(REPLACEMENTS, "Calibri"), "Arial")
        self.assertFalse(backup.exists())

    def test_undo_leaves_the_simulators_own_values_alone(self):
        self.user_reg(REPLACEMENTS, Calibri="Carlito")
        self.script("apply")
        self.script("undo")
        self.assertEqual(self.reg_value(REPLACEMENTS, "Calibri"), "Carlito")

    def test_undo_without_apply_says_nothing_was_changed(self):
        result = self.script("undo")
        self.assertEqual(result.returncode, 0)
        self.assertIn("already Calibri was not changed by this add-on", result.stdout)
        self.assertEqual(self.recorded(), [])

    def test_without_a_compatdata_folder_to_keep_the_old_values_in_apply_stops_before_writing(self):
        result = self.script("apply", FSOL_COMPATDATA="")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.recorded(), [])

    def test_a_registry_that_wine_has_not_flushed_fails_the_apply(self):
        stub(self.proton / "files/bin/wine", recording(self.record) + "exit 0\n")
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Wine has not written Calibri to user.reg of the add-ons prefix yet", result.stderr)


class ActiveSkyLaunchTest(ActiveSkyCase):
    def setUp(self):
        super().setUp()
        self.stubs()
        self.install_program(self.EXE)

    def test_a_copy_already_running_is_left_alone(self):
        self.stand_in(self.PROGRAM)
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(any("already running" in line for line in self.recorded()))
        self.assertFalse(any(self.wine_call() in line for line in self.recorded()))

    def test_a_missing_program_points_at_the_page_because_there_is_no_install_script(self):
        (self.found.prefix / self.EXE).unlink()
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("flightsim-on-linux/active-sky.html", result.stderr)
        self.assertNotIn("addon install", result.stderr)

    def test_a_missing_add_ons_prefix_says_how_to_make_it(self):
        shutil.rmtree(self.addons_prefix)
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon apply active-sky", result.stderr)

    def test_it_starts_in_the_add_ons_prefix_whether_or_not_the_simulator_runs(self):
        for running in ("0", "1"):
            self.forget_records()
            result = self.script("launch", FSOL_SIM_RUNNING=running)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.wait_for_record()
            self.assertEqual(self.recorded(), [self.wine_call(self.PROGRAM)])
        self.assertEqual(self.recorded_env()["WINEPREFIX"], str(self.addons_prefix))
        self.assertEqual(self.recorded_cwd()[-1], str(self.addons_prefix))


class ActiveSkyUtilsTest(ActiveSkyCase):
    def setUp(self):
        super().setUp()
        self.stubs()
        self.install_program(self.UTILS_EXE)

    def test_it_starts_activesky_utils_in_the_add_ons_prefix(self):
        result = self.script("utils")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.recorded(), [self.wine_call(self.UTILS)])
        self.assertEqual(self.recorded_env()["WINEPREFIX"], str(self.addons_prefix))
        self.assertEqual(self.recorded_cwd()[-1], str(self.addons_prefix))

    def test_a_copy_already_running_is_left_alone(self):
        self.stand_in(self.UTILS)
        self.assertEqual(self.script("utils").returncode, 0)
        self.assertFalse(any(self.wine_call() in line for line in self.recorded()))

    def test_a_missing_utils_says_where_it_comes_from(self):
        (self.found.prefix / self.UTILS_EXE).unlink()
        result = self.script("utils")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ActiveSky Utils is not installed", result.stderr)


class ActiveSkyCloseTest(ActiveSkyCase):
    def test_nothing_running_means_nothing_to_close(self):
        self.stubs()
        result = self.script("close")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "Active Sky is not running"))
        self.assertEqual(self.recorded(), [])

    def test_the_window_is_asked_to_close_in_the_add_ons_prefix_and_the_force_flag_is_never_used(self):
        program = self.stand_in(self.PROGRAM)
        self.stubs(after=f"kill {program.pid}\n")
        result = self.script("close", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [self.wine_call("taskkill", "/im", "AS_FS.exe")])
        self.assertNotIn("/f", " ".join(self.recorded()).split())

    def test_a_program_that_stays_is_reported_with_the_reason_and_never_killed(self):
        program = self.stand_in(self.PROGRAM)
        self.stubs()
        result = self.script("close", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("still running", result.stderr)
        self.assertIn("never killed", result.stderr)
        self.assertIsNone(program.poll())
        self.assertNotIn("/f", " ".join(self.recorded()).split())


class RexCase(RegistryAddonCase):
    ID = "rex-atmos-core"
    PATCHES = {
        "win32u-cross-process-child-windows.patch": "lib/wine/x86_64-unix/win32u.so",
        "server-process-vm-write-falls-back-to-ptrace.patch": "bin/wineserver",
    }
    PROGRAM = "C:\\REX Atmos Core\\weathercreator.exe"
    EXE = "drive_c/REX Atmos Core/weathercreator.exe"

    def setUp(self):
        super().setUp()
        self.write_build(self.PATCHES)
        (self.found.prefix / "user.reg").write_text("")


class RexCheckTest(RexCase):
    def setUp(self):
        super().setUp()
        self.user_reg(WEBVIEW2, **{"*": "other", "weathercreator.exe": FOUR_OPTIONS})

    def only_failure(self, word):
        result = self.script("check")
        failures = [line for line in result.stdout.splitlines() if line.startswith("FAIL")]
        self.assertEqual((result.returncode, len(failures)), (1, 1), result.stdout)
        self.assertIn(word, failures[0])
        self.assertEqual(len(result.stdout.splitlines()), 3)

    def test_a_machine_with_both_patches_and_the_policy_passes_with_three_ok_lines(self):
        self.assert_lines(self.script("check"), ["ok"] * 3, 0)

    def test_a_proton_without_the_child_window_patch_fails(self):
        self.write_build(self.PATCHES, drop=("win32u-cross-process-child-windows.patch",))
        self.only_failure("win32u-cross-process-child-windows.patch")

    def test_a_proton_without_the_wineserver_write_patch_fails(self):
        self.write_build(self.PATCHES, drop=("server-process-vm-write-falls-back-to-ptrace.patch",))
        self.only_failure("server-process-vm-write-falls-back-to-ptrace.patch")

    def test_a_wineserver_that_differs_from_the_build_record_fails(self):
        self.write_build(self.PATCHES, tamper=("server-process-vm-write-falls-back-to-ptrace.patch",))
        self.only_failure("bin/wineserver")

    def test_a_policy_without_the_program_value_fails(self):
        (self.found.prefix / "user.reg").write_text("")
        self.user_reg(WEBVIEW2, **{"*": FOUR_OPTIONS})
        self.only_failure("weathercreator.exe")

    def test_a_policy_value_that_misses_an_option_fails_because_a_program_value_replaces_the_star_value(self):
        (self.found.prefix / "user.reg").write_text("")
        self.user_reg(WEBVIEW2, **{"*": FOUR_OPTIONS, "weathercreator.exe": FOUR_OPTIONS.replace(" --disable-software-rasterizer", "")})
        self.only_failure("weathercreator.exe")

    def test_check_changes_nothing(self):
        before = self.tree()
        self.script("check")
        self.assertEqual(self.tree(), before)


class RexApplyTest(RexCase):
    def setUp(self):
        super().setUp()
        self.wine_that_edits_the_registry()
        self.write_build(self.PATCHES)

    def test_apply_writes_the_policy_value_with_the_four_options_and_the_check_then_passes(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.reg_value(WEBVIEW2, "weathercreator.exe"), FOUR_OPTIONS)
        self.assertEqual(self.script("check").returncode, 0)

    def test_apply_leaves_the_other_programs_values_alone(self):
        self.user_reg(WEBVIEW2, **{"*": FOUR_OPTIONS, "SayIntentionsAI.exe": "x"})
        self.script("apply")
        self.assertEqual(self.reg_value(WEBVIEW2, "*"), FOUR_OPTIONS)
        self.assertEqual(self.reg_value(WEBVIEW2, "SayIntentionsAI.exe"), "x")

    def test_apply_twice_writes_once(self):
        self.script("apply")
        calls = len(self.recorded())
        self.assertIn("already weathercreator.exe is", self.script("apply").stdout)
        self.assertEqual(len(self.recorded()), calls)

    def test_without_a_compatdata_folder_to_keep_the_old_value_in_apply_stops_before_writing(self):
        result = self.script("apply", FSOL_COMPATDATA="")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.recorded(), [])

    def test_undo_removes_only_its_value(self):
        self.user_reg(WEBVIEW2, **{"*": FOUR_OPTIONS})
        self.script("apply")
        self.assertEqual(self.script("undo").returncode, 0)
        self.assertIsNone(self.reg_value(WEBVIEW2, "weathercreator.exe"))
        self.assertEqual(self.reg_value(WEBVIEW2, "*"), FOUR_OPTIONS)

    def test_undo_puts_back_a_value_the_reader_had(self):
        self.user_reg(WEBVIEW2, **{"weathercreator.exe": "--remote-debugging-port=9334"})
        self.script("apply")
        self.script("undo")
        self.assertEqual(self.reg_value(WEBVIEW2, "weathercreator.exe"), "--remote-debugging-port=9334")


class RexLaunchTest(RexCase):
    def setUp(self):
        super().setUp()
        self.stubs()
        self.install_program(self.EXE)

    def test_a_closed_simulator_stops_it_with_a_notice(self):
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("start Microsoft Flight Simulator 2024 first", result.stderr)

    def test_a_copy_already_running_is_left_alone(self):
        self.stand_in(self.PROGRAM)
        self.assertEqual(self.script("launch", FSOL_SIM_RUNNING="1").returncode, 0)
        self.assertFalse(any(line.startswith(f"{self.root}/cli run") for line in self.recorded()))

    def test_it_starts_inside_the_simulators_container_with_direct3d_9_off_and_without_a_gpu_choice(self):
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.root}/cli run {self.PROGRAM}"])
        env = self.recorded_env()
        self.assertEqual(env["EXTRA_WINEDLLOVERRIDES"], "d3d9=d")
        self.assertNotIn("VK_LOADER_DRIVERS_SELECT", env)
        self.assertNotIn("DXVK_FILTER_DEVICE_NAME", env)

    def test_a_missing_program_points_at_the_page_because_there_is_no_install_script(self):
        (self.found.prefix / self.EXE).unlink()
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("flightsim-on-linux/rex-atmos-core.html", result.stderr)
        self.assertNotIn("addon install", result.stderr)


class RexPageTest(unittest.TestCase):
    def test_the_page_keeps_no_trace_of_the_retired_browser_route(self):
        text = page_text("rex-atmos-core").lower()
        for word in ("remote-debugging", "9334", "screencast", "servir-tela", "from your browser", "use it in your browser"):
            self.assertNotIn(word, text)
        self.assertIn("retired", text)

    def test_the_page_names_both_patches_and_the_policy_value(self):
        text = page_text("rex-atmos-core")
        for word in ("win32u-cross-process-child-windows.patch", "server-process-vm-write-falls-back-to-ptrace.patch", "weathercreator.exe", "fs2024-build.txt"):
            self.assertIn(word, text)


class VraasCase(ServiceCase):
    ID = "vraas"
    PROGRAM = "C:\\users\\steamuser\\AppData\\Local\\LAND3.vRAAS\\current\\vRAAS.exe"
    EXE = "drive_c/users/steamuser/AppData/Local/LAND3.vRAAS/current/vRAAS.exe"
    CONTRAIL = "C:\\users\\steamuser\\AppData\\Local\\ContrailApp\\app-3.0.0\\Contrail.exe"


class VraasTest(VraasCase):
    def test_apply_makes_the_add_ons_prefix_and_its_cfg(self):
        for name in ("system.reg", "user.reg"):
            write(self.found.prefix / name, f"sim {name}\n")
        (self.found.prefix / "drive_c").mkdir(parents=True, exist_ok=True)
        write(self.found.base / "SimConnect.xml", sim_xml(5111))
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.addons_prefix / "system.reg").read_text(), "sim system.reg\n")
        self.assertIn("Port=5111", (self.addons_prefix / "SimConnect.cfg").read_text())

    def test_undo_removes_the_cfg_next_to_the_program_and_leaves_the_prefix(self):
        self.install_program(self.EXE)
        self.addons_prefix_ready()
        cfg = (self.found.prefix / self.EXE).parent / "SimConnect.cfg"
        write(cfg, "x")
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(cfg.exists())
        self.assertTrue((self.addons_prefix / "SimConnect.cfg").exists())
        self.assertIn("already no SimConnect.cfg", self.script("undo").stdout)

    def test_check_passes_where_the_program_and_the_prefix_are_and_fails_with_the_command_where_they_are_not(self):
        failed = self.script("check")
        self.assertEqual(failed.returncode, 1)
        self.assertIn("addon install vraas", failed.stdout)
        self.install_program(self.EXE)
        unapplied = self.script("check")
        self.assertEqual(unapplied.returncode, 1)
        self.assertIn("addon apply vraas", unapplied.stdout)
        self.addons_prefix_ready()
        passed = self.script("check")
        self.assertEqual((passed.returncode, passed.stdout.split()[0]), (0, "ok"), passed.stdout)

    def test_it_counts_as_installed_where_the_program_is(self):
        addon = addons.load(self.addon)
        self.assertFalse(addons.is_installed(addon, self.found))
        self.install_program(self.EXE)
        self.assertTrue(addons.is_installed(addon, self.found))


class VraasInstallTest(VraasCase):
    def setUp(self):
        super().setUp()
        self.stubs()
        self.setup = self.root / "downloads" / "LAND3-Simulations-vRAAS-MSFS-FS24-139.exe"
        write(self.setup, "MZ")

    def test_the_installer_runs_alone_in_the_prefix(self):
        result = self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {self.setup}"])
        self.assertEqual(self.recorded_env()["WINEPREFIX"], str(self.found.prefix))
        self.assertTrue(Path(f"{self.record}.server").exists())

    def test_with_contrail_open_it_refuses_before_running_anything_and_explains_exit_82(self):
        self.stand_in(self.CONTRAIL)
        result = self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Contrail is open", result.stderr)
        self.assertIn("exit 82", result.stderr)
        self.assertEqual(self.recorded(), [])

    def test_an_installer_that_stops_with_82_is_explained_and_keeps_its_exit_code(self):
        self.stubs(wine_exit=82)
        result = self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 82)
        self.assertIn("exit 82", result.stderr)
        self.assertIn("error 5, access denied", result.stderr)
        self.assertIn("Contrail", result.stderr)

    def test_any_other_exit_code_is_passed_on_without_the_82_explanation(self):
        self.stubs(wine_exit=3)
        result = self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 3)
        self.assertNotIn("access denied", result.stderr)

    def test_without_an_installer_the_script_says_how_to_give_one(self):
        result = self.script("install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("flightsim-on-linux addon install vraas", result.stderr)
        self.assertEqual(self.recorded(), [])

    def test_the_variable_that_breaks_wine_installers_does_not_reach_them(self):
        self.script("install", FSOL_INSTALLER=str(self.setup), NoDefaultCurrentDirectoryInExePath="1")
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", self.recorded_env())


class VraasLaunchTest(VraasCase):
    def setUp(self):
        super().setUp()
        self.stubs()
        self.install_program(self.EXE)
        self.addons_prefix_ready()

    def test_a_copy_already_running_is_left_alone(self):
        self.stand_in(self.PROGRAM)
        self.assertEqual(self.script("launch", FSOL_SIM_RUNNING="1").returncode, 0)
        self.assertFalse(any(self.wine_call() in line for line in self.recorded()))

    def test_it_starts_in_the_add_ons_prefix_with_direct3d_9_off_whether_or_not_the_simulator_runs(self):
        for running in ("0", "1"):
            self.forget_records()
            result = self.script("launch", FSOL_SIM_RUNNING=running)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.wait_for_record()
            self.assertEqual(self.recorded(), [self.wine_call(self.PROGRAM)])
        env = self.recorded_env()
        self.assertEqual(env["WINEPREFIX"], str(self.addons_prefix))
        self.assertIn("d3d9=d", env["WINEDLLOVERRIDES"])

    def test_the_cfg_is_copied_next_to_the_program_at_every_start_because_an_update_replaces_that_folder(self):
        cfg = (self.found.prefix / self.EXE).parent / "SimConnect.cfg"
        self.script("launch")
        self.wait_for_record()
        self.assertEqual(cfg.read_bytes(), (self.addons_prefix / "SimConnect.cfg").read_bytes())
        cfg.unlink()
        self.script("launch")
        self.wait_for_record(2)
        self.assertTrue(cfg.is_file())

    def test_no_cfg_in_the_add_ons_prefix_says_how_to_write_it_and_starts_nothing(self):
        (self.addons_prefix / "SimConnect.cfg").unlink()
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon apply vraas", result.stderr)
        self.assertFalse(any(self.wine_call() in line for line in self.recorded()))

    def test_the_watchers_autostart_option_reaches_the_program(self):
        self.script("launch", "-autostart", FSOL_SIM_RUNNING="1")
        self.wait_for_record()
        self.assertEqual(self.recorded(), [self.wine_call(self.PROGRAM, "-autostart")])

    def test_a_missing_program_says_how_to_install_it(self):
        (self.found.prefix / self.EXE).unlink()
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install vraas", result.stderr)


class VraasPageTest(unittest.TestCase):
    def test_the_audio_noise_is_listed_as_not_an_error_and_exit_82_is_explained(self):
        text = page_text("vraas")
        self.assertIn("AudioSessionProbe", text)
        self.assertIn("82", text)


class BeyondAtcCase(ServiceCase):
    ID = "beyondatc"
    PROGRAM = "C:\\BeyondATC\\BeyondATC.exe"
    EXE = "drive_c/BeyondATC/BeyondATC.exe"


class BeyondAtcTest(BeyondAtcCase):
    def test_apply_and_undo_say_there_is_nothing_to_do_and_change_nothing(self):
        before = self.tree()
        for name in ("apply", "undo"):
            result = self.script(name)
            self.assertEqual(result.returncode, 0)
            self.assertIn("already nothing to", result.stdout)
        self.assertEqual(self.tree(), before)

    def test_check_looks_for_the_program(self):
        self.assertEqual(self.script("check").returncode, 1)
        self.install_program(self.EXE)
        result = self.script("check")
        self.assertEqual((result.returncode, result.stdout.split()[0]), (0, "ok"))

    def test_it_starts_inside_the_simulators_container_and_chooses_nothing_about_the_graphics(self):
        self.stubs()
        self.install_program(self.EXE)
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.root}/cli run {self.PROGRAM}"])
        env = self.recorded_env()
        for name in ("LIBGL_ALWAYS_SOFTWARE", "__EGL_VENDOR_LIBRARY_FILENAMES", "VK_LOADER_DRIVERS_SELECT", "EXTRA_WINEDLLOVERRIDES"):
            self.assertNotIn(name, env)

    def test_a_closed_simulator_stops_it_with_a_notice(self):
        self.stubs()
        self.install_program(self.EXE)
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("start Microsoft Flight Simulator 2024 first", result.stderr)

    def test_a_copy_already_running_is_left_alone(self):
        self.stubs()
        self.install_program(self.EXE)
        self.stand_in(self.PROGRAM)
        self.assertEqual(self.script("launch", FSOL_SIM_RUNNING="1").returncode, 0)
        self.assertFalse(any(line.startswith(f"{self.root}/cli run") for line in self.recorded()))

    def test_a_missing_program_fails_with_a_notice(self):
        self.stubs()
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not installed", result.stderr)


class BeyondAtcPageTest(unittest.TestCase):
    def test_the_software_opengl_choice_is_a_page_option_with_its_condition_and_the_page_claims_no_renderer(self):
        text = page_text("beyondatc")
        self.assertIn("-force-glcore", text)
        self.assertIn("LIBGL_ALWAYS_SOFTWARE=1", text)
        self.assertRegex(text, r"<h2>[^<]*main GPU")
        self.assertIn("Player.log", text)
        self.assertNotRegex(text, r"(?i)BeyondATC (draws|renders|runs) (with|on|in) (software|llvmpipe|OpenGL|Vulkan)")


class VolantaCase(ServiceCase):
    ID = "volanta"
    PROGRAM = "C:\\users\\steamuser\\AppData\\Local\\Programs\\Volanta\\Volanta.exe"
    EXE = "drive_c/users/steamuser/AppData/Local/Programs/Volanta/Volanta.exe"

    def setUp(self):
        super().setUp()
        for name in ("system.reg", "user.reg", "userdef.reg"):
            write(self.found.prefix / name, f"sim {name}\n")
        write(self.found.prefix / "drive_c" / "marker", "")
        write(self.found.base / "SimConnect.xml", sim_xml(5111))
        self.sibling = self.data / "flightsim-on-linux" / "addons-prefix"

    def sibling_ready(self):
        return self.script("apply")


class VolantaApplyTest(VolantaCase):
    def test_apply_makes_the_sibling_prefix_with_a_copy_of_the_registry_and_a_shared_drive_c(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual((self.sibling / "system.reg").read_text(), "sim system.reg\n")
        self.assertEqual((self.sibling / ".update-timestamp").read_text(), "disable\n")
        self.assertEqual((self.sibling / "drive_c").resolve(), (self.found.prefix / "drive_c").resolve())

    def test_apply_writes_a_simconnect_cfg_with_the_port_the_simulator_declares(self):
        write(self.found.base / "SimConnect.xml", sim_xml(5300))
        self.script("apply")
        self.assertEqual((self.sibling / "SimConnect.cfg").read_bytes(), b"[SimConnect]\r\nProtocol=IPv4\r\nAddress=127.0.0.1\r\nPort=5300\r\n")

    def test_an_existing_registry_is_never_overwritten_by_a_second_apply(self):
        self.script("apply")
        (self.sibling / "user.reg").write_text("what Volanta's prefix has grown\n")
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.sibling / "user.reg").read_text(), "what Volanta's prefix has grown\n")
        self.assertIn("already exists", result.stderr)

    def test_apply_without_simconnect_tcp_applied_fails_with_the_command_and_writes_no_cfg(self):
        write(self.found.base / "SimConnect.xml", sim_xml(500))
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("run: flightsim-on-linux addon apply simconnect-tcp", result.stdout)
        self.assertFalse((self.sibling / "SimConnect.cfg").exists())

    def test_undo_leaves_the_prefix_and_its_cfg_because_other_add_ons_run_there(self):
        self.script("apply")
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.sibling / "SimConnect.cfg").exists())
        self.assertTrue((self.sibling / "system.reg").exists())
        self.assertIn("rm -r", result.stdout)

    def test_undo_twice_is_harmless(self):
        self.script("apply")
        self.script("undo")
        self.assertEqual(self.script("undo").returncode, 0)


class VolantaCheckTest(VolantaCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.script("apply").returncode, 0)

    def only_failure(self, word):
        result = self.script("check")
        failures = [line for line in result.stdout.splitlines() if line.startswith("FAIL")]
        self.assertEqual((result.returncode, len(failures)), (1, 1), result.stdout)
        self.assertIn(word, failures[0])

    def test_an_applied_machine_passes_with_five_ok_lines(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok"] * 5)

    def test_no_sibling_prefix_fails_with_the_command(self):
        shutil.rmtree(self.sibling)
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("run: flightsim-on-linux addon apply volanta", result.stdout)

    def test_a_drive_c_that_is_not_the_simulators_fails(self):
        (self.sibling / "drive_c").unlink()
        (self.sibling / "drive_c").mkdir()
        self.only_failure("drive_c")

    def test_a_timestamp_that_is_not_disable_fails(self):
        (self.sibling / ".update-timestamp").write_text("1234\n")
        self.only_failure(".update-timestamp")

    def test_a_simulator_still_on_port_500_fails_with_the_exact_command(self):
        write(self.found.base / "SimConnect.xml", sim_xml(500))
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("declares port 500", result.stdout)
        self.assertIn("run: flightsim-on-linux addon apply simconnect-tcp", result.stdout)

    def test_a_cfg_for_another_port_fails(self):
        (self.sibling / "SimConnect.cfg").write_text("[SimConnect]\r\nPort=5999\r\n")
        self.only_failure("SimConnect.cfg")

    def test_a_missing_cfg_fails(self):
        (self.sibling / "SimConnect.cfg").unlink()
        self.only_failure("SimConnect.cfg")

    def test_no_simulator_base_folder_fails_instead_of_reading_the_current_folder(self):
        result = self.script("check", FSOL_BASE="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no simulator base folder", result.stdout)

    def test_check_changes_nothing(self):
        before = self.tree()
        self.script("check")
        self.assertEqual(self.tree(), before)


class VolantaInstallTest(VolantaCase):
    def setUp(self):
        super().setUp()
        self.stubs()
        self.setup = self.root / "downloads" / "volanta-1.19.2.exe"
        write(self.setup, "MZ")

    def test_it_runs_the_installer_silently_in_the_sibling_prefix_with_powershell_off(self):
        result = self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        windows = "Z:" + str(self.setup).replace("/", "\\")
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {windows} /S"])
        env = self.recorded_env()
        self.assertEqual(env["WINEPREFIX"], str(self.sibling))
        self.assertIn("powershell.exe=d", env["WINEDLLOVERRIDES"].split(";"))
        self.assertIn("d3d11=n", env["WINEDLLOVERRIDES"].split(";"))
        self.assertTrue(Path(f"{self.record}.server").exists())

    def test_it_creates_the_sibling_prefix_when_apply_was_not_run_first(self):
        self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertTrue((self.sibling / "system.reg").exists())

    def test_it_never_wipes_a_registry_that_exists(self):
        self.script("apply")
        (self.sibling / "user.reg").write_text("grown\n")
        self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual((self.sibling / "user.reg").read_text(), "grown\n")

    def test_with_volanta_open_it_refuses_and_runs_nothing(self):
        self.stand_in(self.PROGRAM)
        result = self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 1)
        self.assertIn("quit it from its tray icon", result.stderr)
        self.assertEqual(self.recorded(), [])

    def test_the_installer_exit_code_is_the_script_exit_code(self):
        self.stubs(wine_exit=2)
        self.assertEqual(self.script("install", FSOL_INSTALLER=str(self.setup)).returncode, 2)

    def test_without_an_installer_the_script_says_how_to_give_one(self):
        result = self.script("install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("flightsim-on-linux addon install volanta", result.stderr)

    def test_the_variable_that_breaks_wine_installers_does_not_reach_them(self):
        self.script("install", FSOL_INSTALLER=str(self.setup), NoDefaultCurrentDirectoryInExePath="1")
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", self.recorded_env())


class VolantaLaunchTest(VolantaCase):
    def setUp(self):
        super().setUp()
        self.stubs()
        self.install_program(self.EXE)
        self.assertEqual(self.script("apply").returncode, 0)

    def test_it_starts_in_the_sibling_prefix_from_the_folder_that_holds_simconnect_cfg_without_the_simulator(self):
        result = self.script("launch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {self.PROGRAM} --in-process-gpu --disable-gpu --disable-gpu-compositing"])
        self.assertEqual(self.recorded_cwd(), [str(self.sibling)])
        self.assertEqual(self.recorded_env()["WINEPREFIX"], str(self.sibling))

    def test_it_keeps_chromium_out_of_the_gpu_process_and_chooses_no_gpu(self):
        self.script("launch")
        self.wait_for_record()
        env = self.recorded_env()
        for name in ("VK_LOADER_DRIVERS_SELECT", "DXVK_FILTER_DEVICE_NAME", "VKD3D_FILTER_DEVICE_NAME"):
            self.assertNotIn(name, env)
        self.assertIn("--in-process-gpu", self.recorded()[0])

    def test_the_watchers_minimized_option_goes_to_volanta_before_the_chromium_options(self):
        self.script("launch", "--minimized")
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.proton}/files/bin/wine {self.PROGRAM} --minimized --in-process-gpu --disable-gpu --disable-gpu-compositing"])

    def test_the_x11_driver_and_the_dxvk_overrides_are_on_for_the_sibling_prefix(self):
        self.script("launch")
        self.wait_for_record()
        overrides = self.recorded_env()["WINEDLLOVERRIDES"].split(";")
        for word in ("winewayland.drv=d", "winex11.drv=b", "d3d11=n", "dxgi=n"):
            self.assertIn(word, overrides)

    def test_a_copy_already_running_is_left_alone(self):
        self.stand_in(self.PROGRAM)
        self.assertEqual(self.script("launch").returncode, 0)
        self.assertEqual([line for line in self.recorded() if "files/bin/wine" in line], [])

    def test_a_missing_program_says_how_to_install_it(self):
        (self.found.prefix / self.EXE).unlink()
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install volanta", result.stderr)

    def test_a_missing_sibling_prefix_says_how_to_make_it(self):
        shutil.rmtree(self.sibling)
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon apply volanta", result.stderr)


class VolantaPageTest(unittest.TestCase):
    def test_the_page_sends_the_reader_to_a_browser_for_the_windows_build_and_leaves_the_radeon_out(self):
        text = page_text("volanta")
        self.assertIn("https://api.volanta.app/api/v1/ClientUpdate/latest.yml", text)
        self.assertIn("403", text)
        for name in ("VK_LOADER_DRIVERS_SELECT", "DXVK_FILTER_DEVICE_NAME", "VKD3D_FILTER_DEVICE_NAME"):
            self.assertNotIn(name, text)


class SimlinkCase(ServiceCase):
    ID = "navigraph-simlink"
    PROGRAM = "C:\\Program Files\\Navigraph\\Simlink\\NavigraphSimlink.exe"
    HANDLER = "flightsim-on-linux-navigraph-simlink.desktop"
    SCHEME = "x-scheme-handler/navigraph-traffic-desktop"
    LINK = "navigraph-traffic-desktop://oidc_sign_in_redirect?code=SECRET-CODE"

    def setUp(self):
        super().setUp()
        self.prefix = self.data / "flightsim-on-linux" / "navigraph-simlink"
        self.folder = self.prefix / "drive_c" / "Program Files" / "Navigraph" / "Simlink"
        write(self.found.base / "SimConnect.xml", sim_xml(5111))
        write(self.data / "applications" / self.HANDLER, "[Desktop Entry]\nType=Application\nName=Simlink\nExec=/usr/bin/true %u\n")
        stub(self.root / "bin" / "xdg-mime", XDG_MIME_STUB)

    def installed(self):
        write(self.prefix / "system.reg", "x")
        write(self.folder / "NavigraphSimlink.exe", "MZ")

    def mimeapps(self):
        file = self.config / "mimeapps.list"
        return file.read_text() if file.exists() else ""

    def default_mime(self, handler):
        write(self.config / "mimeapps.list", f"[Default Applications]\n{self.SCHEME}={handler}\n")


class SimlinkApplyTest(SimlinkCase):
    def setUp(self):
        super().setUp()
        self.installed()

    def test_apply_writes_simconnect_cfg_beside_the_exe_and_makes_the_shortcut_the_link_handler(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual((self.folder / "SimConnect.cfg").read_bytes(), b"[SimConnect]\r\nProtocol=IPv4\r\nAddress=127.0.0.1\r\nPort=5111\r\n")
        self.assertIn(f"{self.SCHEME}={self.HANDLER}", self.mimeapps())

    def test_the_cfg_follows_the_port_the_simulator_declares(self):
        write(self.found.base / "SimConnect.xml", sim_xml(5300))
        self.script("apply")
        self.assertIn(b"Port=5300", (self.folder / "SimConnect.cfg").read_bytes())

    def test_apply_twice_changes_nothing_the_second_time(self):
        self.script("apply")
        result = self.script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertIn("already", result.stdout)

    def test_without_the_menu_entry_apply_asks_for_it_and_changes_nothing(self):
        (self.data / "applications" / self.HANDLER).unlink()
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("run: flightsim-on-linux addon shortcut navigraph-simlink", result.stdout)
        self.assertFalse((self.folder / "SimConnect.cfg").exists())
        self.assertEqual(self.mimeapps(), "")

    def test_without_simlink_installed_apply_says_how_to_install_it(self):
        shutil.rmtree(self.folder)
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install navigraph-simlink", result.stdout)

    def test_without_simconnect_tcp_applied_apply_fails_with_the_command_and_leaves_the_handler_alone(self):
        write(self.found.base / "SimConnect.xml", sim_xml(500))
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("run: flightsim-on-linux addon apply simconnect-tcp", result.stdout)
        self.assertEqual(self.mimeapps(), "")

    def test_an_xdg_mime_that_does_not_take_the_handler_fails_the_apply(self):
        stub(self.root / "bin" / "xdg-mime", "#!/bin/sh\nexit 0\n")
        result = self.script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("still do not open", result.stdout)

    def test_a_handler_the_reader_had_is_put_back_by_undo(self):
        self.default_mime("firefox.desktop")
        self.script("apply")
        self.assertIn(f"{self.SCHEME}={self.HANDLER}", self.mimeapps())
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{self.SCHEME}=firefox.desktop", self.mimeapps())
        self.assertNotIn(self.HANDLER, self.mimeapps())

    def test_undo_removes_the_handler_and_the_cfg_it_added_and_keeps_the_prefix(self):
        self.script("apply")
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(self.SCHEME, self.mimeapps())
        self.assertFalse((self.folder / "SimConnect.cfg").exists())
        self.assertTrue((self.folder / "NavigraphSimlink.exe").exists())
        self.assertIn("rm -r", result.stdout)

    def test_a_simconnect_cfg_the_folder_already_had_is_kept_and_put_back_by_undo(self):
        write(self.folder / "SimConnect.cfg", "[SimConnect]\r\nPort=5999\r\n")
        self.script("apply")
        self.assertIn(b"Port=5111", (self.folder / "SimConnect.cfg").read_bytes())
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.folder / "SimConnect.cfg").read_bytes(), b"[SimConnect]\r\nPort=5999\r\n")
        self.assertFalse((self.found.compatdata / "flightsim-on-linux-backup" / "navigraph-simlink" / "cfg").exists())

    def test_the_manifest_says_the_fix_touches_a_file_of_the_add_on(self):
        self.assertTrue(addons.load(ROOT / "addons" / self.ID).touches_addon_files)

    def test_undo_leaves_another_programs_handler_alone(self):
        self.default_mime("firefox.desktop")
        result = self.script("undo")
        self.assertEqual(result.returncode, 0)
        self.assertIn(f"{self.SCHEME}=firefox.desktop", self.mimeapps())
        self.assertIn("was not set for those links by this add-on", result.stdout)

    @unittest.skipUnless(shutil.which("xdg-mime"), "needs xdg-mime")
    def test_the_real_xdg_mime_takes_the_handler_in_and_out_of_a_scratch_config_home(self):
        (self.root / "bin" / "xdg-mime").unlink()
        self.config.mkdir()
        env = {"PATH": os.environ["PATH"]}
        for name in ("HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "FSOL_PREFIX", "FSOL_COMPATDATA", "FSOL_PROTON", "FSOL_BASE", "FSOL_CACHE", "FSOL_CLI"):
            env[name] = self.environment()[name]
        for action in ("apply", "undo"):
            result = subprocess.run([str(self.addon / action)], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if action == "apply":
                self.assertEqual(subprocess.run(["xdg-mime", "query", "default", self.SCHEME], env=env, capture_output=True, text=True).stdout.strip(), self.HANDLER)
        self.assertNotIn(self.HANDLER, self.mimeapps())


class SimlinkCheckTest(SimlinkCase):
    def setUp(self):
        super().setUp()
        self.installed()
        self.system_wine()
        self.assertEqual(self.script("apply").returncode, 0)

    def only_failure(self, word):
        result = self.script("check")
        failures = [line for line in result.stdout.splitlines() if line.startswith("FAIL")]
        self.assertEqual((result.returncode, len(failures)), (1, 1), result.stdout)
        self.assertIn(word, failures[0])

    def test_an_applied_machine_passes_with_six_ok_lines(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok"] * 6)

    def test_no_wine_fails(self):
        tools = self.root / "tools"
        tools.mkdir()
        for name in ("dirname", "readlink", "python3", "env", "xdg-mime", "cut", "tr", "head", "grep", "cat", "bash", "sh"):
            path = shutil.which(name) if name != "xdg-mime" else str(self.root / "bin" / "xdg-mime")
            os.symlink(path, tools / name)
        result = self.script("check", PATH=str(tools))
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL Wine is not installed", result.stdout)

    def test_a_missing_exe_fails_with_the_install_command(self):
        (self.folder / "NavigraphSimlink.exe").unlink()
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("run: flightsim-on-linux addon install navigraph-simlink", result.stdout)

    def test_a_cfg_for_another_port_fails(self):
        (self.folder / "SimConnect.cfg").write_text("[SimConnect]\r\nPort=5999\r\n")
        self.only_failure("SimConnect.cfg")

    def test_a_missing_cfg_fails(self):
        (self.folder / "SimConnect.cfg").unlink()
        self.only_failure("SimConnect.cfg")

    def test_a_simulator_still_on_port_500_fails_with_the_command(self):
        write(self.found.base / "SimConnect.xml", sim_xml(500))
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("run: flightsim-on-linux addon apply simconnect-tcp", result.stdout)

    def test_a_simulator_on_a_port_the_watcher_does_not_watch_fails_and_names_the_setting(self):
        write(self.found.base / "SimConnect.xml", sim_xml(5300))
        (self.folder / "SimConnect.cfg").write_text("[SimConnect]\r\nPort=5300\r\n")
        self.only_failure("watcher.health.connected_to")

    def test_a_sign_in_link_that_goes_to_another_program_fails(self):
        self.default_mime("firefox.desktop")
        self.only_failure("sign-in link goes nowhere")

    def test_check_changes_nothing(self):
        before = self.tree()
        self.script("check")
        self.assertEqual(self.tree(), before)


class SimlinkInstallTest(SimlinkCase):
    def setUp(self):
        super().setUp()
        self.system_wine()
        self.setup = self.root / "downloads" / "Navigraph+Simlink.exe"
        write(self.setup, "MZ")

    def test_a_new_prefix_is_initialised_and_the_installer_runs_silently_in_it(self):
        result = self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.root}/bin/wine wineboot -i", f"{self.root}/bin/wine {self.setup} /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /LOG=C:\\simlink-install.log"])
        env = self.recorded_env()
        self.assertEqual(env["WINEPREFIX"], str(self.prefix))
        self.assertEqual(env["WINEDLLOVERRIDES"], "winemenubuilder.exe=d")
        self.assertTrue(Path(f"{self.record}.server").exists())

    def test_an_existing_prefix_is_not_initialised_again(self):
        write(self.prefix / "system.reg", "x")
        self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(len(self.recorded()), 1)

    def test_it_uses_the_system_wine_and_nothing_of_the_simulators_prefix(self):
        self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertTrue(all(line.startswith(f"{self.root}/bin/wine") for line in self.recorded()))
        self.assertNotEqual(self.recorded_env()["WINEPREFIX"], str(self.found.prefix))

    def test_simlink_open_stops_the_installer_before_it_runs(self):
        self.stand_in(self.PROGRAM)
        result = self.script("install", FSOL_INSTALLER=str(self.setup))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Simlink is open", result.stderr)
        self.assertEqual(self.recorded(), [])

    def test_the_installer_exit_code_is_the_script_exit_code(self):
        write(self.prefix / "system.reg", "x")
        self.system_wine(wine_exit=5)
        self.assertEqual(self.script("install", FSOL_INSTALLER=str(self.setup)).returncode, 5)

    def test_the_variable_that_breaks_wine_installers_does_not_reach_them(self):
        self.script("install", FSOL_INSTALLER=str(self.setup), NoDefaultCurrentDirectoryInExePath="1")
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", self.recorded_env())

    def test_without_an_installer_the_script_says_how_to_give_one(self):
        result = self.script("install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("flightsim-on-linux addon install navigraph-simlink", result.stderr)


class SimlinkLaunchTest(SimlinkCase):
    def setUp(self):
        super().setUp()
        self.installed()
        self.system_wine(after=f'echo "token {self.LINK}"\n')

    def test_it_starts_in_the_simlink_prefix_from_the_exe_folder_with_software_qt(self):
        result = self.script("launch", NoDefaultCurrentDirectoryInExePath="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.root}/bin/wine {self.PROGRAM}"])
        self.assertEqual(self.recorded_cwd(), [str(self.folder)])
        env = self.recorded_env()
        self.assertEqual((env["WINEPREFIX"], env["QT_OPENGL"], env["WINEDLLOVERRIDES"]), (str(self.prefix), "software", "winemenubuilder.exe=d"))
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", env)

    def test_its_output_is_thrown_away_because_simlink_prints_account_tokens(self):
        result = self.script("launch", self.LINK)
        self.wait_for_record()
        time.sleep(0.2)
        self.assertNotIn("SECRET-CODE", result.stdout + result.stderr)
        self.assertEqual([path for path in (self.root / "cache").rglob("*") if path.is_file()], [])

    def test_the_sign_in_link_is_handed_to_a_second_copy_even_when_simlink_is_open(self):
        self.stand_in(self.PROGRAM)
        result = self.script("launch", self.LINK)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.root}/bin/wine {self.PROGRAM} {self.LINK}"])

    def test_the_sign_in_link_is_never_printed(self):
        result = self.script("launch", self.LINK)
        self.assertNotIn("SECRET-CODE", result.stdout + result.stderr)

    def test_an_argument_that_is_not_the_sign_in_link_is_refused(self):
        result = self.script("launch", "--evil")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a navigraph-traffic-desktop: link", result.stderr)
        self.assertFalse(any("bin/wine" in line for line in self.recorded()))

    def test_without_a_link_a_copy_already_running_is_left_alone(self):
        self.stand_in(self.PROGRAM)
        self.assertEqual(self.script("launch").returncode, 0)
        self.assertFalse(any(line.startswith(f"{self.root}/bin/wine") for line in self.recorded()))

    def test_it_does_not_need_the_simulator(self):
        self.assertEqual(self.script("launch", FSOL_SIM_RUNNING="0").returncode, 0)

    def test_a_missing_program_says_how_to_install_it(self):
        (self.folder / "NavigraphSimlink.exe").unlink()
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install navigraph-simlink", result.stderr)


class SimlinkRestartTest(SimlinkCase):
    def setUp(self):
        super().setUp()
        self.installed()

    def test_the_program_is_closed_by_force_in_its_own_prefix_only_and_then_started_again(self):
        program = self.stand_in(self.PROGRAM)
        self.system_wine(after=f"[ \"$1\" = taskkill ] && kill {program.pid}\n")
        result = self.script("restart")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record(2)
        self.assertEqual(self.recorded(), [f"{self.root}/bin/wine taskkill /f /im NavigraphSimlink.exe", f"{self.root}/bin/wine {self.PROGRAM}"])
        self.assertEqual(self.recorded_env()["WINEPREFIX"], str(self.prefix))
        self.assertNotIn(str(self.found.prefix), " ".join(self.recorded()))

    def test_a_program_that_stays_after_the_forced_close_is_not_started_twice(self):
        self.stand_in(self.PROGRAM)
        self.system_wine()
        result = self.script("restart")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not starting a second copy", result.stderr)
        self.assertEqual(self.recorded()[:1], [f"{self.root}/bin/wine taskkill /f /im NavigraphSimlink.exe"])
        self.assertFalse(any(line.endswith(self.PROGRAM) for line in self.recorded()))


class SimlinkAutostartTest(SimlinkCase):
    def entry(self):
        return self.config / "autostart" / self.HANDLER

    def test_it_writes_one_login_entry_that_launches_through_the_cli(self):
        result = self.script("autostart")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.entry().read_text()
        self.assertIn(f'Exec="{self.root}/cli" addon launch navigraph-simlink launch', text)
        self.assertIn("X-FlightsimOnLinux-Addon=navigraph-simlink", text)
        if shutil.which("desktop-file-validate"):
            check = subprocess.run(["desktop-file-validate", str(self.entry())], capture_output=True, text=True)
            self.assertEqual((check.returncode, check.stdout + check.stderr), (0, ""))

    def test_writing_it_twice_leaves_one_file(self):
        self.script("autostart")
        self.script("autostart")
        self.assertEqual(len(list((self.config / "autostart").iterdir())), 1)

    def test_remove_takes_out_only_the_entry_it_wrote(self):
        self.script("autostart")
        other = self.config / "autostart" / "other.desktop"
        write(other, "[Desktop Entry]\n")
        result = self.script("autostart", "--remove")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.entry().exists())
        self.assertTrue(other.exists())

    def test_remove_leaves_a_file_of_the_same_name_that_this_add_on_did_not_write(self):
        write(self.entry(), "[Desktop Entry]\nName=mine\n")
        result = self.script("autostart", "--remove")
        self.assertEqual(result.returncode, 0)
        self.assertIn("was not written by this add-on", result.stdout)
        self.assertTrue(self.entry().exists())

    def test_it_needs_to_run_through_the_cli(self):
        result = self.script("autostart", FSOL_CLI="")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.entry().exists())

    def test_an_unknown_option_is_refused(self):
        self.assertEqual(self.script("autostart", "--now").returncode, 2)


class TouchedFilesPageTest(unittest.TestCase):
    def test_the_pages_of_the_add_ons_that_change_a_file_of_the_program_say_so_at_the_top_with_the_undo_command(self):
        for name in ("sayintentions", "navigraph-simlink"):
            top = page_text(name)[:page_text(name).index("<h2>Get it</h2>")]
            self.assertIn('<span class="tag warn">touches add-on files</span>', top, name)
            self.assertIn(f"addon undo {name}", top, name)

    def test_the_add_on_list_marks_the_three_that_touch_files_with_the_warning_tag(self):
        rows = {row.split("</td>")[0]: row for row in page_text("addons").split("<tr>")}
        for title in ("SayIntentions.AI (5.6.5)", "Navigraph Simlink (1.1.41)", "OpenDeck for Stream Deck (2.14.2)"):
            row = next(row for key, row in rows.items() if key.endswith(title))
            self.assertIn('<span class="tag warn">touches add-on files</span>', row, title)


class DispatchCase(ServiceCase):
    ID = "flightsim-dispatch"
    PROGRAM = "C:\\FlightSimDispatch\\FlightSimDispatch.exe"

    def setUp(self):
        super().setUp()
        self.prefix = self.data / "flightsim-on-linux" / "flightsim-dispatch"
        self.folder = self.prefix / "drive_c" / "FlightSimDispatch"

    def make_zip(self, name="FlightSimDispatch.1.5.0.2.zip", files=None):
        path = self.root / "downloads" / name
        path.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            for member, text in (files or {"FlightSimDispatch.exe": "new exe", "FlightDB.db": "new db", "Data/Airline/AAA.txt": "x", "Readme.txt": "r"}).items():
                archive.writestr(member, text)
        return path

    def installed(self):
        write(self.prefix / "system.reg", "x")
        write(self.folder / "FlightSimDispatch.exe", "old exe")
        write(self.folder / "FlightDB.db", "old db")
        write(self.folder / "old-only.txt", "left over")


class DispatchTest(DispatchCase):
    def test_apply_and_undo_say_there_is_nothing_to_do_and_change_nothing(self):
        before = self.tree()
        for name in ("apply", "undo"):
            result = self.script(name)
            self.assertEqual(result.returncode, 0)
            self.assertIn("already nothing to", result.stdout)
        self.assertEqual(self.tree(), before)

    def test_check_passes_with_the_program_the_database_and_wine(self):
        self.installed()
        self.system_wine()
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["ok"] * 3)

    def test_check_fails_without_the_database_and_names_the_install_command(self):
        self.installed()
        self.system_wine()
        (self.folder / "FlightDB.db").unlink()
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL", result.stdout)
        self.assertIn("addon install flightsim-dispatch", result.stdout)

    def test_check_changes_nothing(self):
        self.installed()
        self.system_wine()
        before = self.tree()
        self.script("check")
        self.assertEqual(self.tree(), before)


class DispatchInstallTest(DispatchCase):
    def setUp(self):
        super().setUp()
        self.system_wine()

    def test_a_new_prefix_is_initialised_and_the_zip_is_unzipped_into_the_programs_folder(self):
        archive = self.make_zip()
        result = self.script("install", FSOL_INSTALLER=str(archive))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"{self.root}/bin/wine wineboot -i"])
        self.assertEqual(self.recorded_env()["WINEDLLOVERRIDES"], "winemenubuilder.exe=d")
        self.assertEqual((self.folder / "FlightSimDispatch.exe").read_text(), "new exe")
        self.assertEqual((self.folder / "Data" / "Airline" / "AAA.txt").read_text(), "x")
        self.assertFalse(Path(f"{self.folder}.new").exists())

    def test_an_update_replaces_the_whole_folder_as_the_zip_itself_asks(self):
        self.installed()
        self.script("install", FSOL_INSTALLER=str(self.make_zip()))
        self.assertEqual((self.folder / "FlightSimDispatch.exe").read_text(), "new exe")
        self.assertEqual((self.folder / "FlightDB.db").read_text(), "new db")
        self.assertFalse((self.folder / "old-only.txt").exists())
        self.assertEqual(self.recorded(), [])

    def test_a_zip_that_is_not_the_program_leaves_the_installed_folder_as_it_was(self):
        self.installed()
        archive = self.make_zip(files={"something.txt": "else"})
        result = self.script("install", FSOL_INSTALLER=str(archive))
        self.assertEqual(result.returncode, 1)
        self.assertIn("has no FlightSimDispatch.exe", result.stderr)
        self.assertEqual((self.folder / "FlightSimDispatch.exe").read_text(), "old exe")
        self.assertTrue((self.folder / "old-only.txt").exists())
        self.assertFalse(Path(f"{self.folder}.new").exists())

    def test_a_running_copy_stops_the_install_before_anything_is_touched(self):
        self.installed()
        self.stand_in(self.PROGRAM)
        result = self.script("install", FSOL_INSTALLER=str(self.make_zip()))
        self.assertEqual(result.returncode, 1)
        self.assertIn("is open", result.stderr)
        self.assertEqual((self.folder / "FlightSimDispatch.exe").read_text(), "old exe")

    def test_without_a_zip_the_script_says_how_to_give_one(self):
        result = self.script("install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("flightsim-on-linux addon install flightsim-dispatch", result.stderr)


class DispatchLaunchTest(DispatchCase):
    def setUp(self):
        super().setUp()
        self.installed()
        self.system_wine()

    def test_it_starts_from_the_programs_folder_in_its_own_prefix_and_needs_no_simulator(self):
        result = self.script("launch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for_record()
        self.assertEqual(self.recorded(), [f"{self.root}/bin/wine {self.PROGRAM}"])
        self.assertEqual(self.recorded_cwd(), [str(self.folder)])
        self.assertEqual(self.recorded_env()["WINEPREFIX"], str(self.prefix))
        self.assertTrue((self.root / "cache" / "logs" / "flightsim-dispatch.log").is_file())

    def test_a_second_copy_is_not_started(self):
        self.stand_in(self.PROGRAM)
        self.assertEqual(self.script("launch").returncode, 0)
        self.assertFalse(any(line.startswith(f"{self.root}/bin/wine") for line in self.recorded()))

    def test_a_missing_program_says_how_to_install_it(self):
        (self.folder / "FlightSimDispatch.exe").unlink()
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install flightsim-dispatch", result.stderr)


class DispatchPageTest(unittest.TestCase):
    def test_the_page_says_the_mesa_warnings_are_harmless(self):
        text = page_text("flightsim-dispatch")
        self.assertIn("failed to create dri2 screen", text)


if __name__ == "__main__":
    unittest.main()
