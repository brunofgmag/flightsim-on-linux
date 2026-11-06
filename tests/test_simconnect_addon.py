import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fsol import addons, simconnect
from tests.test_machine import FakeMachine

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addons" / "simconnect-tcp"
READER = ROOT / "tools" / "aircraft-loaded.mjs"
NODE_TIMEOUT = 60
XML = (
    '<?xml version="1.0" encoding="Windows-1252"?>\r\n'
    '<SimBase.Document Type="SimConnect">\r\n'
    "  <Descr>SimConnect Server Configuration \xe9</Descr>\r\n"
    "  <SimConnect.Comm>\r\n"
    "    <Descr>Static IP4 port</Descr>\r\n"
    "    <Protocol>IPv4</Protocol>\r\n"
    "    <Scope>local</Scope>\r\n"
    "    <Port>%s</Port>\r\n"
    "    <MaxClients>256</MaxClients>\r\n"
    "  </SimConnect.Comm>\r\n"
    "  <SimConnect.Comm>\r\n"
    "    <Descr>Static IP6 port</Descr>\r\n"
    "    <Protocol>IPv6</Protocol>\r\n"
    "    <Port>501</Port>\r\n"
    "  </SimConnect.Comm>\r\n"
    "  <SimConnect.Comm>\r\n"
    "    <Descr>Dynamic IP4 port</Descr>\r\n"
    "    <Protocol>IPv4</Protocol>\r\n"
    "    <Port>0</Port>\r\n"
    "  </SimConnect.Comm>\r\n"
    "</SimBase.Document>"
)
FAKE_NPM = """#!/bin/sh
echo "$@" >> "$NPM_LOG"
[ "${NPM_FAIL:-0}" = 1 ] && exit 1
while [ $# -gt 0 ]; do
  [ "$1" = --prefix ] && prefix="$2"
  shift
done
mkdir -p "$prefix/node_modules/node-simconnect"
echo '{"name": "node-simconnect", "version": "4.2.1"}' > "$prefix/node_modules/node-simconnect/package.json"
"""


def xml(port=500):
    return (XML % port).encode("cp1252")


class ParserTest(unittest.TestCase):
    def test_the_static_ipv4_port_is_read(self):
        self.assertEqual(simconnect.read_port(xml(500)), 500)
        self.assertEqual(simconnect.read_port(xml(5111)), 5111)

    def test_only_the_static_ipv4_block_counts_not_ipv6_and_not_the_dynamic_port(self):
        data = xml(5111).replace(b"<Port>5111</Port>", b"<Port>0</Port>")
        self.assertIsNone(simconnect.read_port(data))

    def test_a_file_with_no_port_gives_none(self):
        self.assertIsNone(simconnect.read_port(b"<SimBase.Document/>"))
        self.assertIsNone(simconnect.read_port(b"not xml at all \xff"))

    def test_spaces_inside_the_tags_are_allowed(self):
        data = b"<SimConnect.Comm><Protocol> IPv4 </Protocol><Port> 7000 </Port></SimConnect.Comm>"
        self.assertEqual(simconnect.read_port(data), 7000)

    def test_changing_the_port_keeps_every_other_byte(self):
        before = xml(500)
        after = simconnect.with_port(before, 5111)
        self.assertEqual(after, before.replace(b"<Port>500</Port>", b"<Port>5111</Port>", 1))
        self.assertIn(b"\r\n", after)
        self.assertIn("\xe9".encode("cp1252"), after)

    def test_changing_the_port_of_a_file_without_one_is_an_error(self):
        with self.assertRaises(ValueError):
            simconnect.with_port(b"<SimBase.Document/>", 5111)


class PortOfTheMachineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.found = FakeMachine(Path(self.temp.name)).build().discover()

    def tearDown(self):
        self.temp.cleanup()

    def test_the_port_comes_from_the_file_beside_user_cfg(self):
        (self.found.base / "SimConnect.xml").write_bytes(xml(5111))
        self.assertEqual(simconnect.port(self.found), 5111)

    def test_no_file_means_no_port(self):
        self.assertIsNone(simconnect.port(self.found))

    def test_no_base_folder_means_no_port(self):
        self.found.base = None
        self.assertIsNone(simconnect.port(self.found))


class ClientConfigTest(unittest.TestCase):
    TOOL = ROOT / "fsol" / "simconnect.py"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "base"
        self.base.mkdir()
        self.folder = self.root / "client"
        self.folder.mkdir()

    def run_tool(self, *args):
        result = subprocess.run(["python3", "-I", str(self.TOOL), *args], capture_output=True, text=True)
        return result.returncode, result.stdout.strip(), result.stderr.strip()

    def declare(self, port):
        (self.base / "SimConnect.xml").write_bytes(xml(port))

    def cfg(self):
        return self.folder / "SimConnect.cfg"

    def test_write_makes_a_cfg_that_points_the_client_at_the_declared_port(self):
        self.declare(5111)
        code, out, _ = self.run_tool("write", "demo", str(self.base), str(self.folder))
        self.assertEqual(code, 0)
        self.assertIn("points SimConnect clients at 127.0.0.1:5111", out)
        self.assertEqual(self.cfg().read_bytes(), b"[SimConnect]\r\nProtocol=IPv4\r\nAddress=127.0.0.1\r\nPort=5111\r\n")

    def test_a_second_write_changes_nothing(self):
        self.declare(5111)
        self.run_tool("write", "demo", str(self.base), str(self.folder))
        code, out, _ = self.run_tool("write", "demo", str(self.base), str(self.folder))
        self.assertEqual((code, out.startswith("already ")), (0, True))

    def test_write_follows_a_port_that_changed(self):
        self.declare(5111)
        self.run_tool("write", "demo", str(self.base), str(self.folder))
        self.declare(5222)
        self.run_tool("write", "demo", str(self.base), str(self.folder))
        self.assertIn(b"Port=5222", self.cfg().read_bytes())

    def test_a_privileged_port_is_refused_with_the_fix(self):
        self.declare(500)
        code, out, _ = self.run_tool("write", "demo", str(self.base), str(self.folder))
        self.assertEqual(code, 1)
        self.assertIn("declares port 500, which Linux will not open for the simulator; run: flightsim-on-linux addon apply simconnect-tcp", out)
        self.assertFalse(self.cfg().exists())

    def test_check_gives_one_line_for_the_declaration_and_one_for_the_cfg_and_names_the_add_on_to_apply(self):
        self.declare(5111)
        code, out, _ = self.run_tool("check", "demo", str(self.base), str(self.folder))
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[0], "ok SimConnect.xml declares the static IPv4 port 5111, above 1023")
        self.assertIn(f"FAIL {self.cfg()} does not point at port 5111; run: flightsim-on-linux addon apply demo", out)
        self.run_tool("write", "demo", str(self.base), str(self.folder))
        code, out, _ = self.run_tool("check", "demo", str(self.base), str(self.folder))
        self.assertEqual((code, out.splitlines()[1]), (0, f"ok {self.cfg()} points at port 5111"))

    def test_remove_deletes_the_cfg_and_a_second_remove_says_it_is_already_gone(self):
        self.declare(5111)
        self.run_tool("write", "demo", str(self.base), str(self.folder))
        self.assertEqual(self.run_tool("remove", "demo", str(self.base), str(self.folder))[0], 0)
        self.assertFalse(self.cfg().exists())
        self.assertTrue(self.run_tool("remove", "demo", str(self.base), str(self.folder))[1].startswith("already "))

    def watcher_file(self, port):
        path = self.root / "addon.json"
        path.write_text(json.dumps({"watcher": {"health": {"connected_to": port}}}))
        return path

    def test_watched_passes_when_the_watcher_looks_at_the_declared_port_and_fails_when_it_does_not(self):
        self.declare(5111)
        self.assertEqual(self.run_tool("watched", "demo", str(self.base), str(self.watcher_file(5111)))[:2], (0, "ok the watcher looks for the Simlink connection on port 5111, the one SimConnect.xml declares"))
        code, out, _ = self.run_tool("watched", "demo", str(self.base), str(self.watcher_file(5000)))
        self.assertEqual(code, 1)
        self.assertIn("set watcher.health.connected_to to 5111", out)

    def test_watched_says_nothing_when_the_port_is_not_declared_because_check_already_failed(self):
        self.assertEqual(self.run_tool("watched", "demo", str(self.base), str(self.watcher_file(5000))), (0, "", ""))

    def test_an_unknown_action_or_a_wrong_count_prints_the_usage_and_exits_2(self):
        for args in (("frobnicate", "demo", "a", "b"), ("write", "demo", "a")):
            code, _, err = self.run_tool(*args)
            self.assertEqual(code, 2)
            self.assertIn("usage: simconnect.py", err)


class AddonCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake = FakeMachine(self.root).build()
        self.found = self.fake.discover()
        self.file = self.found.base / "SimConnect.xml"
        self.cache = self.root / "cache"
        self.backup = self.fake.compatdata / "flightsim-on-linux-backup" / "simconnect-tcp" / "SimConnect.xml"
        self.npm_log = self.root / "npm.log"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "npm").write_text(FAKE_NPM)
        (self.bin / "npm").chmod(0o755)
        self.without_npm = self.root / "without-npm"
        self.without_npm.mkdir()
        for name in ("bash", "dirname", "readlink", "python3"):
            os.symlink(shutil.which(name), self.without_npm / name)
        self.have_npm = True

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, action, **env):
        path = f"{self.bin}:/usr/bin:/bin" if self.have_npm else str(self.without_npm)
        merged = {
            "PATH": path,
            "HOME": str(self.root / "home"),
            "FSOL_BASE": str(self.found.base),
            "FSOL_COMPATDATA": str(self.fake.compatdata),
            "FSOL_CACHE": str(self.cache),
            "NPM_LOG": str(self.npm_log),
        }
        merged.update(env)
        return subprocess.run([str(ADDON / action)], env=merged, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def npm_calls(self):
        return self.npm_log.read_text().splitlines() if self.npm_log.exists() else []


class ShapeTest(AddonCase):
    def test_the_addon_loads_with_its_checks_on_the_base_folder(self):
        addon = addons.load(ADDON)
        self.assertEqual((addon.id, addon.needs, addon.installed), ("simconnect-tcp", ["simulator-closed"], {"base": ["SimConnect.xml"]}))

    def test_it_counts_as_installed_where_the_file_exists(self):
        addon = addons.load(ADDON)
        self.assertFalse(addons.is_installed(addon, self.found))
        self.file.write_bytes(xml())
        self.assertTrue(addons.is_installed(addon, self.found))


class ApplyTest(AddonCase):
    def test_a_privileged_port_moves_to_5111_and_nothing_else_changes(self):
        self.file.write_bytes(xml(500))
        result = self.run_script("apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.file.read_bytes(), xml(5111))
        self.assertIn("moved the static IPv4 port from 500 to 5111", result.stdout)

    def test_the_original_is_kept_byte_for_byte(self):
        self.file.write_bytes(xml(500))
        self.run_script("apply")
        self.assertEqual(self.backup.read_bytes(), xml(500))

    def test_a_second_apply_keeps_the_first_backup(self):
        self.file.write_bytes(xml(500))
        self.run_script("apply")
        self.file.write_bytes(xml(600))
        self.run_script("apply")
        self.assertEqual(self.file.read_bytes(), xml(5111))
        self.assertEqual(self.backup.read_bytes(), xml(500))

    def test_a_port_above_1023_is_left_alone_and_gets_no_backup(self):
        self.file.write_bytes(xml(7000))
        result = self.run_script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.file.read_bytes(), xml(7000))
        self.assertFalse(self.backup.exists())
        self.assertIn("already 7000", result.stdout)

    def test_a_missing_file_fails_and_says_what_to_do(self):
        result = self.run_script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("start the simulator once", result.stdout)
        self.assertEqual(self.npm_calls(), [])

    def test_a_file_without_a_static_port_fails_and_is_not_touched(self):
        self.file.write_bytes(b"<SimBase.Document/>")
        result = self.run_script("apply")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.file.read_bytes(), b"<SimBase.Document/>")
        self.assertFalse(self.backup.exists())

    def test_the_pinned_node_simconnect_is_installed_into_the_cache(self):
        self.file.write_bytes(xml(500))
        result = self.run_script("apply")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.npm_calls(), [f"install --prefix {self.cache / 'simconnect'} --no-save --no-audit --no-fund --ignore-scripts node-simconnect@4.2.1"])

    def test_a_second_apply_does_not_ask_npm_again(self):
        self.file.write_bytes(xml(500))
        self.run_script("apply")
        self.run_script("apply")
        self.assertEqual(len(self.npm_calls()), 1)

    def test_without_npm_the_port_still_moves_and_the_skip_is_said(self):
        self.have_npm = False
        self.file.write_bytes(xml(500))
        result = self.run_script("apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.file.read_bytes(), xml(5111))
        self.assertIn("npm is not installed", result.stdout)

    def test_a_failing_npm_fails_the_apply(self):
        self.file.write_bytes(xml(500))
        result = self.run_script("apply", NPM_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL npm could not install node-simconnect 4.2.1", result.stdout)


class CheckTest(AddonCase):
    def test_a_port_above_1023_passes_with_one_line_for_each_fact(self):
        self.file.write_bytes(xml(5111))
        result = self.run_script("check")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), ["ok SimConnect.xml declares the static IPv4 port 5111", "ok port 5111 is above 1023, so the simulator can open it"])

    def test_the_unfixed_file_fails(self):
        self.file.write_bytes(xml(500))
        result = self.run_script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL port 500 is below 1024", result.stdout)

    def test_a_missing_file_fails(self):
        result = self.run_script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL", result.stdout)

    def test_a_file_that_does_not_parse_fails(self):
        self.file.write_bytes(b"<SimBase.Document><Port>")
        result = self.run_script("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL SimConnect.xml has no static IPv4 port", result.stdout)

    def test_apply_then_check_passes_and_undo_makes_it_fail_again(self):
        self.file.write_bytes(xml(500))
        self.assertEqual(self.run_script("check").returncode, 1)
        self.run_script("apply")
        self.assertEqual(self.run_script("check").returncode, 0)
        self.run_script("undo")
        self.assertEqual(self.run_script("check").returncode, 1)

    def test_check_changes_nothing(self):
        self.file.write_bytes(xml(500))
        self.run_script("check")
        self.assertEqual(self.file.read_bytes(), xml(500))
        self.assertFalse(self.backup.exists())
        self.assertFalse(self.cache.exists())

    def test_an_installed_module_gets_its_own_line(self):
        self.file.write_bytes(xml(500))
        self.run_script("apply")
        self.assertIn(f"ok node-simconnect 4.2.1 is in {self.cache / 'simconnect'}", self.run_script("check").stdout)


class UndoTest(AddonCase):
    def test_undo_puts_the_original_back_and_drops_the_backup(self):
        self.file.write_bytes(xml(500))
        self.run_script("apply")
        result = self.run_script("undo")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.file.read_bytes(), xml(500))
        self.assertFalse(self.backup.exists())

    def test_undo_without_a_backup_changes_nothing(self):
        self.file.write_bytes(xml(7000))
        result = self.run_script("undo")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.file.read_bytes(), xml(7000))
        self.assertIn("nothing was restored", result.stdout)


NODE_CASE = unittest.skipUnless(shutil.which("node"), "needs node")

FAKE_MODULE = """
const events = {};
const handle = {
  on(name, callback) { events[name] = callback; },
  requestSystemState(id, name) {
    for (const aircraft of (process.env.FAKE_AIRCRAFT || '').split('|').filter(Boolean)) {
      events.systemState({ requestID: id, dataString: aircraft });
    }
    if (process.env.FAKE_CLOSE === '1') events.close();
  },
};
exports.Protocol = { SunRise: 6 };
exports.open = async (name, protocol, options) => {
  if (process.env.FAKE_REFUSE === '1') throw new Error('Open timeout');
  require('fs').writeFileSync(process.env.FAKE_LOG, JSON.stringify({ name, protocol, options }));
  return { handle };
};
"""


@NODE_CASE
class AircraftReaderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        module = self.root / "modules" / "node_modules" / "node-simconnect"
        module.mkdir(parents=True)
        (module / "index.js").write_text(FAKE_MODULE)
        (module / "package.json").write_text('{"name": "node-simconnect", "main": "index.js"}')
        self.log = self.root / "log"

    def tearDown(self):
        self.temp.cleanup()

    def read(self, *args, timeout=NODE_TIMEOUT, **env):
        merged = dict(os.environ, FAKE_LOG=str(self.log), **env)
        return subprocess.run(["node", str(READER), *args], env=merged, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)

    def test_the_port_and_the_module_folder_come_from_the_arguments(self):
        result = self.read("6123", str(self.root / "modules"), FAKE_AIRCRAFT="Airplanes\\Maddog X", FAKE_CLOSE="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.log.read_text())["options"], {"host": "127.0.0.1", "port": 6123})
        self.assertEqual(json.loads(self.log.read_text())["protocol"], 6)

    def test_it_says_open_then_prints_each_new_aircraft_once(self):
        result = self.read("5111", str(self.root / "modules"), FAKE_AIRCRAFT="Airplanes\\A|Airplanes\\A|Airplanes\\B", FAKE_CLOSE="1")
        self.assertEqual(result.stdout.splitlines(), ["#open", "Airplanes\\A", "Airplanes\\B"])

    def test_a_missing_module_exits_with_the_code_the_watcher_knows(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.assertEqual(self.read("5111", str(empty)).returncode, 3)

    def test_a_simulator_that_does_not_answer_exits_quietly_with_1(self):
        result = self.read("5111", str(self.root / "modules"), FAKE_REFUSE="1")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", ""))

    def test_bad_arguments_exit_with_4_and_the_usage(self):
        for args in ((), ("x", "folder"), ("5111",)):
            result = self.read(*args)
            self.assertEqual(result.returncode, 4, args)
            self.assertIn("usage:", result.stderr)

    def test_a_relative_module_folder_works(self):
        result = subprocess.run(["node", str(READER), "5111", "modules"], cwd=self.root, env=dict(os.environ, FAKE_LOG=str(self.log), FAKE_CLOSE="1"), capture_output=True, text=True, timeout=NODE_TIMEOUT)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
