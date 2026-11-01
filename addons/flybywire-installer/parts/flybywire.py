import json
import os
import sys
from pathlib import Path

KEYS = ("mainSettings", "simulator", "msfs2024")
FIELDS = ("communityPath", "installPath")
USAGE = "usage: flybywire.py apply|check <config.json> <the simulator's Community folder>"


def simulator_block(file):
    try:
        node = json.loads(Path(file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for key in KEYS:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node if isinstance(node, dict) else None


def configured_paths(file):
    block = simulator_block(file)
    if block is None:
        return None
    return {field: block[field] for field in FIELDS if isinstance(block.get(field), str) and block[field]}


def same_folder(left, right):
    return os.path.realpath(left) == os.path.realpath(right)


def field_line(field, value, community):
    if value is None:
        return False, f"FAIL the installer has no {field} for MSFS 2024; open its settings and set the Community folder to {community}"
    if same_folder(value, community):
        return True, f"ok {field} is {value}, the simulator's Community folder"
    return False, f"FAIL {field} is {value}, not the simulator's Community folder {community}; the installer puts the packages there, where the simulator does not look. Set the Community folder in the installer's settings to {community}"


def report(file, community):
    if not community:
        return [(False, "FAIL no Community folder found; run: flightsim-on-linux machine")]
    if not Path(file).is_file():
        return [(False, f"FAIL the FlyByWire Installer has no settings file at {file}; start it once")]
    paths = configured_paths(file)
    if paths is None:
        return [(False, "FAIL the FlyByWire Installer has no MSFS 2024 block in its settings; start it, choose the simulator and set its Community folder to " + community)]
    return [field_line(field, paths.get(field), community) for field in FIELDS]


def main(argv):
    if len(argv) != 3 or argv[0] not in ("apply", "check"):
        print(USAGE, file=sys.stderr)
        return 2
    action, file, community = argv
    lines = report(file, community)
    if action == "apply":
        print("this add-on changes nothing: the installer keeps your account in the same settings file, so only communityPath and installPath of the MSFS 2024 block are read")
    for _, line in lines:
        print(line)
    return 0 if action == "apply" or all(good for good, _ in lines) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
