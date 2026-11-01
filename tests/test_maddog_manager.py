import codecs
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addons" / "maddog-md80"
sys.path.insert(0, str(ADDON / "manager"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from maddog_manager import liveries, options  # noqa: E402
from maddog_manager.ini import IniFile  # noqa: E402
from maddog_manager.layout import FILETIME_EPOCH, build_layout, write_layout  # noqa: E402
from maddog_manager.liveries import Livery  # noqa: E402
from maddog_manager.locations import MaddogFiles, MaddogNotFound, machine, windows_to_linux  # noqa: E402
from maddog_manager.registration import registered  # noqa: E402
from maddog_manager.sim import simulator_running  # noqa: E402

QT = importlib.util.find_spec("PyQt6") is not None
if QT:
    from PyQt6.QtCore import QEventLoop, QTimer
    from PyQt6.QtNetwork import QNetworkAccessManager
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from maddog_manager import liveries_page
    from maddog_manager.liveries_page import LiveriesPage
    from maddog_manager.net import Fetch, FileDownload
    from maddog_manager.options_page import OptionsPage

needs_qt = unittest.skipUnless(QT, "needs PyQt6")
BODY = bytes(range(256)) * 400


def make_install(root, maddogx=b"[SYSTEM]\r\nCrew=Maddog\r\n"):
    root = Path(root)
    prefix = root / "pfx"
    (prefix / "dosdevices").mkdir(parents=True)
    (prefix / "drive_c").mkdir()
    os.symlink("../drive_c", prefix / "dosdevices" / "c:")
    os.symlink(str(root), prefix / "dosdevices" / "z:")
    community = root / "Community"
    package = community / "lsh-maddogx-aircraft"
    (package / "Data").mkdir(parents=True)
    (package / "Data" / "maddogx.ini").write_bytes(maddogx)
    for name in ("cold and dark.mdx", "cold and dark open.mdx", "ready at gate.mdx", "ready for takeoff.mdx"):
        (package / "Data" / name).write_text(name)
    (package / "Data" / "maddogx.mdx").write_text("last flight state")
    maddog = prefix / "drive_c/users/steamuser/AppData/Local/Maddog X Files"
    (maddog / "MSFS 2024" / "Data").mkdir(parents=True)
    (maddog / "Data").mkdir()
    (maddog / "Crew").mkdir()
    (maddog / "Crew" / "Maddog.pck").write_bytes(b"")
    (maddog / "Crew" / "Maddog.ini").write_bytes(b"")
    work = prefix / "drive_c/users/steamuser/AppData/Roaming/Microsoft Flight Simulator 2024/WASM/MSFS2024/lsh-maddogx-aircraft/work"
    work.mkdir(parents=True)
    lm = (
        "[GENERAL]\r\nVersion=2.1 b283\r\n"
        f"FSPath=Z:\\Community\r\n"
        "WorkPath=C:\\users\\steamuser\\AppData\\Roaming\\Microsoft Flight Simulator 2024\\WASM\\MSFS2024\\lsh-maddogx-aircraft\\work\r\n"
    )
    (maddog / "MSFS 2024" / "Data" / "LM.ini").write_bytes(lm.encode("cp1252"))
    (maddog / "manager.ini").write_bytes(b"[MANAGER]\r\nSim=1\r\nPanelState=0\r\nChanged=0\r\n")
    return MaddogFiles.discover(prefix), work


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        server = self.server
        server.requests.append((self.path, self.headers.get("Range")))
        body = server.files.get(self.path)
        if body is None:
            self.send_error(404)
            return
        start = 0
        ranged = self.headers.get("Range")
        if ranged and server.honor_range:
            start = int(ranged.removeprefix("bytes=").split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body) - start))
        self.end_headers()
        chunk = 4096
        for offset in range(start, len(body), chunk):
            try:
                self.wfile.write(body[offset:offset + chunk])
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            if server.delay:
                time.sleep(server.delay)


class LocalServer:
    def __init__(self, files, honor_range=True, delay=0.0):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.files = files
        self.httpd.honor_range = honor_range
        self.httpd.delay = delay
        self.httpd.requests = []
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def requests(self):
        return self.httpd.requests

    def url(self, path):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}{path}"


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


APPLICATION = []


def application():
    if not APPLICATION:
        APPLICATION.append(QApplication.instance() or QApplication(["maddog-manager-tests"]))
    return APPLICATION[0]


def wait_until(condition, timeout=10000):
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: condition() and loop.quit())
    QTimer.singleShot(timeout, loop.quit)
    timer.start()
    if not condition():
        loop.exec()
    timer.stop()
    return condition()


class LocationsTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.prefix = self.root / "games" / "pfx"
        dosdevices = self.prefix / "dosdevices"
        dosdevices.mkdir(parents=True)
        (self.prefix / "drive_c").mkdir()
        os.symlink("../drive_c", dosdevices / "c:")
        os.symlink(str(self.root), dosdevices / "z:")

    def make_maddog(self, fs_path, user="steamuser"):
        root = self.prefix / "drive_c/users" / user / "AppData/Local/Maddog X Files"
        (root / "MSFS 2024/Data").mkdir(parents=True)
        (root / "MSFS 2024/Data/LM.ini").write_bytes(f"[GENERAL]\r\nFSPath={fs_path}\r\n".encode("cp1252"))
        (root / "manager.ini").write_bytes(b"[MANAGER]\r\nSim=1\r\n")
        return root

    def test_converts_drive_letters_through_dosdevices(self):
        (self.root / "games/Flight Simulator 2024/Community").mkdir(parents=True)
        path = windows_to_linux(self.prefix, "Z:\\games\\flight simulator 2024\\Community")
        self.assertEqual(path, self.root / "games/Flight Simulator 2024/Community")
        self.assertEqual(windows_to_linux(self.prefix, "c:\\users"), (self.prefix / "drive_c/users").resolve())
        with self.assertRaises(ValueError):
            windows_to_linux(self.prefix, "no drive")

    def test_discovers_every_maddog_folder(self):
        community = self.root / "games/Community"
        community.mkdir(parents=True)
        root = self.make_maddog("Z:\\games\\Community")
        files = MaddogFiles.discover(self.prefix)
        self.assertEqual(files.root, root)
        self.assertEqual(files.community, community)
        self.assertEqual(files.manager_ini, root / "manager.ini")
        self.assertEqual(files.livery_dir, community / "lsh-maddogx-aircraft/SimObjects/Airplanes/MaddogX20thNV/liveries/lsh")
        self.assertEqual(files.aircrafts_dir, community / "lsh-maddogx-aircraft/Aircrafts")

    def test_finds_the_maddog_folder_of_any_wine_user(self):
        community = self.root / "games/Community"
        community.mkdir(parents=True)
        root = self.make_maddog("Z:\\games\\Community", user="pilot")
        self.assertEqual(MaddogFiles.discover(self.prefix).root, root)

    def test_complains_when_the_community_folder_is_missing_and_none_was_given(self):
        self.make_maddog("Z:\\missing\\folder")
        with self.assertRaisesRegex(MaddogNotFound, "no Community folder was given"):
            MaddogFiles.discover(self.prefix)

    def test_the_simulator_community_stands_in_when_the_load_manager_folder_does_not_exist(self):
        community = self.root / "simulator/Community"
        community.mkdir(parents=True)
        self.make_maddog("Z:\\missing\\folder")
        self.assertEqual(MaddogFiles.discover(self.prefix, community).community, community)

    def test_the_load_manager_folder_wins_when_it_exists(self):
        vendor = self.root / "vendor/Community"
        simulator = self.root / "simulator/Community"
        vendor.mkdir(parents=True)
        simulator.mkdir(parents=True)
        self.make_maddog("Z:\\vendor\\Community")
        self.assertEqual(MaddogFiles.discover(self.prefix, simulator).community, vendor)

    def test_a_simulator_community_that_does_not_exist_is_no_help(self):
        self.make_maddog("Z:\\missing\\folder")
        with self.assertRaises(MaddogNotFound):
            MaddogFiles.discover(self.prefix, self.root / "nowhere")

    def test_complains_when_the_prefix_has_no_maddog(self):
        with self.assertRaisesRegex(MaddogNotFound, "install the Maddog first"):
            MaddogFiles.discover(self.prefix)


class Completed:
    def __init__(self, stdout):
        self.stdout = stdout


class MachineTest(unittest.TestCase):
    def runner(self, data):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if isinstance(data, Exception):
                raise data
            return Completed(json.dumps(data))

        return run, calls

    def test_the_fsol_variables_are_enough_and_the_cli_is_not_run(self):
        run, calls = self.runner({"prefix": "/other", "community": "/other"})
        found = machine({"FSOL_PREFIX": "/pfx", "FSOL_COMMUNITY": "/community"}, run)
        self.assertEqual(found, {"prefix": "/pfx", "community": "/community"})
        self.assertEqual(calls, [])

    def test_a_missing_variable_is_filled_in_from_machine_json_of_the_named_cli(self):
        run, calls = self.runner({"prefix": "/from/json", "community": "/community/from/json"})
        found = machine({"FSOL_PREFIX": "/pfx", "FSOL_CLI": "/some/cli"}, run)
        self.assertEqual(found, {"prefix": "/pfx", "community": "/community/from/json"})
        self.assertEqual(calls, [["/some/cli", "machine", "--json"]])

    def test_without_variables_the_cli_of_this_repository_is_the_one_that_runs(self):
        run, calls = self.runner({"prefix": "/from/json", "community": "/community/from/json"})
        self.assertEqual(machine({}, run), {"prefix": "/from/json", "community": "/community/from/json"})
        self.assertEqual(calls, [[str(ROOT / "bin" / "flightsim-on-linux"), "machine", "--json"]])
        self.assertTrue((ROOT / "bin" / "flightsim-on-linux").is_file())

    def test_a_cli_that_cannot_run_leaves_the_values_empty(self):
        run, _ = self.runner(OSError("no such file"))
        self.assertEqual(machine({}, run), {"prefix": "", "community": ""})
        run, _ = self.runner(subprocess.CalledProcessError(1, "cli"))
        self.assertEqual(machine({}, run), {"prefix": "", "community": ""})

    def test_discover_without_a_prefix_asks_the_machine_for_both_folders(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        files, _ = make_install(folder.name)
        run, calls = self.runner({"prefix": str(files.prefix), "community": str(files.simulator_community or files.community)})
        found = MaddogFiles.discover(environ={"FSOL_CLI": "/cli"}, run=run)
        self.assertEqual(found.prefix, files.prefix)
        self.assertEqual(calls, [["/cli", "machine", "--json"]])

    def test_discover_says_to_run_machine_when_nothing_is_found(self):
        run, _ = self.runner(OSError("no cli"))
        with self.assertRaisesRegex(MaddogNotFound, "flightsim-on-linux machine"):
            MaddogFiles.discover(environ={}, run=run)

    def test_an_explicit_prefix_never_runs_the_cli(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        files, _ = make_install(folder.name)
        run, calls = self.runner(OSError("must not run"))
        MaddogFiles.discover(files.prefix, run=run)
        self.assertEqual(calls, [])


class SimulatorTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.proc = Path(self.folder.name)

    def process(self, name, command):
        (self.proc / name).mkdir()
        (self.proc / name / "cmdline").write_bytes(command)

    def test_the_simulator_is_found_by_the_first_word_of_a_command_line(self):
        self.process("100", b"C:\\windows\\system32\\other.exe\0")
        self.assertFalse(simulator_running(self.proc))
        self.process("200", b"S:\\steamapps\\common\\MSFS2024\\FlightSimulator2024.exe\0-FastLaunch\0")
        self.assertTrue(simulator_running(self.proc))

    def test_the_name_in_a_later_argument_does_not_count(self):
        self.process("100", b"/usr/bin/tail\0FlightSimulator2024.exe\0")
        self.assertFalse(simulator_running(self.proc))

    def test_entries_that_are_not_processes_are_skipped(self):
        (self.proc / "self").mkdir()
        self.process("300", b"")
        self.assertFalse(simulator_running(self.proc))


class RegistrationTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.files, _ = make_install(self.folder.name)

    def register(self, text):
        self.files.registration_ini.write_bytes(text.encode())
        return registered(self.files)

    def test_a_key_of_16_letters_and_digits_with_a_purchase_code_counts(self):
        self.assertTrue(self.register("[REGISTRATION]\r\nKey=ABCD-EFGH-JKLM-NPQR\r\nPurchaseCode=TEST\r\n"))

    def test_a_short_key_an_empty_purchase_code_or_a_missing_file_do_not(self):
        self.assertFalse(self.register("[REGISTRATION]\r\nKey=ABCD\r\nPurchaseCode=TEST\r\n"))
        self.assertFalse(self.register("[REGISTRATION]\r\nKey=ABCD-EFGH-JKLM-NPQR\r\nPurchaseCode=\r\n"))
        self.files.registration_ini.unlink()
        self.assertFalse(registered(self.files))


class NothingPersonalTest(unittest.TestCase):
    FORBIDDEN = ("/home/", "brunom", "Jogos", "SteamLibrary", "Z:\\home", "WAYLAND_DISPLAY", "XAUTHORITY", ".local/share/Steam")

    def test_no_file_of_the_add_on_names_a_personal_path_or_a_display_variable(self):
        found = []
        for file in sorted(ADDON.rglob("*")):
            if not file.is_file() or "__pycache__" in file.parts:
                continue
            text = file.read_text(errors="replace")
            found += [f"{file.relative_to(ADDON)}: {word}" for word in self.FORBIDDEN if word in text]
        self.assertEqual(found, [])

    def test_the_manager_has_no_portuguese_left_in_its_text(self):
        words = ("não", "nenhum", "Instalar", "Remover", "Opções", "Início", "pasta ", "Falha")
        found = []
        for file in sorted((ADDON / "manager").rglob("*.py")):
            text = file.read_text()
            found += [f"{file.name}: {word}" for word in words if word in text]
        self.assertEqual(found, [])


class IniTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "maddogx.ini"

    def tearDown(self):
        self.folder.cleanup()

    def write(self, data):
        self.path.write_bytes(data)

    def test_reads_sections_and_keys_without_case(self):
        self.write(b'[Selection]\r\nrequired_tags="MD83"\r\n[FLTSIM.0]\r\natc_id = "N944AS" ; registration\r\n')
        ini = IniFile(self.path)
        self.assertEqual(ini.get("SELECTION", "Required_Tags"), "MD83")
        self.assertEqual(ini.get("fltsim.0", "ATC_ID"), "N944AS")
        self.assertEqual(ini.get("GENERAL", "name", "none"), "none")

    def test_skips_comments_and_stops_at_next_section(self):
        self.write(b"[A]\r\n;x=1\r\ny=2\r\n[B]\r\nx=3\r\n")
        ini = IniFile(self.path)
        self.assertFalse(ini.has("A", "x"))
        self.assertEqual(ini.get_int("A", "y"), 2)
        self.assertEqual(ini.get_int("B", "x"), 3)

    def test_numbers_and_booleans(self):
        self.write(b"[S]\r\na=1\r\nb=0\r\nc=true\r\nd=abc\r\n")
        ini = IniFile(self.path)
        self.assertTrue(ini.get_bool("S", "a"))
        self.assertFalse(ini.get_bool("S", "b"))
        self.assertTrue(ini.get_bool("S", "c"))
        self.assertFalse(ini.get_bool("S", "d"))
        self.assertEqual(ini.get_int("S", "d", 7), 7)
        self.assertEqual(ini.get_int("S", "missing", 60), 60)

    def test_changes_a_key_and_keeps_every_other_byte(self):
        original = b"[SYSTEM]\r\nPilot=Jos\xe9 \x81\r\nWindowShades =0\r\n\r\n[SOUND]\r\nVolAC=100\r\n"
        self.write(original)
        ini = IniFile(self.path)
        ini.set_int("system", "windowshades", 2)
        ini.save()
        self.assertEqual(self.path.read_bytes(), original.replace(b"WindowShades =0", b"WindowShades =2"))
        self.assertEqual(IniFile(self.path).get("SYSTEM", "Pilot"), "José \udc81")

    def test_adds_missing_key_at_end_of_its_section(self):
        self.write(b"[SYSTEM]\r\nCrew=Maddog\r\n\r\n[SOUND]\r\nVolAC=100\r\n")
        ini = IniFile(self.path)
        ini.set("SYSTEM", "WXdata", 1)
        ini.set("PILOT", "CM1", "Pilot")
        ini.save()
        self.assertEqual(self.path.read_bytes(), b"[SYSTEM]\r\nCrew=Maddog\r\nWXdata=1\r\n\r\n[SOUND]\r\nVolAC=100\r\n[PILOT]\r\nCM1=Pilot\r\n")

    def test_creates_a_missing_file(self):
        ini = IniFile(self.path)
        ini.set("PUSHOVER", "Token", "abc")
        ini.save()
        self.assertEqual(self.path.read_bytes(), b"[PUSHOVER]\r\nToken=abc\r\n")

    def test_does_not_write_when_nothing_changed(self):
        self.write(b"[MANAGER]\r\nSim=1\r\n")
        ini = IniFile(self.path)
        ini.set_int("MANAGER", "Sim", 1)
        self.path.write_bytes(b"outro")
        ini.save()
        self.assertEqual(self.path.read_bytes(), b"outro")

    def test_keeps_utf16_files_in_utf16(self):
        self.write(codecs.BOM_UTF16_LE + "[A]\r\nname=Café\r\n".encode("utf-16-le"))
        ini = IniFile(self.path)
        self.assertEqual(ini.get("A", "name"), "Café")
        ini.set("A", "name", "Crème")
        ini.save()
        self.assertEqual(self.path.read_bytes(), codecs.BOM_UTF16_LE + "[A]\r\nname=Crème\r\n".encode("utf-16-le"))


MTIME_NS = 1_700_000_000_123_456_789


class LayoutTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.package = Path(self.folder.name) / "lsh-maddogx-aircraft"
        files = {
            "Data/b.ini": b"12",
            "Data/A.mdx": b"1",
            "aircrafts/x.mda": b"123",
            "SimObjects/Airplanes/P/liveries/lsh/N1/livery.cfg": b"abcd",
            "_CVT_/cache.bin": b"zzzzzzzz",
            "layout.json": b"{}",
            "manifest.json": b'{\r\n  "title": "Maddog",\r\n  "total_package_size": "00000000000000000000"\r\n}',
        }
        for name, data in files.items():
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            os.utime(path, ns=(MTIME_NS, MTIME_NS))

    def tearDown(self):
        self.folder.cleanup()

    def test_lists_files_level_by_level_in_ntfs_order(self):
        content, _ = build_layout(self.package)
        self.assertEqual([entry["path"] for entry in content], [
            "aircrafts/x.mda",
            "Data/A.mdx",
            "Data/b.ini",
            "SimObjects/Airplanes/P/liveries/lsh/N1/livery.cfg",
        ])

    def test_dates_are_windows_filetimes_of_the_mtime(self):
        content, _ = build_layout(self.package)
        self.assertEqual(content[0]["date"], MTIME_NS // 100 + FILETIME_EPOCH)
        self.assertEqual(content[0]["size"], 3)

    def test_writes_layout_and_package_size(self):
        write_layout(self.package)
        layout = (self.package / "layout.json").read_bytes()
        self.assertNotIn(b"\r\n", layout)
        self.assertFalse(layout.endswith(b"\n"))
        self.assertEqual(len(json.loads(layout)["content"]), 4)
        manifest = (self.package / "manifest.json").read_bytes()
        self.assertIn(b"\r\n", manifest)
        manifest_size = len(manifest)
        expected = 3 + 1 + 2 + 4 + manifest_size + len(layout)
        self.assertEqual(json.loads(manifest)["total_package_size"], str(expected).zfill(20))
        self.assertEqual(json.loads(manifest)["title"], "Maddog")


class RealPackageTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("FSOL_PREFIX"), "set FSOL_PREFIX to compare with the layout.json the installer wrote")
    def test_matches_the_layout_the_installer_wrote(self):
        try:
            files = MaddogFiles.discover(os.environ["FSOL_PREFIX"], os.environ.get("FSOL_COMMUNITY"))
        except MaddogNotFound as error:
            self.skipTest(str(error))
        package = files.layout_package
        layout_path = package / "layout.json"
        written_at = layout_path.stat().st_mtime_ns
        existing = json.loads(layout_path.read_text(encoding="utf-8"))["content"]
        content, _ = build_layout(package)
        untouched = {entry["path"] for entry in content if (package / entry["path"]).stat().st_mtime_ns <= written_at}
        self.assertGreater(len(untouched), 1000)
        self.assertEqual({entry["path"]: entry for entry in content if entry["path"] in untouched},
                         {entry["path"]: entry for entry in existing if entry["path"] in untouched})
        self.assertEqual({entry["path"] for entry in content}, {entry["path"] for entry in existing})


LIVERY_CFG = b'[VERSION]\r\nmajor =1\r\n\r\n[GENERAL]\r\nname="Delta N991DL"\r\n\r\n[Selection]\r\nrequired_tags="MD88"\r\n\r\n[FLTSIM.0]\r\natc_id="N991DL"\r\n'


@dataclass
class FakeFiles:
    livery_dir: Path
    aircrafts_dir: Path
    layout_package: Path


def make_zip(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


class LiveriesTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        package = root / "Community" / "lsh-maddogx-aircraft"
        self.files = FakeFiles(
            livery_dir=package / "SimObjects/Airplanes/MaddogX20thNV/liveries/lsh",
            aircrafts_dir=package / "Aircrafts",
            layout_package=package,
        )
        self.files.livery_dir.mkdir(parents=True)
        self.files.aircrafts_dir.mkdir(parents=True)
        (package / "manifest.json").write_bytes(b'{\r\n  "total_package_size": "00000000000000000000"\r\n}')
        self.livery = Livery(name="MD-88 - Delta N991DL", reg="N991DL", folder="N991DL", variant=2)
        self.zip = make_zip(root / "N991DL.zip", {
            "N991DL/livery.cfg": LIVERY_CFG,
            "N991DL/texture.exterior/FWD_ALBEDO.PNG.KTX2": b"texture",
            "N991DL/thumbnail/thumbnail.png": b"big-png",
            "N991DL.mda": b"new-mda",
            "N991DL.png": b"new-icon",
            "readme.txt": b"outside",
        })

    def tearDown(self):
        self.folder.cleanup()

    def layout_paths(self):
        layout = json.loads((self.files.layout_package / "layout.json").read_text())
        return {entry["path"] for entry in layout["content"]}

    def test_parses_the_official_catalog(self):
        data = json.dumps([{
            "zip_path": "https://www.flythemaddog.com/downloads/FS2024_Liveries_20th/N944AS.zip",
            "image_path_jpg": "https://www.flythemaddog.com/downloads/FS2024_Liveries_20th/images/N944AS.jpg",
            "image_path_png": "",
            "name": "MD-83 - Alaska Airlines N944AS",
            "variant": 1,
            "reg": "N944AS",
            "livery_folder": "N944AS",
        }]).encode("utf-8-sig")
        [livery] = liveries.parse_catalog(data)
        self.assertEqual((livery.reg, livery.folder, livery.variant, livery.variant_name), ("N944AS", "N944AS", 1, "MD-83"))
        self.assertTrue(livery.image_url.endswith("N944AS.jpg"))

    def test_installs_folder_aircraft_file_and_icon(self):
        liveries.install(self.zip, self.livery, self.files)
        installed = self.files.livery_dir / "N991DL"
        self.assertEqual((installed / "texture.exterior/FWD_ALBEDO.PNG.KTX2").read_bytes(), b"texture")
        self.assertEqual((self.files.aircrafts_dir / "N991DL.mda").read_bytes(), b"new-mda")
        self.assertEqual((self.files.aircrafts_dir / "N991DL.png").read_bytes(), b"new-icon")
        self.assertFalse((self.files.layout_package / "readme.txt").exists())
        self.assertEqual(sorted(path.name for path in self.files.livery_dir.parent.iterdir()), ["lsh"])
        self.assertIn("SimObjects/Airplanes/MaddogX20thNV/liveries/lsh/N991DL/livery.cfg", self.layout_paths())
        self.assertTrue(liveries.is_installed(self.files.livery_dir, "N991DL"))
        self.assertEqual(liveries.installed_variant(self.files.livery_dir, "N991DL"), 2)

    def test_keeps_the_aircraft_file_already_there_and_replaces_the_rest(self):
        (self.files.aircrafts_dir / "N991DL.mda").write_bytes(b"flight-hours")
        (self.files.aircrafts_dir / "N991DL.png").write_bytes(b"old-icon")
        stale = self.files.livery_dir / "N991DL" / "old.dds"
        stale.parent.mkdir()
        stale.write_bytes(b"old")
        liveries.install(self.zip, self.livery, self.files)
        self.assertEqual((self.files.aircrafts_dir / "N991DL.mda").read_bytes(), b"flight-hours")
        self.assertEqual((self.files.aircrafts_dir / "N991DL.png").read_bytes(), b"new-icon")
        self.assertFalse(stale.exists())

    def test_refuses_paths_that_leave_the_folder(self):
        evil = make_zip(Path(self.folder.name) / "evil.zip", {
            "N991DL/livery.cfg": LIVERY_CFG,
            "N991DL/../../../../outside.txt": b"x",
        })
        with self.assertRaises(ValueError):
            liveries.install(evil, self.livery, self.files)
        self.assertFalse(any(self.files.livery_dir.iterdir()))
        self.assertFalse((self.files.layout_package / "layout.json").exists())

    def test_refuses_a_zip_without_the_livery_folder(self):
        other = make_zip(Path(self.folder.name) / "other.zip", {"OTHER/livery.cfg": LIVERY_CFG})
        with self.assertRaises(ValueError):
            liveries.install(other, self.livery, self.files)

    def test_uninstall_removes_the_folder_and_updates_the_layout(self):
        liveries.install(self.zip, self.livery, self.files)
        liveries.uninstall(self.livery, self.files)
        self.assertFalse((self.files.livery_dir / "N991DL").exists())
        self.assertTrue((self.files.aircrafts_dir / "N991DL.mda").exists())
        self.assertFalse(any("liveries/lsh/N991DL" in path for path in self.layout_paths()))

    def test_scans_installed_liveries(self):
        liveries.install(self.zip, self.livery, self.files)
        broken = self.files.livery_dir / "NO-THUMBNAIL"
        broken.mkdir()
        (broken / "livery.cfg").write_bytes(LIVERY_CFG)
        [found] = liveries.scan_installed(self.files.livery_dir)
        self.assertEqual((found.name, found.reg, found.folder), ("Delta N991DL", "N991DL", "N991DL"))
        self.assertEqual(found.image_path.name, "thumbnail.png")

    def test_a_second_install_waits_for_the_first_one(self):
        other = Livery(name="Alaska N944AS", reg="N944AS", folder="N944AS", variant=1)
        other_zip = make_zip(Path(self.folder.name) / "N944AS.zip", {
            "N944AS/livery.cfg": LIVERY_CFG.replace(b"N991DL", b"N944AS"),
            "N944AS/thumbnail/thumbnail.png": b"png",
        })
        extracting = threading.Event()
        release = threading.Event()

        def hold(done, total):
            extracting.set()
            release.wait(5)

        first = threading.Thread(target=liveries.install, args=(self.zip, self.livery, self.files, hold))
        first.start()
        self.assertTrue(extracting.wait(5))
        second = threading.Thread(target=liveries.install, args=(other_zip, other, self.files))
        second.start()
        second.join(0.5)
        still_waiting = second.is_alive()
        layout = self.files.layout_package / "layout.json"
        written_meanwhile = layout.read_text() if layout.exists() else ""
        release.set()
        first.join(5)
        second.join(5)
        self.assertTrue(still_waiting)
        self.assertNotIn("installing", written_meanwhile)
        self.assertFalse(any("installing" in path for path in self.layout_paths()))
        self.assertTrue({"N991DL", "N944AS"} <= {path.split("/")[5] for path in self.layout_paths() if "/liveries/lsh/" in path})

    def test_reads_a_local_zip_without_extracting_it(self):
        cache = Path(self.folder.name) / "cache"
        found = liveries.read_zip_livery(self.zip, cache)
        self.assertEqual((found.name, found.reg, found.folder, found.zip_path), ("Delta N991DL", "N991DL", "N991DL", self.zip))
        self.assertEqual(found.image_path.read_bytes(), b"big-png")
        self.assertIsNone(liveries.read_zip_livery(make_zip(Path(self.folder.name) / "x.zip", {"a.txt": b""}), cache))


def run(transfer, signals, on_progress=None, timeout=10000):
    loop = QEventLoop()
    outcome = {}
    for name in signals:
        getattr(transfer, name).connect(lambda *args, name=name: (outcome.update(signal=name, args=args), loop.quit()))
    if on_progress:
        transfer.progress.connect(on_progress)
    QTimer.singleShot(timeout, loop.quit)
    loop.exec()
    return outcome


@needs_qt
class NetTest(unittest.TestCase):
    def setUp(self):
        self.app = application()
        self.manager = QNetworkAccessManager()
        self.folder = tempfile.TemporaryDirectory()
        self.target = Path(self.folder.name) / "kit.zip"
        self.partial = Path(self.folder.name) / "kit.zip.part"

    def tearDown(self):
        self.folder.cleanup()

    def download(self, server, resume=False, on_progress=None):
        transfer = FileDownload(self.manager, server.url("/kit.zip"), self.target, resume=resume)
        return transfer, run(transfer, ["done", "failed", "canceled"], on_progress)

    def test_fetch_returns_the_body(self):
        with LocalServer({"/liveries.json": b"[1]"}) as server:
            outcome = run(Fetch(self.manager, server.url("/liveries.json")), ["done", "failed"])
        self.assertEqual(outcome, {"signal": "done", "args": (b"[1]",)})

    def test_fetch_reports_http_errors(self):
        with LocalServer({}) as server:
            outcome = run(Fetch(self.manager, server.url("/nada")), ["done", "failed"])
        self.assertEqual(outcome["signal"], "failed")

    def test_downloads_to_the_target_and_drops_the_partial_file(self):
        seen = []
        with LocalServer({"/kit.zip": BODY}) as server:
            _, outcome = self.download(server, on_progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(outcome["signal"], "done")
        self.assertEqual(self.target.read_bytes(), BODY)
        self.assertFalse(self.partial.exists())
        self.assertEqual(seen[-1], (len(BODY), len(BODY)))

    def test_resumes_from_the_partial_file(self):
        self.partial.write_bytes(BODY[:30000])
        with LocalServer({"/kit.zip": BODY}) as server:
            _, outcome = self.download(server, resume=True)
            self.assertEqual(server.requests[-1][1], "bytes=30000-")
        self.assertEqual(outcome["signal"], "done")
        self.assertEqual(self.target.read_bytes(), BODY)

    def test_starts_over_when_the_server_ignores_the_range(self):
        self.partial.write_bytes(b"junk" * 1000)
        with LocalServer({"/kit.zip": BODY}, honor_range=False) as server:
            _, outcome = self.download(server, resume=True)
        self.assertEqual(outcome["signal"], "done")
        self.assertEqual(self.target.read_bytes(), BODY)

    def test_cancel_removes_the_partial_file_and_pause_keeps_it(self):
        for keep in (False, True):
            with self.subTest(keep=keep), LocalServer({"/kit.zip": BODY}, delay=0.01) as server:
                transfer = FileDownload(self.manager, server.url("/kit.zip"), self.target)
                stopped = []

                def stop(done, total, transfer=transfer):
                    if done and not stopped:
                        stopped.append(done)
                        transfer.cancel(keep)

                outcome = run(transfer, ["done", "failed", "canceled"], stop)
                self.assertEqual(outcome["signal"], "canceled")
                self.assertFalse(self.target.exists())
                self.assertEqual(self.partial.exists(), keep)
                self.partial.unlink(missing_ok=True)


def option(group, key):
    return next(item for item in group if item.key == key)


class OptionsTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.files, self.work = make_install(self.folder.name, b"[FAILURES]\r\nDisableAllFailures=0\r\n[SYSTEM]\r\nClbThrottle=50\r\nFloodLights=30\r\n")

    def tearDown(self):
        self.folder.cleanup()

    def maddogx(self):
        return IniFile(self.files.maddogx_ini)

    def changed(self):
        return IniFile(self.files.manager_ini).get_int("MANAGER", "Changed")

    def test_realistic_mode_is_the_inverse_of_disable_all_failures(self):
        realistic = option(options.TOGGLES, "DisableAllFailures")
        self.assertTrue(realistic.read(self.maddogx()))
        options.write_option(self.files, self.files.maddogx_ini, realistic, False)
        self.assertEqual(self.maddogx().get("FAILURES", "DisableAllFailures"), "1")
        self.assertEqual(self.changed(), 0)

    def test_throttle_curve_is_stored_fifty_up(self):
        throttle = options.AXES[3][1][0]
        self.assertEqual(throttle.read(self.maddogx()), 0)
        options.write_option(self.files, self.files.maddogx_ini, throttle, -10)
        self.assertEqual(self.maddogx().get_int("SYSTEM", "ClbThrottle"), 40)

    def test_missing_sounds_take_the_manager_defaults(self):
        self.assertEqual(option(options.SOUNDS, "VolEngineCockpit").read(self.maddogx()), 10)
        self.assertEqual(options.BOARDING_MUSIC.read(self.maddogx()), 60)
        self.assertEqual(options.CREW_VOLUME.read(self.maddogx()), 80)

    def test_lights_and_sounds_tell_the_aircraft_something_changed(self):
        options.write_option(self.files, self.files.maddogx_ini, option(options.LIGHTS, "FloodLights"), 75)
        self.assertEqual(self.maddogx().get_int("SYSTEM", "FloodLights"), 75)
        self.assertEqual(self.changed(), 1)

    def test_cpdlc_settings_follow_the_data_provider(self):
        options.write_cpdlc(self.files, options.SAYINTENTIONS, "LogonCode", "SI-123")
        options.write_cpdlc(self.files, 1, "Enable", True)
        self.assertEqual(IniFile(self.files.common_data / "sayintentionsai.ini").get("SAYINTENTIONSAI", "LogonCode"), "SI-123")
        self.assertEqual(IniFile(self.files.common_data / "hoppie.ini").get("HOPPIE", "Enable"), "1")
        self.assertEqual(options.cpdlc_settings(self.files, options.SAYINTENTIONS)["logon"], "SI-123")
        self.assertTrue(options.cpdlc_settings(self.files, 0)["enable"])
        self.assertFalse(options.cpdlc_settings(self.files, options.NO_LOGON)["show_logon"])

    def test_exports_and_imports_toggles_and_lights(self):
        exported = Path(self.folder.name) / "mine.mdc"
        options.export_settings(self.files, exported)
        self.assertEqual(IniFile(exported).get_int("SYSTEM", "FloodLights"), 30)
        self.assertEqual(IniFile(exported).get("FAILURES", "DisableAllFailures"), "0")
        preset = Path(self.folder.name) / "other.mdc"
        preset.write_bytes(b"[SYSTEM]\r\nFloodLights=90\r\nAutoFuel=1\r\n[FAILURES]\r\nDisableAllFailures=1\r\n")
        options.import_settings(self.files, preset)
        self.assertEqual(self.maddogx().get_int("SYSTEM", "FloodLights"), 90)
        self.assertEqual(self.maddogx().get("SYSTEM", "AutoFuel"), "1")
        self.assertEqual(self.maddogx().get("FAILURES", "DisableAllFailures"), "1")
        self.assertEqual(self.maddogx().get_int("SYSTEM", "ClbThrottle"), 50)

    def test_panel_state_copies_its_mdx_and_records_the_choice(self):
        options.apply_panel_state(self.files, 3)
        self.assertEqual((self.files.package / "Data" / "maddogx.mdx").read_text(), "ready at gate.mdx")
        manager = IniFile(self.files.manager_ini)
        self.assertEqual(manager.get_int("MANAGER", "PanelState"), 3)
        self.assertEqual(manager.get("MANAGER", "PanelStateName"), "Ready at gate")
        self.assertEqual(self.changed(), 1)

    def test_do_not_change_leaves_the_last_flight_alone(self):
        options.apply_panel_state(self.files, 0)
        self.assertEqual((self.files.package / "Data" / "maddogx.mdx").read_text(), "last flight state")

    def test_versions(self):
        self.assertEqual(options.installed_build(self.files), ("2.1 b283", 283))
        self.assertEqual(options.server_build("[MDX20BUILD=280][MDX24BUILD=290]"), 290)
        self.assertIsNone(options.server_build("nada"))

    def test_reads_the_airac_cycle_from_the_work_folder(self):
        self.assertIsNone(options.airac_info(self.files))
        navdata = self.work / "Navigraph" / "Navdata"
        navdata.mkdir(parents=True)
        (navdata / "cycle_info.txt").write_text("AIRAC cycle    : 2610\nVersion        : 1\nValid (from/to): 02/OCT/2026 - 29/OCT/2026\n")
        self.assertEqual(options.airac_info(self.files), {"cycle": "2610", "version": "1", "valid": "02/OCT/2026 - 29/OCT/2026"})

    def test_lists_crew_sets(self):
        self.assertEqual(options.crew_sets(self.files), ["Maddog"])


def zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@needs_qt
class LiveriesPageTest(unittest.TestCase):
    def setUp(self):
        application()
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.files, _ = make_install(root / "install")
        (self.files.package / "manifest.json").write_bytes(b'{\r\n  "total_package_size": "00000000000000000000"\r\n}')
        self.server = LocalServer({
            "/N991DL.zip": zip_bytes({
                "N991DL/livery.cfg": LIVERY_CFG,
                "N991DL/texture.exterior/FWD_ALBEDO.PNG.KTX2": b"texture" * 5000,
                "N991DL/thumbnail/thumbnail.png": b"png",
                "N991DL.mda": b"mda",
            }),
        }).__enter__()
        catalog = [{
            "zip_path": self.server.url("/N991DL.zip"),
            "image_path_jpg": "",
            "image_path_png": "",
            "name": "MD-88 - Delta N991DL",
            "variant": 2,
            "reg": "N991DL",
            "livery_folder": "N991DL",
        }]
        self.server.httpd.files["/liveries.json"] = json.dumps(catalog).encode()
        caches = root / "cache"
        self.patches = [
            mock.patch.object(liveries_page, "CATALOG_URL", self.server.url("/liveries.json")),
            mock.patch.object(liveries_page, "simulator_running", lambda: False),
            mock.patch.object(liveries_page, "cache_dir", lambda name: (caches / name).mkdir(parents=True, exist_ok=True) or caches / name),
            mock.patch.object(liveries_page, "downloads_dir", lambda: root / "Downloads"),
            mock.patch.object(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
            mock.patch.object(QMessageBox, "information", lambda *args, **kwargs: None),
            mock.patch.object(QMessageBox, "warning", lambda *args, **kwargs: None),
        ]
        for patch in self.patches:
            patch.start()
        self.page = LiveriesPage(self.files, QNetworkAccessManager())

    def tearDown(self):
        self.page.deleteLater()
        for patch in self.patches:
            patch.stop()
        self.server.__exit__(None, None, None)
        self.folder.cleanup()

    def card(self, reg):
        self.assertTrue(wait_until(lambda: any(card.livery.reg == reg for card in self.page.cards)))
        return next(card for card in self.page.cards if card.livery.reg == reg)

    def test_downloads_installs_and_removes_an_official_livery(self):
        card = self.card("N991DL")
        self.assertEqual(card.button.text(), "Install")
        card.button.click()
        self.assertTrue(wait_until(lambda: card.installed and not card.busy))
        installed = self.files.livery_dir / "N991DL" / "texture.exterior" / "FWD_ALBEDO.PNG.KTX2"
        self.assertEqual(installed.read_bytes(), b"texture" * 5000)
        self.assertEqual((self.files.aircrafts_dir / "N991DL.mda").read_bytes(), b"mda")
        layout = (self.files.package / "layout.json").read_text()
        self.assertIn("liveries/lsh/N991DL/livery.cfg", layout)
        self.assertEqual(list((Path(self.folder.name) / "cache" / "downloads").iterdir()), [])
        self.assertEqual(card.button.text(), "Remove")
        card.button.click()
        self.assertTrue(wait_until(lambda: not card.installed and not card.busy))
        self.assertFalse((self.files.livery_dir / "N991DL").exists())
        self.assertNotIn("liveries/lsh/N991DL", (self.files.package / "layout.json").read_text())

    def test_does_nothing_while_the_simulator_runs(self):
        card = self.card("N991DL")
        with mock.patch.object(liveries_page, "simulator_running", lambda: True):
            card.button.click()
        self.assertFalse(card.busy)
        self.assertNotIn("/N991DL.zip", [path for path, _ in self.server.requests])

    def test_lists_installed_liveries_that_are_not_in_the_catalog(self):
        own = self.files.livery_dir / "PP-MDG"
        (own / "thumbnail").mkdir(parents=True)
        (own / "livery.cfg").write_bytes(b'[GENERAL]\r\nname="Minha pintura"\r\n[FLTSIM.0]\r\natc_id="PP-MDG"\r\n')
        self.page.load()
        card = self.card("PP-MDG")
        self.assertTrue(card.external)
        self.assertTrue(card.installed)


@needs_qt
class OptionsPageTest(unittest.TestCase):
    def setUp(self):
        application()
        self.folder = tempfile.TemporaryDirectory()
        self.files, _ = make_install(Path(self.folder.name), b"[FAILURES]\r\nDisableAllFailures=1\r\n[SYSTEM]\r\nWXdata=3\r\n")
        (self.files.common_data / "sayintentionsai.ini").write_bytes(b"[SAYINTENTIONSAI]\r\nLogonCode=SI-9\r\n")

    def tearDown(self):
        self.folder.cleanup()

    def test_opening_writes_nothing_and_changes_write_at_once(self):
        before = self.files.maddogx_ini.read_bytes()
        cpdlc = (self.files.common_data / "sayintentionsai.ini").read_bytes()
        page = OptionsPage(self.files)
        self.assertEqual(self.files.maddogx_ini.read_bytes(), before)
        self.assertEqual((self.files.common_data / "sayintentionsai.ini").read_bytes(), cpdlc)
        self.assertEqual(page.logon.text(), "SI-9")
        page.toggles["DisableAllFailures"].setChecked(True)
        self.assertEqual(IniFile(self.files.maddogx_ini).get("FAILURES", "DisableAllFailures"), "0")
        page.provider.setCurrentIndex(1)
        self.assertEqual(IniFile(self.files.maddogx_ini).get("SYSTEM", "WXdata"), "1")
        self.assertFalse(page.logon.isVisibleTo(page))
        page.deleteLater()

    def test_switching_provider_does_not_write_into_the_new_provider_file(self):
        (self.files.common_data / "sayintentionsai.ini").write_bytes(b"[SAYINTENTIONSAI]\r\nLogonCode=SI-9\r\nEnable=1\r\n")
        hoppie = self.files.common_data / "hoppie.ini"
        hoppie.write_bytes(b"[HOPPIE]\r\nLogonCode=HP-1\r\n")
        page = OptionsPage(self.files)
        self.assertTrue(page.cpdlc_enable.isChecked())
        page.provider.setCurrentIndex(1)
        self.assertEqual(page.logon.text(), "HP-1")
        self.assertFalse(page.cpdlc_enable.isChecked())
        self.assertEqual(hoppie.read_bytes(), b"[HOPPIE]\r\nLogonCode=HP-1\r\n")
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
