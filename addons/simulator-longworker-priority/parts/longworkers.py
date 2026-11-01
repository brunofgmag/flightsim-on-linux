import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fsol import machine

THREAD = "ACE: LongWorker"
SERVICE = "flightsim-on-linux-simulator-longworker-priority.service"
WATCHER_ON = "flightsim-on-linux addon watcher simulator-longworker-priority"
WATCHER_OFF = f"{WATCHER_ON} --off"
POLL = 8
PROC = Path("/proc")


def long_workers(pid, proc=PROC):
    threads = []
    for comm in (proc / str(pid) / "task").glob("*/comm"):
        try:
            if comm.read_text().strip() == THREAD:
                threads.append(int(comm.parent.name))
        except OSError:
            continue
    return sorted(threads)


def policy_of(tid):
    try:
        return os.sched_getscheduler(tid)
    except OSError:
        return None


def set_policy(threads, policy):
    changed = 0
    refused = 0
    for tid in threads:
        current = policy_of(tid)
        if current is None or current == policy:
            continue
        try:
            os.sched_setscheduler(tid, policy, os.sched_param(0))
            changed += 1
        except ProcessLookupError:
            continue
        except PermissionError:
            refused += 1
    return changed, refused


def watcher_on():
    return subprocess.run(["systemctl", "--user", "is-active", "--quiet", SERVICE], stdin=subprocess.DEVNULL).returncode == 0


def check(sim, active):
    lines = ["ok the watcher is on" if active else f"FAIL the watcher is off; run: {WATCHER_ON}"]
    if sim is None:
        return [*lines, "ok the simulator is closed, so there are no threads to look at"], active
    threads = long_workers(sim)
    if not threads:
        return [*lines, f"FAIL the simulator has no {THREAD} thread; it may still be starting, or an update renamed them"], False
    idle = sum(policy_of(tid) == os.SCHED_IDLE for tid in threads)
    if idle < len(threads):
        return [*lines, f"FAIL {idle} of {len(threads)} LongWorker threads use only spare CPU time"], False
    return [*lines, f"ok all {len(threads)} LongWorker threads use only spare CPU time"], active


def apply(sim):
    if sim is None:
        return ["already nothing to change: the simulator is closed, and the watcher sets its threads when it runs"]
    changed, _ = set_policy(long_workers(sim), os.SCHED_IDLE)
    if changed:
        return [f"changed {changed} LongWorker threads of the running simulator to use only spare CPU time"]
    return ["already every LongWorker thread of the running simulator uses only spare CPU time"]


def undo(sim, active):
    if active:
        return [f"FAIL the watcher is on and would set the threads again; first run: {WATCHER_OFF}"], False
    if sim is None:
        return ["already nothing to undo: the simulator is closed, and its threads start with the normal policy"], True
    changed, refused = set_policy(long_workers(sim), os.SCHED_OTHER)
    lines = [f"changed {changed} LongWorker threads back to the normal policy"]
    if refused:
        lines.append(f"{refused} threads stay as they are until the simulator closes: your nice limit doesn't let a process leave this policy")
    return lines, True


def loop():
    print("watching for the simulator's LongWorker threads", flush=True)
    while True:
        sim = machine.sim_pid()
        if sim is not None:
            changed, _ = set_policy(long_workers(sim), os.SCHED_IDLE)
            if changed:
                print(f"simulator {sim}: {changed} LongWorker threads now use only spare CPU time", flush=True)
        time.sleep(POLL)


def main(command):
    sim = machine.sim_pid()
    if command == "loop":
        return loop()
    if command == "apply":
        print("\n".join(apply(sim)))
        return 0
    lines, passed = check(sim, watcher_on()) if command == "check" else undo(sim, watcher_on())
    print("\n".join(lines))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
