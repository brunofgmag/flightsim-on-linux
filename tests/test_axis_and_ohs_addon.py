import http.client
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fsol import addons, watcher
from tests.support import process_stubs, start_stand_in, stub
from tests.test_dotnet48_addon import version_resource
from tests.test_gsx_addon import MARKER, assembly, sha256
from tests.test_machine import FakeMachine, write

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addons" / "axis-and-ohs"
PARTS = ADDON / "parts"
sys.path.insert(0, str(PARTS))
sys.path.insert(0, str(ROOT / "addons/gsx-pro/parts"))

import clr  # noqa: E402
import cors_proxy  # noqa: E402
import deadlock  # noqa: E402

PATTERN = r"^[A-Za-z]:.*\\LorbyAxisAndOhs_MSFS2024\.exe"
STAND_IN = "Q:\\Program Files\\LorbyAxisAndOhs_MSFS24\\LorbyAxisAndOhs_MSFS2024.exe"
PROGRAM = "C:\\Program Files\\LorbyAxisAndOhs_MSFS24\\LorbyAxisAndOhs_MSFS2024.exe"
EXE = "drive_c/Program Files/LorbyAxisAndOhs_MSFS24/LorbyAxisAndOhs_MSFS2024.exe"
WINDOWSBASE = "lib/mono/gac/WindowsBase/4.0.0.0__31bf3856ad364e35/WindowsBase.dll"
HEAP_PATCH = "ntdll-heap-read-slack.patch"
HEAP_MODULE = "lib/wine/x86_64-windows/ntdll.dll"
DEADLOCK_LINES = (
    "err:sync:RtlpWaitForCriticalSection section 0x6FFFE6126510 \"?\" wait timed out in thread 35d0, blocked by 08e8, retrying (60 sec)\n"
    "err:sync:RtlpWaitForCriticalSection section 0x36708 \"?\" wait timed out in thread 08e8, blocked by 35d0, retrying (60 sec)\n"
)
SET_INTERVAL = b"\x28" + (0x0A000001).to_bytes(4, "little") + b"\x2A"
PATCHED_INTERVAL = b"\x28" + (0x0A000002).to_bytes(4, "little") + b"\x28" + (0x0A000001).to_bytes(4, "little") + b"\x2A"
DEFAULT_USER_REG = "WINE REGISTRY Version 2\n\n"


def windowsbase(patched=False):
    body, names = (PATCHED_INTERVAL, ("get_TotalMilliseconds", "FromTicks")) if patched else (SET_INTERVAL, ("get_TotalMilliseconds",))
    return assembly([("System.Windows.Threading", "DispatcherTimer", [("set_Interval", body)])], names)


class DeadlockTest(unittest.TestCase):
    def test_two_threads_waiting_on_each_other_are_a_cycle(self):
        self.assertEqual(deadlock.cycle(deadlock.waits(DEADLOCK_LINES.splitlines())), ["35d0", "08e8"])

    def test_three_threads_with_the_third_waiting_on_the_first_still_find_the_cycle(self):
        lines = DEADLOCK_LINES.splitlines() + ["RtlpWaitForCriticalSection x wait timed out in thread 3110, blocked by 35d0"]
        self.assertEqual(deadlock.cycle(deadlock.waits(lines)), ["35d0", "08e8"])

    def test_a_long_wait_without_a_cycle_is_not_a_deadlock(self):
        self.assertIsNone(deadlock.cycle(deadlock.waits(["RtlpWaitForCriticalSection x wait timed out in thread 35d0, blocked by 08e8"])))

    def test_lines_that_are_not_waits_are_ignored(self):
        self.assertEqual(deadlock.waits(["err:module:LdrInitializeThunk something", ""]), {})

    def test_the_command_line_exits_0_for_a_cycle_and_1_without(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "log"
            log.write_text(DEADLOCK_LINES)
            run = lambda: subprocess.run([sys.executable, "-I", str(PARTS / "deadlock.py"), str(log)], capture_output=True, text=True)
            self.assertEqual(run().returncode, 0)
            log.write_text("nothing\n")
            self.assertEqual(run().returncode, 1)


class ProxyTest(unittest.TestCase):
    def setUp(self):
        class Upstream(http.server.BaseHTTPRequestHandler):
            seen = []

            def do_GET(inner):
                Upstream.seen.append(("GET", inner.path, None))
                inner.send_response(200)
                inner.send_header("Access-Control-Allow-Origin", "*")
                inner.send_header("Access-Control-Allow-Origin", "*")
                inner.send_header("Content-Type", "text/plain")
                inner.send_header("Content-Length", "2")
                inner.end_headers()
                inner.wfile.write(b"OK")

            def do_POST(inner):
                body = inner.rfile.read(int(inner.headers.get("Content-Length", 0)))
                Upstream.seen.append(("POST", inner.path, body))
                inner.send_response(200)
                inner.send_header("Content-Length", "4")
                inner.end_headers()
                inner.wfile.write(b"ECHO")

            def do_OPTIONS(inner):
                Upstream.seen.append(("OPTIONS", inner.path, None))
                inner.send_response(405)
                inner.send_header("Content-Length", "0")
                inner.end_headers()

            def log_message(inner, *args):
                pass

        self.upstream_class = Upstream
        self.upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        self.proxy = cors_proxy.make_server(0, self.upstream.server_address[1])
        self.threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (self.upstream, self.proxy)]
        for thread in self.threads:
            thread.start()
        self.port = self.proxy.server_address[1]
        self.upstream_up = True

    def tearDown(self):
        self.proxy.shutdown()
        self.proxy.server_close()
        if self.upstream_up:
            self.upstream.shutdown()
            self.upstream.server_close()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        result = response.status, response.getheaders(), data
        connection.close()
        return result

    def test_the_duplicate_cors_header_of_the_upstream_goes_out_once(self):
        status, headers, data = self.request("GET", "/webapi?conn=1")
        names = [name.lower() for name, _ in headers]
        self.assertEqual((status, data), (200, b"OK"))
        self.assertEqual(names.count("access-control-allow-origin"), 1)
        self.assertEqual(names.count("content-length"), 1)

    def test_the_preflight_is_answered_by_the_proxy_with_the_requested_headers(self):
        status, headers, _ = self.request("OPTIONS", "/webapi", headers={"Access-Control-Request-Headers": "cache-control"})
        found = {name.lower(): value for name, value in headers}
        self.assertEqual(status, 204)
        self.assertEqual(found["access-control-allow-headers"], "cache-control")
        self.assertIn("POST", found["access-control-allow-methods"])
        self.assertEqual([call for call in self.upstream_class.seen if call[0] == "OPTIONS"], [])

    def test_a_post_body_reaches_the_upstream(self):
        status, _, data = self.request("POST", "/webapi", body=b"(A:TITLE, String)", headers={"Content-Type": "text/plain"})
        self.assertEqual((status, data), (200, b"ECHO"))
        self.assertIn(("POST", "/webapi", b"(A:TITLE, String)"), self.upstream_class.seen)

    def test_an_upstream_that_is_down_gives_a_502_with_the_cors_headers(self):
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_up = False
        status, headers, _ = self.request("GET", "/webapi")
        self.assertEqual(status, 502)
        self.assertIn("access-control-allow-origin", [name.lower() for name, _ in headers])

    def test_the_script_listens_on_the_port_it_is_given(self):
        listening = subprocess.Popen([str(ADDON / "cors-proxy"), "0", "1"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(listening.wait)
        self.addCleanup(listening.kill)
        time.sleep(0.5)
        self.assertIsNone(listening.poll())


class AaoCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.token = str(self.root)
        self.fake = FakeMachine(self.root).build()
        self.found = self.fake.discover()
        self.prefix = self.found.prefix
        self.proton = self.fake.tool
        self.record = self.root / "record"
        self.processes = []
        write(self.prefix / EXE, "MZ")
        write(self.prefix / "user.reg", DEFAULT_USER_REG)
        source = self.proton / "files/share/wine/mono/wine-mono-11.3.0"
        (source / WINDOWSBASE).parent.mkdir(parents=True)
        (source / WINDOWSBASE).write_bytes(windowsbase())
        (source / "lib/mono/4.5").mkdir(parents=True, exist_ok=True)
        (source / "lib/mono/4.5/mscorlib.dll").write_bytes(b"mscorlib")
        (source / MARKER).write_bytes(b"System.Core of 11.3.0")
        self.cache = self.root / "cache"
        self.tool = self.cache / "shims/windowsbase-timer/windowsbase-timer.dll"
        write(self.tool, "tool")
        os.utime(self.tool, (4102444800, 4102444800))
        self.patched = self.root / "patched-windowsbase.dll"
        self.patched.write_bytes(windowsbase(patched=True))
        self.write_build()
        stub(self.root / "bin/notify-send", f'#!/bin/sh\nprintf "notice %s\\n" "$*" >> "{self.record}"\n')
        stub(self.root / "bin/dotnet", f'#!/bin/sh\nprintf "dotnet %s\\n" "$*" >> "{self.record}"\ncp "{self.patched}" "$4"\n')
        stub(self.proton / "files/bin/wine", f'#!/bin/sh\nprintf "wine %s\\n" "$*" >> "{self.record}"\n')
        stub(self.proton / "files/bin/wineserver", "#!/bin/sh\nexit 0\n")
        process_stubs(self.root / "bin", self.token)

    def tearDown(self):
        self.stop_stand_ins()
        for process in self.processes:
            process.kill()
            process.wait()
        self.temp.cleanup()

    def stop_stand_ins(self):
        mine = f"FSOL_TEST_TOKEN={self.token}".encode()
        for pid_dir in watcher.matching(PATTERN, watcher.scan(Path("/proc"))):
            try:
                if mine in (pid_dir / "environ").read_bytes().split(b"\0"):
                    os.kill(int(pid_dir.name), 15)
            except (OSError, ValueError):
                continue

    def write_build(self, listed=True):
        module = self.proton / "files" / HEAP_MODULE
        write(module, "ntdll")
        patch = sha256((ROOT / "patches/wine" / HEAP_PATCH).read_bytes())
        lines = ["release cachyos-11.0-20261005-slr x86_64"]
        if listed:
            lines.append(f"patch {patch} {HEAP_PATCH}")
        lines.append(f"module {sha256(module.read_bytes())} {HEAP_MODULE}")
        (self.proton / "fs2024-build.txt").write_text("\n".join(lines) + "\n")

    def environment(self, **extra):
        env = {
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "FSOL_PREFIX": str(self.prefix),
            "FSOL_COMPATDATA": str(self.found.compatdata),
            "FSOL_PROTON": str(self.proton),
            "FSOL_BASE": str(self.found.base),
            "FSOL_COMMUNITY": str(self.found.community),
            "FSOL_CACHE": str(self.cache),
            "FSOL_CLI": str(self.root / "cli"),
            "FSOL_ADDON": "axis-and-ohs",
            "FSOL_TEST_TOKEN": self.token,
            "FSOL_SIM_RUNNING": "0",
            "FSOL_AAO_STEP": "1",
            "FSOL_AAO_WATCH": "10",
        }
        env.update(extra)
        return env

    def script(self, name, *args, **extra):
        return subprocess.run([str(ADDON / name), *args], env=self.environment(**extra), capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def recorded(self):
        return self.record.read_text().splitlines() if self.record.exists() else []

    def copy_path(self, relative):
        return self.prefix / "drive_c/windows/mono/mono-2.0" / relative

    def backup(self):
        return self.found.compatdata / "flightsim-on-linux-backup/axis-and-ohs"

    def snapshot(self):
        return {str(path.relative_to(self.root)): sha256(path.read_bytes()) for path in sorted(self.root.rglob("*")) if path.is_file() and not path.is_symlink() and path.name != "record" and "cache" not in path.parts}


class ShapeTest(AaoCase):
    def test_the_manifest_is_complete(self):
        addon = addons.load(ADDON)
        self.assertEqual((addon.id, addon.needs, addon.tested_version, addon.tested_on), ("axis-and-ohs", ["proton-fs2024", "simulator-closed"], "4.58.2", "2026-10-24"))
        self.assertTrue(addon.touches_addon_files)
        self.assertTrue(addon.can_install)
        self.assertEqual([shortcut["title"] for shortcut in addon.shortcuts], ["Axis and Ohs"])

    def test_the_watcher_waits_for_the_gsx_engine_up_to_five_minutes_and_stops_only_what_it_started(self):
        configured = addons.load(ADDON).watcher
        self.assertEqual(configured["wait_for"], {"process": r"^[A-Za-z]:.*\\couatl64_MSFS2024\.exe", "timeout": 300})
        self.assertEqual(configured["stop"], "with_simulator_if_started")
        self.assertEqual(configured["app"], PATTERN)
        self.assertEqual(configured.get("script", "launch"), "launch")

    def test_the_watcher_pattern_matches_the_program_and_not_the_helper_scripts(self):
        import re
        self.assertTrue(re.search(addons.load(ADDON).watcher["app"], PROGRAM))
        self.assertFalse(re.search(addons.load(ADDON).watcher["app"], "C:\\Program Files\\LorbyAxisAndOhs_MSFS24\\other.exe"))


class ApplyTest(AaoCase):
    def test_apply_says_first_that_it_changes_wine_mono_and_how_to_undo_it(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("changes files of wine-mono", result.stdout.splitlines()[0])
        self.assertIn("addon undo axis-and-ohs", result.stdout.splitlines()[0])

    def test_apply_patches_windowsbase_in_the_prefix_copy_and_never_in_protons_own_folder(self):
        proton_file = self.proton / "files/share/wine/mono/wine-mono-11.3.0" / WINDOWSBASE
        before = proton_file.read_bytes()
        self.script("apply")
        self.assertEqual(clr.state_of(self.copy_path(WINDOWSBASE), "dispatcher-timer"), "patched")
        self.assertEqual(proton_file.read_bytes(), before)
        self.assertEqual(clr.state_of(proton_file, "dispatcher-timer"), "original")
        self.assertEqual((self.backup() / "mono" / WINDOWSBASE).read_bytes(), before)

    def test_the_tool_writes_to_another_file_and_the_original_is_replaced_in_one_move(self):
        self.script("apply")
        calls = [line for line in self.recorded() if line.startswith("dotnet ")]
        self.assertEqual(len(calls), 1)
        self.assertIn(f"{self.tool} --apply {self.copy_path(WINDOWSBASE)} {self.copy_path(WINDOWSBASE)}.fsol-new", calls[0])
        self.assertFalse(Path(f"{self.copy_path(WINDOWSBASE)}.fsol-new").exists())

    def test_a_tool_whose_output_does_not_read_back_as_patched_leaves_the_original(self):
        self.patched.write_bytes(windowsbase())
        before = self.copy_path
        result = self.script("apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not read back as patched", result.stderr)
        self.assertEqual(clr.state_of(before(WINDOWSBASE), "dispatcher-timer"), "original")

    def test_a_windowsbase_with_another_body_is_refused_untouched(self):
        self.script("apply")
        odd = assembly([("System.Windows.Threading", "DispatcherTimer", [("set_Interval", b"\x2A")])])
        self.copy_path(WINDOWSBASE).write_bytes(odd)
        result = self.script("apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn("neither the original nor the patched", result.stderr)
        self.assertEqual(self.copy_path(WINDOWSBASE).read_bytes(), odd)

    def test_apply_turns_the_windows_gac_off_for_mono_and_remembers_that_it_did(self):
        self.script("apply")
        self.assertIn(r"wine reg add HKCU\Software\Wine\Mono\AsmOverrides /v * /t REG_SZ /d gac=n /f", self.recorded())
        self.assertTrue((self.backup() / "asm-override-added").is_file())

    def test_a_gac_override_that_is_already_there_is_not_written_again_nor_claimed(self):
        write(self.prefix / "user.reg", DEFAULT_USER_REG + '[Software\\\\Wine\\\\Mono\\\\AsmOverrides] 1792018790\n"*"="gac=n"\n')
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(line.startswith("wine reg") for line in self.recorded()))
        self.assertFalse((self.backup() / "asm-override-added").exists())

    def test_a_different_gac_override_stays_and_apply_fails_saying_what_to_do(self):
        write(self.prefix / "user.reg", DEFAULT_USER_REG + '[Software\\\\Wine\\\\Mono\\\\AsmOverrides] 1792018790\n"*"="gac=y"\n')
        result = self.script("apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("'gac=y'", result.stderr)
        self.assertIn("did not write", result.stderr)
        self.assertIn("addon apply axis-and-ohs", result.stderr)
        self.assertFalse(any(line.startswith("wine reg") for line in self.recorded()))
        self.assertFalse((self.backup() / "asm-override-added").exists())

    def test_a_second_apply_changes_nothing(self):
        self.script("apply")
        before = self.snapshot()
        calls = len(self.recorded())
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertIn("already the timer patch", result.stdout)
        self.assertEqual([line for line in self.recorded()[calls:] if line.startswith("dotnet")], [])

    def test_apply_does_not_launch_axis_and_ohs_nor_touch_its_files(self):
        before = (self.prefix / EXE).read_bytes()
        self.script("apply")
        self.assertEqual((self.prefix / EXE).read_bytes(), before)
        self.assertFalse(any(PROGRAM in line for line in self.recorded()))


class UndoTest(AaoCase):
    def test_undo_returns_every_file_to_what_it_was(self):
        before = self.snapshot()
        self.script("apply")
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.prefix / "drive_c/windows/mono").exists())
        self.assertFalse(self.backup().exists())

    def test_undo_deletes_the_gac_override_only_when_this_tool_added_it(self):
        self.script("apply")
        write(self.prefix / "user.reg", DEFAULT_USER_REG + '[Software\\\\Wine\\\\Mono\\\\AsmOverrides] 1792018790\n"*"="gac=n"\n')
        self.script("undo")
        self.assertIn(r"wine reg delete HKCU\Software\Wine\Mono\AsmOverrides /v * /f", self.recorded())

    def test_undo_keeps_the_gac_override_while_net48_is_installed(self):
        self.script("apply")
        write(self.prefix / "user.reg", DEFAULT_USER_REG + '[Software\\\\Wine\\\\Mono\\\\AsmOverrides] 1792018790\n"*"="gac=n"\n')
        for folder in ("Framework64", "Framework"):
            path = self.prefix / "drive_c/windows/Microsoft.NET" / folder / "v4.0.30319/clr.dll"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(version_resource("4.8.3761.0"))
        result = self.script("undo")
        self.assertIn(".NET Framework 4.8 is installed", result.stdout)
        self.assertNotIn(r"wine reg delete HKCU\Software\Wine\Mono\AsmOverrides /v * /f", self.recorded())

    def test_undo_leaves_a_gac_override_that_something_else_changed_after_apply(self):
        self.script("apply")
        write(self.prefix / "user.reg", DEFAULT_USER_REG + '[Software\\\\Wine\\\\Mono\\\\AsmOverrides] 1792018790\n"*"="gac=y"\n')
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(line.startswith("wine reg delete") for line in self.recorded()))
        self.assertIn("left * alone", result.stdout)
        self.assertFalse((self.backup() / "asm-override-added").exists())

    def test_undo_leaves_a_gac_override_that_was_there_before(self):
        write(self.prefix / "user.reg", DEFAULT_USER_REG + '[Software\\\\Wine\\\\Mono\\\\AsmOverrides] 1792018790\n"*"="gac=n"\n')
        self.script("apply")
        result = self.script("undo")
        self.assertFalse(any(line.startswith("wine reg delete") for line in self.recorded()))
        self.assertIn("left *=gac=n alone", result.stdout)

    def test_undo_keeps_the_wine_mono_copy_when_gsx_pro_still_uses_it(self):
        self.script("apply")
        users = self.found.compatdata / "flightsim-on-linux-backup/mono-copy/users"
        (users / "gsx-pro").write_text("")
        self.script("undo")
        self.assertEqual(clr.state_of(self.copy_path(WINDOWSBASE), "dispatcher-timer"), "original")
        self.assertTrue((users / "gsx-pro").is_file())

    def test_undo_without_apply_changes_nothing(self):
        before = self.snapshot()
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)


class CheckTest(AaoCase):
    def fixed(self):
        self.script("apply")
        write(self.prefix / "user.reg", DEFAULT_USER_REG + '[Software\\\\Wine\\\\Mono\\\\AsmOverrides] 1792018790\n"*"="gac=n"\n')

    def lines(self, result):
        return [line for line in result.stdout.splitlines() if line.startswith(("ok ", "FAIL "))]

    def fails_with(self, mutate, expected):
        self.fixed()
        mutate()
        result = self.script("check")
        failed = [line for line in self.lines(result) if line.startswith("FAIL ")]
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(len(failed), 1, result.stdout)
        self.assertIn(expected, failed[0])

    def test_a_fixed_prefix_passes_the_three_lines(self):
        self.fixed()
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(self.lines(result)), 3)

    def test_a_prefix_nothing_was_applied_to_fails_each_part(self):
        result = self.script("check")
        text = "\n".join(self.lines(result))
        self.assertEqual(result.returncode, 1)
        self.assertIn("WindowsBase.dll", text)
        self.assertIn("gac=n", text)

    def test_a_proton_without_the_heap_slack_patch_fails(self):
        self.fails_with(self.write_build_without_patch, HEAP_PATCH)

    def write_build_without_patch(self):
        self.write_build(listed=False)

    def test_an_unpatched_windowsbase_fails(self):
        self.fails_with(lambda: self.copy_path(WINDOWSBASE).write_bytes(windowsbase()), "is original")

    def test_a_missing_wine_mono_copy_fails(self):
        self.fails_with(lambda: shutil.rmtree(self.prefix / "drive_c/windows/mono"), "does not exist")

    def test_a_gac_override_that_is_gone_fails(self):
        self.fails_with(lambda: write(self.prefix / "user.reg", DEFAULT_USER_REG), "not 'gac=n'")

    def test_the_check_changes_nothing(self):
        self.fixed()
        before = self.snapshot()
        self.script("check")
        self.assertEqual(self.snapshot(), before)


class InstallTest(AaoCase):
    def test_the_installer_runs_alone_in_the_prefix(self):
        setup = self.root / "LorbyAxisAndOhs_Install_MSFS24.exe"
        setup.write_text("MZ")
        result = self.script("install", FSOL_INSTALLER=str(setup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [f"wine {setup}"])

    def test_without_a_file_it_says_how_to_give_one(self):
        result = self.script("install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("flightsim-on-linux addon install axis-and-ohs", result.stderr)


class LaunchTest(AaoCase):
    def stand_in(self, deadlocks=0, forever=True):
        counter = self.root / "attempts"
        lines = DEADLOCK_LINES.replace('"', '\\"')
        stub(self.proton / "files/bin/wine", (
            "#!/bin/sh\n"
            f'printf "wine %s\\n" "$*" >> "{self.record}"\n'
            f'printf "%s\\n" "$WINEDEBUG|$WINEDLLOVERRIDES|$PWD" >> "{self.record}.env"\n'
            f'echo x >> "{counter}"\n'
            f'attempt=$(wc -l < "{counter}")\n'
            f'if [ "$attempt" -le {deadlocks} ]; then printf "%s" "{lines}"; fi\n'
            f"exec -a '{STAND_IN}' sleep {60 if forever else 1}\n"
        ))
        return counter

    def wait_for(self, condition, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.1)
        return condition()

    def logs(self):
        folder = self.cache / "logs/axis-and-ohs"
        return sorted(folder.glob("aao-*.log"), key=lambda path: (len(path.name), path.name)) if folder.is_dir() else []

    def test_a_missing_program_says_how_to_install_it(self):
        (self.prefix / EXE).unlink()
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("addon install axis-and-ohs", result.stderr)

    def test_it_opens_the_program_in_its_own_folder_with_direct3d_9_off(self):
        counter = self.stand_in()
        result = self.script("launch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.wait_for(counter.exists))
        self.assertTrue(self.wait_for(lambda: Path(f"{self.record}.env").exists()))
        debug, overrides, cwd = Path(f"{self.record}.env").read_text().splitlines()[0].split("|")
        self.assertEqual(debug, "err+all")
        self.assertIn("d3d9=d", overrides)
        self.assertEqual(cwd, str(self.prefix / "drive_c/Program Files/LorbyAxisAndOhs_MSFS24"))
        self.assertEqual([line for line in self.recorded() if line.startswith("wine ")], [f"wine {PROGRAM}"])

    def test_a_program_already_running_is_left_alone(self):
        self.stand_in()
        self.processes.append(start_stand_in(self.root, self.token, STAND_IN))
        time.sleep(0.2)
        result = self.script("launch")
        self.assertEqual(result.returncode, 0)
        time.sleep(0.5)
        self.assertEqual([line for line in self.recorded() if line.startswith("wine ")], [])

    def test_with_the_simulator_running_it_goes_through_the_cli_run_command(self):
        stub(self.root / "cli", f'#!/bin/sh\nprintf "cli %s\\n" "$*" >> "{self.record}"\nprintf "%s\\n" "$WINEDEBUG|$EXTRA_WINEDLLOVERRIDES" >> "{self.record}.env"\n')
        self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertTrue(self.wait_for(lambda: any(line.startswith("cli ") for line in self.recorded())))
        self.assertIn(f"cli run {PROGRAM}", self.recorded())
        self.assertEqual(Path(f"{self.record}.env").read_text().strip(), "err+all|d3d9=d")

    def test_a_startup_deadlock_closes_the_program_and_opens_it_again_once(self):
        counter = self.stand_in(deadlocks=1)
        self.script("launch")
        self.assertTrue(self.wait_for(lambda: counter.exists() and len(counter.read_text().splitlines()) == 2))
        time.sleep(2.5)
        self.assertEqual(len(counter.read_text().splitlines()), 2)
        self.assertEqual(len(self.logs()), 2)
        self.assertIn("Mono deadlock at startup", self.logs()[0].read_text())
        self.assertNotIn("Mono deadlock", self.logs()[1].read_text())

    def test_three_deadlocks_in_a_row_stop_with_a_notice_and_no_fourth_attempt(self):
        counter = self.stand_in(deadlocks=9)
        self.script("launch")
        self.assertTrue(self.wait_for(lambda: any(line.startswith("notice") for line in self.recorded()), 30))
        self.assertEqual(len(counter.read_text().splitlines()), 3)
        self.assertEqual(len(self.logs()), 3)
        self.assertIn("hung at startup 3 times in a row", "\n".join(self.recorded()))

    def test_a_program_that_exits_on_its_own_is_not_retried(self):
        counter = self.stand_in(forever=False)
        self.script("launch")
        self.assertTrue(self.wait_for(lambda: counter.exists() and len(self.logs()) == 1 and "exited with code" in self.logs()[0].read_text(), 20))
        time.sleep(1.5)
        self.assertEqual(len(counter.read_text().splitlines()), 1)

    def test_the_logs_are_private_and_the_oldest_are_pruned(self):
        folder = self.cache / "logs/axis-and-ohs"
        folder.mkdir(parents=True)
        for number in range(25):
            (folder / f"aao-20260101-0000{number:02d}.log").write_text("old")
        self.stand_in(forever=False)
        self.script("launch")
        self.assertTrue(self.wait_for(lambda: len(self.logs()) >= 1 and any("exited with code" in log.read_text() for log in self.logs() if not log.read_text() == "old")))
        remaining = self.logs()
        self.assertLessEqual(len(remaining), 21)
        self.assertNotIn(folder / "aao-20260101-000000.log", remaining)
        newest = max(remaining, key=lambda path: path.stat().st_mtime)
        self.assertEqual(oct(newest.stat().st_mode & 0o777), "0o600")


if __name__ == "__main__":
    unittest.main()
