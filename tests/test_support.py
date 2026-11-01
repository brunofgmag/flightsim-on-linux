import re
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests.support import process_stubs, stand_in_name, start_stand_in

TESTS = Path(__file__).resolve().parent
PROGRAM = "Q:\\Program Files\\Demo\\Demo.exe"


class StandInTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.processes = []
        self.addCleanup(self.stop)

    def stop(self):
        for process in self.processes:
            process.kill()
            process.wait()

    def start(self, token, argv0=PROGRAM):
        process = start_stand_in(self.root, token, argv0, 30)
        self.processes.append(process)
        return process

    def tool(self, name, *args):
        return subprocess.run([str(self.root / "bin" / name), *args], capture_output=True, text=True)

    def test_a_stand_in_named_like_a_program_of_the_real_prefix_is_refused(self):
        for argv0 in ("C:\\Program Files\\Demo\\Demo.exe", "c:\\users\\Demo.exe", "S:\\steamapps\\common\\Demo.exe", "s:\\common\\Demo.exe"):
            with self.subTest(argv0), self.assertRaises(ValueError):
                start_stand_in(self.root, "token", argv0)
        self.assertEqual(self.processes, [])

    def test_any_drive_becomes_the_stand_in_drive(self):
        self.assertEqual(stand_in_name("C:\\a\\b.exe"), "Q:\\a\\b.exe")
        self.assertEqual(stand_in_name("Z:\\a\\b.exe"), "Q:\\a\\b.exe")
        self.assertEqual(stand_in_name("Q:\\a\\b.exe"), "Q:\\a\\b.exe")

    def test_the_stand_in_command_line_starts_with_the_stand_in_drive(self):
        process = self.start("token")
        path = Path(f"/proc/{process.pid}/cmdline")
        deadline = time.monotonic() + 5
        while b"sleep" in path.read_bytes().split(b"\0")[0] or not path.read_bytes():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        self.assertEqual(path.read_bytes().split(b"\0")[0].decode(), PROGRAM)

    def test_the_process_tools_see_the_stand_ins_of_their_own_token_and_no_others(self):
        process_stubs(self.root / "bin", "mine")
        mine = self.start("mine")
        theirs = self.start("theirs")
        found = self.tool("pgrep", "-f", "--", r"^[A-Za-z]:.*\\Demo\.exe")
        self.assertEqual((found.returncode, found.stdout.split()), (0, [str(mine.pid)]))
        killed = self.tool("pkill", "-TERM", "-f", "--", r"^[A-Za-z]:.*\\Demo\.exe")
        self.assertEqual(killed.returncode, 0)
        self.assertEqual(mine.wait(5), -signal.SIGTERM)
        time.sleep(0.1)
        self.assertIsNone(theirs.poll())

    def test_the_process_tools_find_nothing_for_a_pattern_no_stand_in_matches(self):
        process_stubs(self.root / "bin", "mine")
        self.start("mine")
        self.assertEqual(self.tool("pgrep", "-f", "--", r"^[A-Za-z]:.*\\Other\.exe").returncode, 1)

    def test_the_i_option_ignores_the_case_of_the_pattern(self):
        process_stubs(self.root / "bin", "mine")
        mine = self.start("mine")
        self.assertEqual(self.tool("pgrep", "-f", "--", r"demo\.exe").returncode, 1)
        found = self.tool("pgrep", "-if", "--", r"demo\.exe")
        self.assertEqual((found.returncode, found.stdout.split()), (0, [str(mine.pid)]))


class AuditTest(unittest.TestCase):
    def test_no_test_starts_a_process_whose_command_line_starts_with_c_or_s(self):
        offenders = []
        for path in sorted(TESTS.glob("test_*.py")):
            if path.name == "test_support.py":
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"exec -a \\?['\"]?[CcSs]:", line):
                    offenders.append(f"{path.name}:{number}")
        self.assertEqual(offenders, [])

    def test_no_test_skips_because_a_real_program_runs_on_the_machine(self):
        offenders = []
        for path in sorted(TESTS.glob("test_*.py")):
            if path.name == "test_support.py":
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if "skipTest" in line and re.search(r"real .* (is|are) running|real program", line):
                    offenders.append(f"{path.name}:{number}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
