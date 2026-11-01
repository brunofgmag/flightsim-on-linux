import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsol import exe_xml  # noqa: E402

USAGE = "usage: exe-entry.py state <exe.xml> <path suffix> | check <prefix> <exe.xml> <path suffix> <label> <hint>"


def command_state(file, suffix):
    print(exe_xml.state_ending(file, suffix)[0])
    return 0


def command_check(prefix, file, suffix, label, hint):
    try:
        state, path = exe_xml.state_ending(file, suffix)
    except (exe_xml.ExeXmlError, OSError) as error:
        print(f"FAIL exe.xml cannot be read: {error}")
        return 1
    problems = {
        "no-file": f"FAIL exe.xml does not exist at {file}; {hint}",
        "absent": f"FAIL exe.xml has no entry for {label}; {hint}",
        "disabled": f"FAIL exe.xml has the entry for {label} with Disabled true, so the simulator won't start it",
    }
    if state in problems:
        print(problems[state])
        return 1
    if exe_xml.linux_path(prefix, path) is None:
        print(f"FAIL exe.xml starts {path} for {label}, and that file does not exist")
        return 1
    print(f"ok exe.xml starts {label} with the simulator")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    shapes = {"state": 3, "check": 6}
    if not args or shapes.get(args[0]) != len(args):
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    sys.exit({"state": command_state, "check": command_check}[args[0]](*args[1:]))
