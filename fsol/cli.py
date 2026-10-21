import argparse
import json
import os
import sys
import textwrap
from pathlib import Path

from fsol import addons, container, doctor, machine

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "bin" / "flightsim-on-linux"
MARKS = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL", "info": "info"}
DOCTOR_WIDTH = 80
DOCTOR_INDENT = " " * 6
COMMAND_INDENT = " " * 8
DOCTOR_GROUPS = (("Needs attention", ("fail", "warn")), ("Good to know", ("info",)), ("Fine", ("ok",)))


def show_machine(args):
    found = machine.discover()
    if args.json:
        print(json.dumps(found.as_dict(), indent=2))
        return 0
    for key, value in found.as_dict().items():
        print(f"{key:16} {value}")
    return 0


def wrapped(text, first):
    return textwrap.wrap(text, DOCTOR_WIDTH, initial_indent=first, subsequent_indent=DOCTOR_INDENT,
                         break_long_words=False, break_on_hyphens=False)


def check_lines(check):
    lines = wrapped(check.detail, f"{MARKS[check.status]}  ")
    if check.status == "ok":
        return lines
    if check.fix:
        lines += wrapped(check.fix, DOCTOR_INDENT)
    if check.command:
        lines += ["", *(COMMAND_INDENT + line for line in check.command.splitlines())]
    return lines


def group_lines(members):
    if members[0].status == "ok":
        return [line for check in members for line in check_lines(check)]
    spaced = [line for check in members for line in ["", *check_lines(check)]]
    return spaced[1:]


def doctor_text(checks):
    groups = [(title, [check for check in checks if check.status in statuses]) for title, statuses in DOCTOR_GROUPS]
    lines = [" ".join(f"{title}: {len(members)}." for title, members in groups)]
    for title, members in groups:
        if members:
            lines += ["", title, "-" * len(title), "", *group_lines(members)]
    return "\n".join(lines) + "\n"


def show_doctor(args):
    checks = doctor.run(machine.discover())
    if args.json:
        print(json.dumps([check.as_dict() for check in checks], indent=2))
    else:
        print(doctor_text(checks), end="")
    return 1 if any(check.status == "fail" for check in checks) else 0


def run_script(script, extra):
    os.execv(str(script), [str(script), *extra])


def proton_build(args):
    run_script(ROOT / "proton" / "build-proton-fs2024.sh", [args.release] if args.release else [])


def proton_check(args):
    target = args.proton or machine.discover().proton
    if not target:
        print("No Proton given and none found for the simulator.", file=sys.stderr)
        return 2
    run_script(ROOT / "checks" / "check-proton.sh", [str(target)])


def dxvk_build(args):
    run_script(ROOT / "dxvk" / "build-dxvk-fenix.sh", [])


def dxvk_check(args):
    run_script(ROOT / "checks" / "check-dxvk.sh", [str(args.dxvk or dxvk_output())])


def dxvk_output():
    return addons.cache_folder(os.environ) / "dxvk-fenix"


def run_in_container(args):
    try:
        command, env = container.plan(machine.discover(), args.program, os.environ)
    except container.ContainerError as error:
        print(f"run: {error}", file=sys.stderr)
        return 2
    os.execvpe(command[0], command, env)


def addon_root():
    return Path(os.environ.get("FSOL_ADDONS") or ROOT / "addons")


def addon_list(args):
    found = machine.discover()
    state = addons.state_folder()
    rows = [addons.describe(addon, found, state) for addon in addons.load_all(addon_root())]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No add-on fixes yet.")
    for row in rows:
        print(f"{row['id']:24} {row['name']:32} {row['status']}")
    return 0


def blocked(addon, found):
    unmet = addons.unmet_needs(addon, found)
    for reason in unmet:
        print(f"  FAIL {addon.name}: {reason}")
    return bool(unmet)


def apply_one(addon, found, state):
    print(addon.name)
    if blocked(addon, found):
        return 1
    if addon.touches_addon_files:
        print(f"  note this changes files of the add-on itself; flightsim-on-linux addon undo {addon.id} restores them")
    code = addons.run_script(addon, "apply", found, CLI, print)
    if code:
        print(f"  FAIL the fix stopped with exit code {code}")
        return 1
    code = addons.run_script(addon, "check", found, CLI, print)
    if code:
        addons.forget(addon, state)
        print("  FAIL the fix ran, but its check did not pass")
        return 1
    addons.remember(addon, state)
    return 0


def check_one(addon, found, state):
    print(addon.name)
    code = addons.run_script(addon, "check", found, CLI, print)
    if code:
        addons.forget(addon, state)
    return 1 if code else 0


def undo_one(addon, found, state):
    print(addon.name)
    if blocked(addon, found):
        return 1
    code = addons.run_script(addon, "undo", found, CLI, print)
    if code:
        print(f"  FAIL undo stopped with exit code {code}")
        return 1
    addons.forget(addon, state)
    return 0


def addon_action(action):
    def run(args):
        found = machine.discover()
        state = addons.state_folder()
        try:
            chosen = [addons.find(addon_root(), wanted) for wanted in args.addons]
            if not chosen and action is check_one:
                chosen = [addon for addon in addons.load_all(addon_root()) if addons.is_installed(addon, found)]
        except addons.AddonError as error:
            print(f"addon: {error}", file=sys.stderr)
            return 2
        failures = 0
        for number, addon in enumerate(chosen):
            if number:
                print()
            failures += action(addon, found, state)
        return 1 if failures else 0
    return run


def parser():
    top = argparse.ArgumentParser(prog="flightsim-on-linux", description="Check Microsoft Flight Simulator 2024 under Proton and prepare it for Windows add-ons.")
    commands = top.add_subparsers(dest="command", required=True)

    found = commands.add_parser("machine", help="show what was found: Steam, libraries, prefix, Proton, launch options")
    found.add_argument("--json", action="store_true")
    found.set_defaults(run=show_machine)

    health = commands.add_parser("doctor", help="check the simulator setup and say how to fix what is missing")
    health.add_argument("--json", action="store_true")
    health.set_defaults(run=show_doctor)

    proton = commands.add_parser("proton", help="build or check Proton FS2024").add_subparsers(dest="action", required=True)
    build = proton.add_parser("build", help="build Proton FS2024 from a proton-cachyos release and swap it in")
    build.add_argument("release", nargs="?", help="proton-cachyos release tag, default the verified one")
    build.set_defaults(run=proton_build)
    check = proton.add_parser("check", help="test a Proton in a throwaway prefix")
    check.add_argument("proton", nargs="?", type=Path, help="Proton folder, default the one the simulator uses")
    check.set_defaults(run=proton_check)

    dxvk = commands.add_parser("dxvk", help="build or check the DXVK the Fenix A320 displays use in GPU mode").add_subparsers(dest="action", required=True)
    dxvk_builder = dxvk.add_parser("build", help="build DXVK with the three Fenix patches, check it and keep it in the cache")
    dxvk_builder.set_defaults(run=dxvk_build)
    dxvk_checker = dxvk.add_parser("check", help="test a DXVK folder in a throwaway prefix")
    dxvk_checker.add_argument("dxvk", nargs="?", type=Path, help="folder with d3d11.dll and dxgi.dll, default the last build")
    dxvk_checker.set_defaults(run=dxvk_check)

    inside = commands.add_parser("run", help="run a Windows program inside the running simulator's container, with its environment")
    inside.add_argument("program", nargs=argparse.REMAINDER, help="[NAME=value ...] program.exe [arguments]")
    inside.set_defaults(run=run_in_container)

    addon = commands.add_parser("addon", help="list, fix, check or undo the fix of an add-on").add_subparsers(dest="action", required=True)
    listing = addon.add_parser("list", help="show every add-on with a fix here and whether it is installed and fixed")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(run=addon_list)
    for name, action, text, required in (
        ("apply", apply_one, "apply an add-on's fix, then check it", True),
        ("check", check_one, "check that an add-on's fix works, default every installed add-on", False),
        ("undo", undo_one, "undo an add-on's fix", True),
    ):
        command = addon.add_parser(name, help=text)
        command.add_argument("addons", nargs="+" if required else "*", metavar="addon")
        command.set_defaults(run=addon_action(action))
    return top


def main(argv=None):
    args = parser().parse_args(argv)
    return args.run(args)
