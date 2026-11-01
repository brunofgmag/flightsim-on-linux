import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from fsol import proton_build, registry  # noqa: E402

PROGRAM_GLOB = "drive_c/users/*/AppData/Local/Programs/BlueBird Simulations/NEST/bbs_nest.exe"
REGISTRY_KEYS = (r"software\bluebirdsimulations\nest", "software/bluebirdsimulations/nest")
COMMUNITY_VALUE = "MSFS2024CommunityPath"
PACE_PATCH = "winewayland-pace-hidden-gl-swaps.patch"
PACE_MODULE = "lib/wine/x86_64-unix/winewayland.so"
LABELS = {"prefix": "prefix", "community": "Community folder", "proton": "Proton"}


def child(base, name):
    candidate = base / name
    if candidate.exists() or not base.is_dir():
        return candidate
    return next((entry for entry in base.iterdir() if entry.name.lower() == name.lower()), candidate)


def windows_to_linux(prefix, path):
    drive, colon, rest = path.strip().partition(":")
    if not colon or len(drive) != 1:
        return None
    folder = (Path(prefix) / "dosdevices" / f"{drive.lower()}:").resolve()
    for part in rest.replace("/", "\\").split("\\"):
        if part:
            folder = child(folder, part)
    return folder


def stored_community(prefix):
    file = Path(prefix) / "user.reg"
    if not file.is_file():
        return None
    keys = registry.load(file)
    return next((found for key in REGISTRY_KEYS if (found := registry.value(keys, key, COMMUNITY_VALUE)) is not None), None)


def program_line(prefix):
    found = next(iter(sorted(Path(prefix).glob(PROGRAM_GLOB))), None)
    if found:
        return True, f"BBS Nest is installed ({found.relative_to(prefix)})"
    return False, "bbs_nest.exe is not in the prefix; run: flightsim-on-linux addon install bbs-nest <BlueBirdNestSetup.exe>"


def community_line(prefix, community):
    stored = stored_community(prefix)
    if not isinstance(stored, str) or not stored:
        return False, f"user.reg has no {COMMUNITY_VALUE} for Nest; open Nest once, set the folder in its Settings to {community}, and open it again"
    folder = windows_to_linux(prefix, stored)
    if folder is None or not folder.is_dir():
        return False, f"Nest's Community folder {stored} is not a folder on this machine; set it in Nest's Settings to {community} and open Nest again"
    if folder.resolve() != Path(community).resolve():
        return False, f"Nest's Community folder is {stored}, which is not the simulator's {community}; set it in Nest's Settings and open Nest again, or it shows the 757 as not installed"
    return True, f"Nest's Community folder ({stored}) is the simulator's Community folder"


def pace_line(proton):
    return proton_build.patch_line(Path(proton), PACE_PATCH, PACE_MODULE, "Proton's winewayland.so paces the buffer swaps of the hidden OpenGL window Nest draws in")


def guarded(line, **places):
    for name, value in places.items():
        if not value:
            return False, f"no {LABELS[name]} found; run: flightsim-on-linux machine"
    return line(*places.values())


def command_check(prefix, community, proton):
    lines = [
        guarded(program_line, prefix=prefix),
        guarded(community_line, prefix=prefix, community=community),
        guarded(pace_line, proton=proton),
    ]
    for good, text in lines:
        print(f"ok {text}" if good else f"FAIL {text}")
    return 0 if all(good for good, _ in lines) else 1


def main(argv):
    if len(argv) != 4 or argv[0] != "check":
        print("usage: nest.py check <prefix> <Community folder> <Proton folder>", file=sys.stderr)
        return 2
    return command_check(*argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
