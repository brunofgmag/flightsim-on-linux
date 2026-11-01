import sys
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from fsol import registry  # noqa: E402

StandIn = namedtuple("StandIn", "dll key value_name wanted passed consequence")

RESOLVER_CLSID = "{660B90C8-73A9-4B58-8CAE-355B7F55341B}"
STAND_INS = (
    StandIn(
        "AppResolverShim.dll", f"Software\\Classes\\CLSID\\{RESOLVER_CLSID}\\InprocServer32", "CodeBase", "file:///C:/appresolver/AppResolverShim.dll",
        "AppResolverShim.dll answers for the CAppResolver class, so Parallel 42 programs get the application id they ask Windows for",
        "the program stops at its first window with 'class not registered'",
    ),
    StandIn(
        "toaststub.dll", "Software\\Microsoft\\WindowsRuntime\\ActivatableClassId\\Windows.UI.Notifications.ToastNotificationManager", "DllPath", "C:\\appresolver\\toaststub.dll",
        "toaststub.dll answers for the ToastNotificationManager class, so Parallel 42 programs can create their notifier",
        "the program stops when it creates its notifier",
    ),
)


def line(good, text):
    print(f"ok {text}" if good else f"FAIL {text}")
    return good


def load(prefix, name):
    path = Path(prefix) / name
    return registry.load(path) if path.is_file() else {}


def stand_in_line(prefix, system, item, addon):
    path = Path(prefix) / "drive_c/appresolver" / item.dll
    if not path.is_file():
        return line(False, f"C:\\appresolver\\{item.dll} does not exist, and {item.consequence}; run: flightsim-on-linux addon apply {addon}")
    found = registry.value(system, item.key, item.value_name)
    if found != item.wanted:
        return line(False, f"the registry holds {found!r} for {item.value_name} of {item.key.rsplit(chr(92), 1)[-1]}, not {item.wanted!r}, and {item.consequence}; run: flightsim-on-linux addon apply {addon}")
    return line(True, item.passed)


def check(prefix, addon):
    system = load(prefix, "system.reg")
    return [stand_in_line(prefix, system, item, addon) for item in STAND_INS]


def main(argv):
    if len(argv) != 3 or argv[0] != "check":
        print("usage: p42.py check <prefix> <add-on id>", file=sys.stderr)
        return 2
    return 0 if all(check(argv[1], argv[2])) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
