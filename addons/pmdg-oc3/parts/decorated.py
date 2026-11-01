import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fsol import registry  # noqa: E402

KEY = r"Software\Wine\AppDefaults\PMDG OC3.exe\X11 Driver"
NAME = "Decorated"


def current(prefix):
    file = Path(prefix) / "user.reg"
    return registry.value(registry.load(file), KEY, NAME) if file.is_file() else None


def command_value(prefix):
    found = current(prefix)
    print("" if found is None else found)
    return 0


def command_check(prefix):
    if not prefix:
        print("FAIL no prefix found; run: flightsim-on-linux machine")
        return 1
    found = current(prefix)
    if found == "N":
        print("ok PMDG OC3.exe is set not to ask the window manager for a frame (Decorated=N)")
        return 0
    print(f"FAIL Decorated for PMDG OC3.exe is {found!r}, not 'N', so the window manager's title bar covers OC3's own; run: flightsim-on-linux addon apply pmdg-oc3")
    return 1


COMMANDS = {"value": command_value, "check": command_check}

if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in COMMANDS:
        print("usage: decorated.py value|check <prefix>", file=sys.stderr)
        sys.exit(2)
    sys.exit(COMMANDS[sys.argv[1]](sys.argv[2]))
