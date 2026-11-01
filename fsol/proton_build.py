import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINE_PATCHES = ROOT / "patches" / "wine"
VKD3D_PATCHES = ROOT / "patches" / "vkd3d-proton"
PATCH_FOLDERS = {"patch": WINE_PATCHES, "vkd3d-patch": VKD3D_PATCHES}
BUILD_RECORD = "fs2024-build.txt"
PROTON_BUILD = "flightsim-on-linux proton build"
USAGE = "usage: proton_build.py check <Proton folder> <patch> <module> <text when it passes> [<patch> <module> <text when it passes> ...]"


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def build_record(proton):
    file = proton / BUILD_RECORD
    if not file.is_file():
        return set()
    return {tuple(line.split(" ", 2)) for line in file.read_text(errors="replace").splitlines() if line.count(" ") >= 2}


def recorded_module(record, module):
    return next((sha for kind, sha, name in record if kind == "module" and name == module), None)



def changed_modules(proton):
    return sorted(name for kind, sha, name in build_record(proton) if kind == "module" and sha256_of(proton / "files" / name) != sha)

def listed_line(proton, patch, passed, kind="patch"):
    if (kind, sha256_of(PATCH_FOLDERS[kind] / patch), patch) not in build_record(proton):
        return False, f"{proton / BUILD_RECORD} does not list this repository's {patch}; run: {PROTON_BUILD}"
    return True, passed


def module_line(proton, module, passed):
    wanted = recorded_module(build_record(proton), module)
    if not wanted or sha256_of(proton / "files" / module) != wanted:
        return False, f"{module} in {proton / 'files'} is not the one its build record lists; run: {PROTON_BUILD}"
    return True, passed


def patch_line(proton, patch, module, passed, kind="patch"):
    listed = listed_line(proton, patch, passed, kind)
    return module_line(proton, module, passed) if listed[0] else listed


def main(argv):
    if len(argv) < 5 or (len(argv) - 2) % 3 or argv[0] != "check":
        print(USAGE, file=sys.stderr)
        return 2
    proton, fixes = argv[1], argv[2:]
    if not proton:
        print("FAIL no Proton found; run: flightsim-on-linux machine")
        return 1
    status = 0
    for patch, module, passed in zip(fixes[0::3], fixes[1::3], fixes[2::3]):
        good, text = patch_line(Path(proton), patch, module, passed)
        print(f"ok {text}" if good else f"FAIL {text}")
        status = status or (0 if good else 1)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
