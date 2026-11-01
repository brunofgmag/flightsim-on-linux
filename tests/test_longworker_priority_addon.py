import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addons" / "simulator-longworker-priority"

spec = importlib.util.spec_from_file_location("longworker_priority_parts", ADDON / "parts" / "longworkers.py")
longworkers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(longworkers)

CHILD = """
import pathlib
import threading
import time

ready = threading.Barrier(4)


def worker(name):
    pathlib.Path(f"/proc/self/task/{threading.get_native_id()}/comm").write_text(name)
    ready.wait()
    time.sleep(120)


for name in ("ACE: LongWorker", "ACE: LongWorker", "ACE: WorkerThre"):
    threading.Thread(target=worker, args=(name,), daemon=True).start()
ready.wait()
print("ready", flush=True)
time.sleep(120)
"""


class ThreadsTest(unittest.TestCase):
    def setUp(self):
        self.child = subprocess.Popen([sys.executable, "-I", "-c", CHILD], stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.child.stdout.close)
        self.addCleanup(self.child.wait)
        self.addCleanup(self.child.kill)
        self.child.stdout.readline()
        self.threads = longworkers.long_workers(self.child.pid)

    def other_thread(self):
        tasks = Path(f"/proc/{self.child.pid}/task")
        return next(int(task.name) for task in tasks.iterdir() if (task / "comm").read_text().strip() == "ACE: WorkerThre")

    def test_it_finds_the_threads_by_name_and_no_others(self):
        self.assertEqual(len(self.threads), 2)
        self.assertNotIn(self.other_thread(), self.threads)

    def test_it_moves_the_long_workers_and_leaves_the_rest_alone(self):
        self.assertEqual(longworkers.set_policy(self.threads, os.SCHED_IDLE), (2, 0))
        self.assertEqual({os.sched_getscheduler(tid) for tid in self.threads}, {os.SCHED_IDLE})
        self.assertEqual(os.sched_getscheduler(self.other_thread()), os.SCHED_OTHER)

    def test_a_second_pass_changes_nothing(self):
        longworkers.set_policy(self.threads, os.SCHED_IDLE)
        self.assertEqual(longworkers.set_policy(self.threads, os.SCHED_IDLE), (0, 0))

    def test_a_refused_thread_is_counted_and_nothing_stops(self):
        with mock.patch.object(longworkers.os, "sched_setscheduler", side_effect=PermissionError):
            self.assertEqual(longworkers.set_policy(self.threads, os.SCHED_IDLE), (0, 2))

    def test_a_thread_that_ended_is_skipped(self):
        with mock.patch.object(longworkers.os, "sched_setscheduler", side_effect=ProcessLookupError):
            self.assertEqual(longworkers.set_policy(self.threads, os.SCHED_IDLE), (0, 0))

    def test_check_passes_when_every_long_worker_is_set(self):
        longworkers.set_policy(self.threads, os.SCHED_IDLE)
        lines, passed = longworkers.check(self.child.pid, True)
        self.assertTrue(passed)
        self.assertIn("ok all 2 LongWorker threads use only spare CPU time", lines)

    def test_check_fails_without_the_fix(self):
        lines, passed = longworkers.check(self.child.pid, True)
        self.assertFalse(passed)
        self.assertIn("FAIL 0 of 2 LongWorker threads use only spare CPU time", lines)

    def test_check_fails_when_one_thread_was_missed(self):
        longworkers.set_policy(self.threads[:1], os.SCHED_IDLE)
        lines, passed = longworkers.check(self.child.pid, True)
        self.assertFalse(passed)
        self.assertIn("FAIL 1 of 2 LongWorker threads use only spare CPU time", lines)

    def test_check_fails_with_the_threads_set_but_the_watcher_off(self):
        longworkers.set_policy(self.threads, os.SCHED_IDLE)
        lines, passed = longworkers.check(self.child.pid, False)
        self.assertFalse(passed)
        self.assertIn(f"FAIL the watcher is off; run: {longworkers.WATCHER_ON}", lines)

    def test_apply_says_what_it_changed_and_then_that_nothing_is_left(self):
        self.assertEqual(longworkers.apply(self.child.pid), ["changed 2 LongWorker threads of the running simulator to use only spare CPU time"])
        self.assertEqual(longworkers.apply(self.child.pid), ["already every LongWorker thread of the running simulator uses only spare CPU time"])

    def test_undo_reports_threads_it_could_not_move_back(self):
        longworkers.set_policy(self.threads, os.SCHED_IDLE)
        with mock.patch.object(longworkers.os, "sched_setscheduler", side_effect=PermissionError):
            lines, passed = longworkers.undo(self.child.pid, False)
        self.assertTrue(passed)
        self.assertEqual(lines[0], "changed 0 LongWorker threads back to the normal policy")
        self.assertIn("2 threads stay as they are until the simulator closes", lines[1])


class NoThreadsTest(unittest.TestCase):
    def test_a_running_simulator_without_the_threads_fails(self):
        lines, passed = longworkers.check(os.getpid(), True)
        self.assertFalse(passed)
        self.assertIn("an update renamed them", lines[-1])

    def test_a_closed_simulator_passes_with_the_watcher_on(self):
        self.assertTrue(longworkers.check(None, True)[1])

    def test_a_closed_simulator_fails_with_the_watcher_off(self):
        self.assertFalse(longworkers.check(None, False)[1])

    def test_undo_refuses_while_the_watcher_would_set_the_threads_again(self):
        lines, passed = longworkers.undo(None, True)
        self.assertFalse(passed)
        self.assertIn(longworkers.WATCHER_OFF, lines[0])

    def test_undo_has_nothing_to_do_with_the_simulator_closed(self):
        self.assertTrue(longworkers.undo(None, False)[1])

    def test_apply_has_nothing_to_do_with_the_simulator_closed(self):
        self.assertIn("simulator is closed", longworkers.apply(None)[0])


if __name__ == "__main__":
    unittest.main()
