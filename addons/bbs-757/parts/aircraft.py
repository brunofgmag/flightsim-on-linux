import json
import sys
from pathlib import Path

AIRCRAFT = "bluebirdsim-aircraft-757-200"
LIVERIES = "bluebirdsim-aircraft-757-200-liveries"
NEST_GLOB = "drive_c/users/*/AppData/Local/Programs/BlueBird Simulations/NEST/bbs_nest.exe"
EXAMPLES = 3
LABELS = {"prefix": "prefix", "community": "Community folder"}


def read_json(file):
    return json.loads(file.read_text(encoding="utf-8-sig"))


def layout_problems(package):
    try:
        content = read_json(package / "layout.json").get("content", [])
    except (OSError, ValueError):
        return None
    wrong = []
    for entry in content:
        file = package / entry["path"]
        if not file.is_file() or file.stat().st_size != entry["size"]:
            wrong.append(entry["path"])
    return len(content), wrong


def package_line(community, name):
    package = Path(community) / name
    manifest = package / "manifest.json"
    if not manifest.is_file():
        return False, f"{name} is not in {community}; install it with BBS Nest"
    try:
        version = read_json(manifest).get("package_version", "unknown")
    except (OSError, ValueError):
        return False, f"{manifest} cannot be read; reinstall {name} with BBS Nest"
    listed = layout_problems(package)
    if listed is None:
        return False, f"{package / 'layout.json'} is missing or cannot be read; reinstall {name} with BBS Nest"
    total, wrong = listed
    if wrong:
        return False, f"{len(wrong)} of the {total} files in the layout.json of {name} are missing or differ in size, for example {', '.join(wrong[:EXAMPLES])}; reinstall it with BBS Nest"
    return True, f"{name} {version} is in Community, and its {total} layout.json files are on disk with the listed sizes"


def nest_line(prefix):
    if next(iter(Path(prefix).glob(NEST_GLOB)), None):
        return True, "BBS Nest is installed; the 757's systems stay off unless it is open beside the simulator"
    return False, "bbs_nest.exe is not in the prefix; run: flightsim-on-linux addon install bbs-nest <BlueBirdNestSetup.exe>"


def guarded(line, **places):
    for name, value in places.items():
        if not value:
            return False, f"no {LABELS[name]} found; run: flightsim-on-linux machine"
    return line(*places.values())


def command_check(community, prefix):
    lines = [
        guarded(lambda found: package_line(found, AIRCRAFT), community=community),
        guarded(lambda found: package_line(found, LIVERIES), community=community),
        guarded(nest_line, prefix=prefix),
    ]
    for good, text in lines:
        print(f"ok {text}" if good else f"FAIL {text}")
    return 0 if all(good for good, _ in lines) else 1


def main(argv):
    if len(argv) != 3 or argv[0] != "check":
        print("usage: aircraft.py check <Community folder> <prefix>", file=sys.stderr)
        return 2
    return command_check(argv[1], argv[2])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
