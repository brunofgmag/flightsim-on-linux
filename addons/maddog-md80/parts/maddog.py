import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "addons" / "maddog-md80" / "manager"))

from fsol import addons, registry, simconnect, watcher  # noqa: E402
from maddog_manager.locations import MaddogFiles, find_maddog_root, windows_to_linux  # noqa: E402
from maddog_manager.registration import registered  # noqa: E402

ADDON_ID = "maddog-md80"
PACKAGE = "lsh-maddogx-aircraft"
OVERRIDE_KEY = r"software\wine\appdefaults\mdclient.exe\dlloverrides"
OVERRIDE_VALUE = "mscoree"
DOTNET_GROUPS = ("clr", "mscoree")
DOTNET_PART = ROOT / "addons" / "dotnet48" / "parts" / "dotnet48.py"
WINESERVER_COST = "on the test machine MDClient on wine-mono cost the wineserver 40 to 44% of a core, and on .NET Framework 4.8 10 to 19%"
ADD_DOTNET = "flightsim-on-linux addon apply dotnet48"
LABELS = {"prefix": "prefix", "community": "Community folder", "base": "simulator base folder"}


def dotnet48_module():
    spec = importlib.util.spec_from_file_location("dotnet48_base", DOTNET_PART)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dotnet48_lines(prefix):
    module = dotnet48_module()
    return [line for group in DOTNET_GROUPS for line in module.group_lines(prefix, group)]


def dotnet48_present(prefix):
    return all(good for good, _ in dotnet48_lines(prefix))


def override_state(prefix):
    file = Path(prefix) / "user.reg"
    value = registry.value(registry.load(file), OVERRIDE_KEY, OVERRIDE_VALUE) if file.is_file() else None
    if value is None:
        return "absent"
    return "native" if value == "native" else f"other:{value}"


def watcher_on():
    return watcher.has_unit(SimpleNamespace(id=ADDON_ID), addons.config_home(os.environ))


def package_line(community):
    manifest = Path(community) / PACKAGE / "manifest.json"
    if manifest.is_file():
        return True, f"the Maddog package {PACKAGE} is in Community"
    return False, f"{PACKAGE} is not in {community}; install the Maddog with the Leonardo installer: flightsim-on-linux addon install maddog-md80 <installer>"


def load_manager_line(prefix, community):
    root = find_maddog_root(prefix)
    if root is None:
        return False, "the Maddog X Files folder is not in the prefix; install the Maddog first"
    files = MaddogFiles(Path(prefix), root, None)
    stored = files.lm_path("FSPath")
    if stored is None:
        return False, f"the FSPath in {files.lm_ini} is not a folder that exists; set it in the Load Manager"
    if stored.resolve() != Path(community).resolve():
        return False, f"the Load Manager keeps the Maddog in {stored}, which is not the simulator's {community}; point its FSPath at Community"
    return True, "the Load Manager's FSPath is the simulator's Community folder"


def registration_line(prefix):
    root = find_maddog_root(prefix)
    if root is not None and registered(MaddogFiles(Path(prefix), root, None)):
        return True, "registration.ini holds a key and a purchase code"
    return False, "the MD-80 is not registered; run: flightsim-on-linux addon launch maddog-md80 register"


def dotnet_line(prefix):
    failing = [text for good, text in dotnet48_lines(prefix) if not good]
    if failing:
        return False, f"the prefix lacks .NET Framework 4.8 ({failing[0]}); add it with: {ADD_DOTNET}"
    return True, "the prefix has .NET Framework 4.8 and Microsoft's mscoree.dll"


def override_line(prefix):
    state = override_state(prefix)
    if state == "native":
        return True, "MDClient.exe runs on .NET Framework 4.8 (mscoree is native for it)"
    detail = "has no mscoree override" if state == "absent" else f"has mscoree set to {state.split(':', 1)[1]}"
    return False, f"AppDefaults\\MDClient.exe\\DllOverrides {detail}, so MDClient runs on wine-mono; {WINESERVER_COST}; run: flightsim-on-linux addon apply maddog-md80"


def simconnect_line(base):
    port = simconnect.port(SimpleNamespace(base=Path(base)))
    if port is None or port < simconnect.PRIVILEGED_BELOW:
        return False, f"SimConnect has no TCP port above 1023 for the watcher to read the aircraft from; run: flightsim-on-linux addon apply simconnect-tcp"
    return True, f"SimConnect listens on TCP port {port}, where the watcher reads the loaded aircraft"


def guarded(line, **places):
    for name, value in places.items():
        if not value:
            return False, f"no {LABELS[name]} found; run: flightsim-on-linux machine"
    return line(*places.values())


def check_lines(prefix, community, base):
    lines = [
        guarded(package_line, community=community),
        guarded(load_manager_line, prefix=prefix, community=community),
        guarded(registration_line, prefix=prefix),
        guarded(dotnet_line, prefix=prefix),
        guarded(override_line, prefix=prefix),
    ]
    if watcher_on():
        lines.append(guarded(simconnect_line, base=base))
    return lines


def command_check(prefix, community, base):
    lines = check_lines(prefix, community, base)
    for good, text in lines:
        print(f"ok {text}" if good else f"FAIL {text}")
    return 0 if all(good for good, _ in lines) else 1


def command_override(prefix):
    print(override_state(prefix))
    return 0


def command_dotnet(prefix):
    print("present" if dotnet48_present(prefix) else "missing")
    return 0


def main(argv):
    if argv[:1] == ["check"] and len(argv) == 4:
        return command_check(*argv[1:])
    if argv[:1] == ["override"] and len(argv) == 2 and argv[1]:
        return command_override(argv[1])
    if argv[:1] == ["dotnet"] and len(argv) == 2 and argv[1]:
        return command_dotnet(argv[1])
    print("usage: maddog.py check <prefix> <Community folder> <base folder> | override <prefix> | dotnet <prefix>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
