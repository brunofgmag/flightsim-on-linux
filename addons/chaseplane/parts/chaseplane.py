import shlex
import sys
from pathlib import Path

PARTS = Path(__file__).resolve().parent
ROOT = PARTS.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PARTS))

import p42  # noqa: E402
from fsol import machine, proton_build, registry  # noqa: E402

VARIABLE = "STEAM_COMPAT_LIBRARY_PATHS"
BRIDGE_KEY = "Software\\Wine\\AppDefaults\\CP MSFS Bridge.exe\\DllOverrides"
WINESERVER = "bin/wineserver"
POINTER_MODULE = "lib/wine/x86_64-unix/winewayland.so"
PROTON_FIXES = (
    ("server-process-vm-eperm-per-call.patch", WINESERVER, "Proton's wineserver falls back to ptrace for one failed memory read only, so ChasePlane reading the simulator no longer stops its main thread"),
    ("winewayland-hidden-cursor-window-clip-confines.patch", POINTER_MODULE, "Proton confines the pointer instead of locking it when the cursor is hidden, so the arrow does not freeze"),
    ("winewayland-commit-pointer-constraint-changes.patch", POINTER_MODULE, "Proton commits the surface after each pointer lock or confinement, so KWin 6.8 applies it"),
    ("winewayland-small-clip-warps-pointer-inside.patch", POINTER_MODULE, "Proton moves the pointer inside a small clip first, as Windows does, so the drag arrow does not wander off"),
    ("winewayland-clip-at-least-one-pixel.patch", POINTER_MODULE, "Proton never confines the pointer to an empty region, so a one-pixel clip holds at 125% scale"),
    ("winewayland-warp-before-confining.patch", POINTER_MODULE, "Proton warps the pointer inside a small clip before it confines it, so KWin 6.7 takes the confinement"),
)


def line(good, text):
    print(f"ok {text}" if good else f"FAIL {text}")
    return good


def library_of(sim_install):
    return Path(sim_install).parents[2] if sim_install and len(Path(sim_install).parents) > 2 else None


def option_text(sim_install):
    return f"{VARIABLE}={shlex.quote(str(library_of(sim_install)))}"


def library_paths(options):
    for word in shlex.split(options or "", posix=True):
        if word.startswith(f"{VARIABLE}="):
            return word.split("=", 1)[1].split(":")
    return []


def option_state(steam, sim_install):
    library = library_of(sim_install)
    if not steam or library is None:
        return None, None
    options = machine.launch_options(Path(steam))
    holding = [user for user, text in options.items() if str(library) in library_paths(text)]
    return bool(holding), options


def option_line(steam, sim_install):
    library = library_of(sim_install)
    if library is None or not steam:
        return line(False, "no simulator installation or Steam folder found; run: flightsim-on-linux machine")
    present, options = option_state(steam, sim_install)
    if present:
        return line(True, f"the launch options of the simulator in Steam hold {option_text(sim_install)}, so the bridge sees steamapps\\common in the simulator's path")
    if not options:
        return line(False, f"Steam has no launch options saved for the simulator; add {option_text(sim_install)} before %command% in its properties")
    return line(False, f"the launch options of the simulator in Steam do not hold {option_text(sim_install)}; add it before %command% in the simulator's properties in Steam")


def drive_line(prefix, sim_install):
    library = library_of(sim_install)
    drive = Path(prefix) / "dosdevices" / "s:"
    target = Path(drive).readlink() if drive.is_symlink() else None
    if library is None:
        return line(False, "no simulator installation found; run: flightsim-on-linux machine")
    if target is not None and Path(target) == library:
        return line(True, f"drive S: of the prefix is {library}, the library itself, so the simulator's path has steamapps\\common")
    return line(False, f"drive S: of the prefix is {target}, not {library}; start the simulator once with {option_text(sim_install)} in its launch options")


def bridge_line(prefix):
    user = Path(prefix) / "user.reg"
    keys = registry.load(user) if user.is_file() else {}
    found = registry.value(keys, BRIDGE_KEY, "httpapi")
    if found == "":
        return line(True, "httpapi is off for CP MSFS Bridge.exe, so its web socket server falls back to the plain one Wine can run")
    return line(False, f"httpapi for CP MSFS Bridge.exe is {found!r}, not off: the bridge panel stays on 'loading'; run: flightsim-on-linux addon apply chaseplane")


def proton_lines(proton):
    if not proton:
        return [line(False, "no Proton found; run: flightsim-on-linux machine")]
    return [line(*proton_build.patch_line(Path(proton), patch, module, text)) for patch, module, text in PROTON_FIXES]


def check(prefix, proton, steam, sim_install):
    results = p42.check(prefix, "chaseplane")
    results += [bridge_line(prefix), option_line(steam, sim_install), drive_line(prefix, sim_install)]
    return results + proton_lines(proton)


def main(argv):
    if len(argv) == 5 and argv[0] == "check":
        return 0 if all(check(*argv[1:])) else 1
    if len(argv) == 3 and argv[0] == "option":
        present, _ = option_state(argv[1], argv[2])
        print(option_text(argv[2]))
        return 0 if present else 1
    print("usage: chaseplane.py check <prefix> <Proton folder> <Steam folder> <simulator install> | option <Steam folder> <simulator install>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
