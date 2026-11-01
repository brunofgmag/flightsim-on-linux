import json
import os
import sys
from pathlib import Path

KEYS = ("manuallyDetectedGamesData", "msfs2024Steam", "directory")
USAGE = "usage: aerosoft.py apply|check <persistent-store.json> <the simulator's packages folder>"


def configured_directory(file):
    try:
        node = json.loads(Path(file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for key in KEYS:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node if isinstance(node, str) and node else None


def same_folder(left, right):
    return os.path.realpath(left) == os.path.realpath(right)


def report(file, packages):
    if not packages:
        return False, "FAIL no packages folder found; run: flightsim-on-linux machine"
    if not Path(file).is_file():
        return False, f"FAIL Aerosoft One has no settings file at {file}; start it once and add MSFS 2024 (Steam)"
    directory = configured_directory(file)
    if directory is None:
        return False, "FAIL Aerosoft One has no MSFS 2024 (Steam) folder in its settings; add the game in its Settings with the folder " + packages
    if same_folder(directory, packages):
        return True, f"ok Aerosoft One's MSFS 2024 (Steam) folder is {directory}, the simulator's packages folder"
    return False, f"FAIL Aerosoft One looks for the simulator in {directory}, but the simulator's packages folder is {packages}; add MSFS 2024 (Steam) again with that folder, or the aircraft land where the simulator does not look"


def main(argv):
    if len(argv) != 3 or argv[0] not in ("apply", "check"):
        print(USAGE, file=sys.stderr)
        return 2
    action, file, packages = argv
    good, line = report(file, packages)
    if action == "apply":
        print("this add-on changes nothing: Aerosoft One keeps your account in the same settings file, so only the one key with the game folder is read")
    print(line)
    return 0 if good or action == "apply" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
