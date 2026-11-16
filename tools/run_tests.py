import argparse
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)


@dataclass
class Outcome:
    module: str
    code: int
    output: str
    seconds: float

    @property
    def passed(self):
        return self.code == 0

    @property
    def tests(self):
        counts = RAN.findall(self.output)
        return int(counts[-1]) if counts else 0


def modules(root):
    return sorted(path.stem for path in (root / "tests").glob("test_*.py"))


def run_module(root, module, verbose):
    command = [sys.executable, "-m", "unittest", *(["-v"] if verbose else []), f"tests.{module}"]
    start = time.monotonic()
    result = subprocess.run(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return Outcome(module, result.returncode, result.stdout, time.monotonic() - start)


def report(outcomes, seconds, verbose):
    width = max(len(outcome.module) for outcome in outcomes)
    for outcome in outcomes:
        if verbose or not outcome.passed:
            print(f"===== {outcome.module}\n{outcome.output.rstrip()}\n")
    for outcome in outcomes:
        status = "ok" if outcome.passed else "FAIL"
        print(f"{status:<4} {outcome.module:<{width}} {outcome.tests:>5} tests {outcome.seconds:6.1f} s")
    failed = [outcome.module for outcome in outcomes if not outcome.passed]
    total = sum(outcome.tests for outcome in outcomes)
    print(f"\n{total} tests in {len(outcomes)} modules, {seconds:.1f} s")
    if failed:
        print(f"{len(failed)} of {len(outcomes)} modules failed: {', '.join(failed)}")
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run each test module of tests/ in a process of its own, several at a time.")
    parser.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1, help="modules at a time (default: the CPU count)")
    parser.add_argument("-v", "--verbose", action="store_true", help="print every module's output, not only the failing ones")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument("module", nargs="*", help="test modules to run, such as test_watcher (default: every tests/test_*.py)")
    args = parser.parse_args(argv)
    chosen = args.module or modules(args.root)
    if not chosen:
        print(f"no test modules in {args.root / 'tests'}")
        return 1
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        outcomes = list(pool.map(lambda module: run_module(args.root, module, args.verbose), chosen))
    return report(outcomes, time.monotonic() - start, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
