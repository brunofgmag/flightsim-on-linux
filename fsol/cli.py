import argparse
import json
import os
import signal
import sys
import textwrap
from pathlib import Path

from fsol import addons, container, desktop, doctor, machine, watcher

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


def simulator_environment(args):
    try:
        values = container.simulator_values(machine.discover(), args.names)
    except container.ContainerError as error:
        print(f"sim-env: {error}", file=sys.stderr)
        return 2
    for name, value in values.items():
        print(f"{name}={value}")
    return 0


def addon_root():
    return Path(os.environ.get("FSOL_ADDONS") or ROOT / "addons")


LIST_MARKS = (("can_install", "install"), ("shortcut", "menu"), ("watcher", "watcher"))


def addon_row(addon, found, state):
    return addons.describe(addon, found, state, desktop.has_shortcuts(addon, addons.data_home()), watcher.has_unit(addon, addons.config_home()))


def row_marks(row):
    return "".join(f"  [{label}]" for key, label in LIST_MARKS if row[key])


def addon_list(args):
    found = machine.discover()
    state = addons.state_folder()
    rows = [addon_row(addon, found, state) for addon in addons.load_all(addon_root())]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No add-on fixes yet.")
    for row in rows:
        print(f"{row['id']:24} {row['name']:32} {row['status']}{row_marks(row)}")
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


def lookup(names):
    try:
        return [addons.find(addon_root(), wanted) for wanted in names]
    except addons.AddonError as error:
        print(f"addon: {error}", file=sys.stderr)
        return None


def lacking(chosen, what, has):
    missing = [addon.id for addon in chosen if not has(addon)]
    for wanted in missing:
        print(f"addon: {wanted} has no {what}", file=sys.stderr)
    return bool(missing)


def source_lines(addon):
    info = addon.install
    if not info:
        return []
    cost = {True: " (paid)", False: " (free)"}.get(info.get("paid"), "")
    return [f"  get it from: {info['from']}{cost}", f"  kind: {info['kind']}"]


def prefix_reasons(found):
    if found.sim_running:
        return ["close the simulator and every program of its prefix first; installers run alone"]
    if found.prefix_in_use:
        programs = ", ".join(found.prefix_programs)
        return [f"programs are still open in the simulator's prefix{': ' + programs if programs else ''}; close them first, installers run alone"]
    return []


def installer_blocked(addon, found):
    reasons = [] if (addon.install or {}).get("own_prefix") else prefix_reasons(found)
    for reason in reasons:
        print(f"  FAIL {addon.name}: {reason}")
    return bool(reasons)


def run_installer(addon, path):
    found = machine.discover()
    if installer_blocked(addon, found):
        return 1
    code = addons.run_script(addon, "install", found, CLI, print, extra={"FSOL_INSTALLER": path} if path else None, drop=addons.WINE_UNFRIENDLY)
    if code:
        print(f"  FAIL the installer stopped with exit code {code}")
        return 1
    print(f"  next: flightsim-on-linux addon apply {addon.id}")
    return 0


def install_without_file(addon):
    for line in source_lines(addon):
        print(line)
    if addon.can_install and not addon.install_needs_file:
        return run_installer(addon, None)
    if addon.can_install:
        print(f"  then run: flightsim-on-linux addon install {addon.id} <the file you downloaded>")
    return 0


def install_with_file(addon, given):
    path = Path(given).resolve()
    if not addon.can_install:
        print(f"addon: {addon.id} has no install script", file=sys.stderr)
        return 2
    if not path.is_file():
        print(f"addon: {given} is not a file", file=sys.stderr)
        return 2
    return run_installer(addon, path)


def addon_install(args):
    chosen = lookup([args.addon])
    if chosen is None:
        return 2
    addon = chosen[0]
    if not (addon.install or addon.can_install):
        print(f"addon: {addon.id} has nothing to install", file=sys.stderr)
        return 2
    print(addon.name)
    return install_without_file(addon) if args.file is None else install_with_file(addon, args.file)


def addon_launch(args):
    chosen = lookup([args.addon])
    if chosen is None:
        return 2
    script = args.script or "launch"
    extra = args.args[1:] if args.args[:1] == ["--"] else args.args
    if not addons.SCRIPT_NAME.match(script) or not chosen[0].has_script(script):
        print(f"addon: {args.addon} has no script called {script}", file=sys.stderr)
        return 2
    return addons.call_script(chosen[0], script, machine.discover(), CLI, extra, drop=addons.WINE_UNFRIENDLY)


def addon_shortcut(args):
    chosen = lookup(args.addons)
    if chosen is None or lacking(chosen, "shortcuts", lambda addon: addon.shortcuts):
        return 2
    found = machine.discover()
    for addon in chosen:
        print(addon.name)
        if args.remove:
            removed = desktop.remove_shortcuts(addon, addons.data_home())
            print("\n".join(f"  removed {path}" for path in removed) or "  no menu entry was written")
            continue
        if not addons.is_installed(addon, found):
            print(f"  note {addon.name} is not installed yet; the menu entry starts nothing until it is")
        for path in desktop.write_shortcuts(addon, found, CLI, addons.data_home(), say=lambda line: print(f"  {line}")):
            print(f"  wrote {path}")
        print(f"  flightsim-on-linux addon shortcut {addon.id} --remove takes it out again")
    return 0


def addon_watcher(args):
    chosen = lookup(args.addons)
    if chosen is None or lacking(chosen, "watcher", lambda addon: addon.watcher):
        return 2
    failures = 0
    for addon in chosen:
        print(addon.name)
        if args.off:
            code = watcher.remove_unit(addon, addons.config_home())
            print(f"  {watcher.unit_name(addon)} is off and its file is deleted" if not code else f"  FAIL systemctl --user disable stopped with exit code {code}")
        else:
            code = watcher.install_unit(addon, CLI, addons.config_home())
            print(f"  wrote {watcher.unit_path(addon, addons.config_home())} and switched it on" if not code else f"  FAIL systemctl --user enable --now stopped with exit code {code}")
            print(f"  flightsim-on-linux addon watcher {addon.id} --off takes it out again")
        failures += 1 if code else 0
    return 1 if failures else 0


def addon_watch(args):
    chosen = lookup([args.addon])
    if chosen is None or lacking(chosen, "watcher", lambda addon: addon.watcher):
        return 2
    if watcher.has_loop(chosen[0]):
        print(f"addon: the watcher of {args.addon} is its own script, {chosen[0].watcher['loop']}; `addon watcher {args.addon}` runs it as a service, `addon watch` is not for it", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    return watcher.run(chosen[0], machine.discover(), CLI, discover=machine.discover)


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

    environ = commands.add_parser("sim-env", help="print NAME=value for each variable the running simulator has, for scripts that cannot use run")
    environ.add_argument("names", nargs="+", metavar="NAME")
    environ.set_defaults(run=simulator_environment)

    addon = commands.add_parser("addon", help="list, install, fix, check, start or undo the fix of an add-on").add_subparsers(dest="action", required=True)
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

    installer = addon.add_parser("install", help="say where to get an add-on, or run its installer on the file you downloaded")
    installer.add_argument("addon")
    installer.add_argument("file", nargs="?", help="the installer you downloaded")
    installer.set_defaults(run=addon_install)

    launcher = addon.add_parser("launch", help="run one of an add-on's scripts, by default the one that starts it")
    launcher.add_argument("addon")
    launcher.add_argument("script", nargs="?", help="script of the add-on, default launch")
    launcher.add_argument("args", nargs=argparse.REMAINDER, help="-- arguments for the script")
    launcher.set_defaults(run=addon_launch)

    shortcut = addon.add_parser("shortcut", help="write the application menu entries of an add-on, or take them out")
    shortcut.add_argument("addons", nargs="+", metavar="addon")
    shortcut.add_argument("--remove", action="store_true")
    shortcut.set_defaults(run=addon_shortcut)

    watch = addon.add_parser("watcher", help="switch on the user service that starts an add-on's program with the simulator, or switch it off")
    watch.add_argument("addons", nargs="+", metavar="addon")
    watch.add_argument("--off", action="store_true")
    watch.set_defaults(run=addon_watcher)

    loop = addon.add_parser("watch", help="the watcher loop itself, in the foreground; the user service runs this")
    loop.add_argument("addon")
    loop.set_defaults(run=addon_watch)
    return top


def main(argv=None):
    args = parser().parse_args(argv)
    return args.run(args)
