import argparse
import json
import os
import sys
from pathlib import Path

from fsol import doctor, machine

ROOT = Path(__file__).resolve().parent.parent
MARKS = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL", "info": "info"}


def show_machine(args):
    found = machine.discover()
    if args.json:
        print(json.dumps(found.as_dict(), indent=2))
        return 0
    for key, value in found.as_dict().items():
        print(f"{key:16} {value}")
    return 0


def show_doctor(args):
    checks = doctor.run(machine.discover())
    if args.json:
        print(json.dumps([check.as_dict() for check in checks], indent=2))
    else:
        for check in checks:
            print(f"{MARKS[check.status]}  {check.detail}")
            if check.fix and check.status != "ok":
                print(f"      {check.fix}")
    return 1 if any(check.status == "fail" for check in checks) else 0


def run_script(script, extra):
    os.execv(str(script), [str(script), *extra])


def proton_build(args):
    run_script(ROOT / "proton" / "build-proton-fenix.sh", [args.release] if args.release else [])


def proton_check(args):
    target = args.proton or machine.discover().proton
    if not target:
        print("No Proton given and none found for the simulator.", file=sys.stderr)
        return 2
    run_script(ROOT / "checks" / "check-proton.sh", [str(target)])


def parser():
    top = argparse.ArgumentParser(prog="flightsim-on-linux", description="Set up Windows add-ons for Microsoft Flight Simulator 2024 under Proton.")
    commands = top.add_subparsers(dest="command", required=True)

    found = commands.add_parser("machine", help="show what was found: Steam, libraries, prefix, Proton, launch options")
    found.add_argument("--json", action="store_true")
    found.set_defaults(run=show_machine)

    health = commands.add_parser("doctor", help="check the simulator setup and say how to fix what is missing")
    health.add_argument("--json", action="store_true")
    health.set_defaults(run=show_doctor)

    proton = commands.add_parser("proton", help="build or check Proton Fenix A320").add_subparsers(dest="action", required=True)
    build = proton.add_parser("build", help="build Proton Fenix A320 from a proton-cachyos release")
    build.add_argument("release", nargs="?", help="proton-cachyos release tag, default the verified one")
    build.set_defaults(run=proton_build)
    check = proton.add_parser("check", help="test a Proton in a throwaway prefix")
    check.add_argument("proton", nargs="?", type=Path, help="Proton folder, default the one the simulator uses")
    check.set_defaults(run=proton_check)
    return top


def main(argv=None):
    args = parser().parse_args(argv)
    return args.run(args)
