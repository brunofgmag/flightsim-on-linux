import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "tools" / "run_tests.py"

PASSING = """import unittest


class PassingTest(unittest.TestCase):
    def test_one(self):
        self.assertTrue(True)

    def test_two(self):
        self.assertTrue(True)
"""

FAILING = """import unittest


class FailingTest(unittest.TestCase):
    def test_it_breaks(self):
        self.assertEqual(1, 2, "the failing module's own message")
"""

BROKEN_IMPORT = """import a_module_that_does_not_exist
"""

EMPTY = """import unittest
"""

SLEEPING = """import time
import unittest


class SleepingTest(unittest.TestCase):
    def test_sleeps(self):
        time.sleep(1.0)
"""


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "__init__.py").write_text("")

    def tearDown(self):
        self.temporary.cleanup()

    def module(self, name, text):
        (self.root / "tests" / f"{name}.py").write_text(text)

    def run_tests(self, *arguments):
        return subprocess.run([sys.executable, str(RUNNER), "--root", str(self.root), *arguments], capture_output=True, text=True, timeout=60)

    def test_passing_modules_exit_zero_and_count_every_test(self):
        self.module("test_alpha", PASSING)
        self.module("test_beta", PASSING)
        result = self.run_tests()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("4 tests in 2 modules", result.stdout)
        self.assertRegex(result.stdout, r"(?m)^ok +test_alpha +2 tests")
        self.assertRegex(result.stdout, r"(?m)^ok +test_beta +2 tests")

    def test_a_failing_module_fails_the_run_and_shows_its_output(self):
        self.module("test_alpha", PASSING)
        self.module("test_broken", FAILING)
        result = self.run_tests()
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stdout, r"(?m)^FAIL +test_broken")
        self.assertIn("the failing module's own message", result.stdout)
        self.assertIn("1 of 2 modules failed: test_broken", result.stdout)

    def test_a_module_that_cannot_import_fails_the_run(self):
        self.module("test_alpha", PASSING)
        self.module("test_import", BROKEN_IMPORT)
        result = self.run_tests()
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stdout, r"(?m)^FAIL +test_import")
        self.assertIn("a_module_that_does_not_exist", result.stdout)

    def test_a_module_without_tests_fails_the_run(self):
        self.module("test_alpha", PASSING)
        self.module("test_empty", EMPTY)
        result = self.run_tests()
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stdout, r"(?m)^FAIL +test_empty +0 tests")

    def test_no_module_at_all_fails_the_run(self):
        result = self.run_tests()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no test modules", result.stdout + result.stderr)

    def test_modules_named_on_the_command_line_run_alone(self):
        self.module("test_alpha", PASSING)
        self.module("test_broken", FAILING)
        result = self.run_tests("test_alpha")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("2 tests in 1 modules", result.stdout)
        self.assertNotIn("test_broken", result.stdout)

    def test_modules_run_at_the_same_time_up_to_the_jobs_given(self):
        for name in ("test_one", "test_two", "test_three"):
            self.module(name, SLEEPING)
        start = time.monotonic()
        parallel = self.run_tests("-j", "3")
        parallel_seconds = time.monotonic() - start
        start = time.monotonic()
        serial = self.run_tests("-j", "1")
        serial_seconds = time.monotonic() - start
        self.assertEqual((parallel.returncode, serial.returncode), (0, 0))
        self.assertLess(parallel_seconds, 2.0)
        self.assertGreaterEqual(serial_seconds, 3.0)

    def test_verbose_shows_every_module_output(self):
        self.module("test_alpha", PASSING)
        result = self.run_tests("-v")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("test_one (tests.test_alpha.PassingTest.test_one) ... ok", result.stdout)


class WhereItRunsTest(unittest.TestCase):
    def test_the_ci_tests_job_and_the_readme_use_the_runner(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("run: python3 tools/run_tests.py -v", workflow)
        self.assertNotIn("unittest discover", workflow)
        readme = (ROOT / "README.md").read_text()
        self.assertIn("python3 tools/run_tests.py", readme)
        self.assertNotIn("unittest discover", readme)


if __name__ == "__main__":
    unittest.main()
