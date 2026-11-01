import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fsol import simconnect

PORT = 5111
MODULE = "node-simconnect"
VERSION = "4.2.1"
BACKUP_NAME = "SimConnect.xml"


def module_version(cache):
    try:
        return json.loads((cache / "node_modules" / MODULE / "package.json").read_text()).get("version")
    except (OSError, ValueError):
        return None


def install_module(cache):
    npm = shutil.which("npm")
    if module_version(cache) == VERSION:
        print(f"{MODULE} {VERSION} is already in {cache}")
        return 0
    if not npm:
        print(f"npm is not installed, so {MODULE} was skipped; only watchers that follow the loaded aircraft need it")
        return 0
    cache.mkdir(parents=True, exist_ok=True)
    command = [npm, "install", "--prefix", str(cache), "--no-save", "--no-audit", "--no-fund", "--ignore-scripts", f"{MODULE}@{VERSION}"]
    if subprocess.run(command, stdin=subprocess.DEVNULL).returncode or module_version(cache) != VERSION:
        print(f"FAIL npm could not install {MODULE} {VERSION} into {cache}")
        return 1
    print(f"installed {MODULE} {VERSION} into {cache}")
    return 0


def move_port(path, backup):
    data = path.read_bytes()
    current = simconnect.read_port(data)
    if current is None:
        print(f"FAIL {path} has no static IPv4 port to move")
        return 1
    if current >= simconnect.PRIVILEGED_BELOW:
        print(f"the static IPv4 port is already {current}; SimConnect.xml is left alone")
        return 0
    backup.mkdir(parents=True, exist_ok=True)
    if not (backup / BACKUP_NAME).exists():
        shutil.copy2(path, backup / BACKUP_NAME)
    path.write_bytes(simconnect.with_port(data, PORT))
    print(f"moved the static IPv4 port from {current} to {PORT}; the original is in {backup / BACKUP_NAME}")
    return 0


def apply(base, backup, cache):
    path = base / simconnect.FILE
    if not path.is_file():
        print(f"FAIL {path} does not exist; start the simulator once so it writes the file, then apply again")
        return 1
    return move_port(path, backup) or install_module(cache)


def check(base, backup, cache):
    path = base / simconnect.FILE
    port = simconnect.read_port(path.read_bytes()) if path.is_file() else None
    if not path.is_file():
        print(f"FAIL {path} does not exist")
    elif port is None:
        print("FAIL SimConnect.xml has no static IPv4 port")
    else:
        print(f"ok SimConnect.xml declares the static IPv4 port {port}")
        print(f"ok port {port} is above 1023, so the simulator can open it" if port >= simconnect.PRIVILEGED_BELOW else f"FAIL port {port} is below 1024; Linux does not let the simulator open it")
    version = module_version(cache)
    if version:
        print(f"ok {MODULE} {version} is in {cache}")
    return 0 if port is not None and port >= simconnect.PRIVILEGED_BELOW else 1


def undo(base, backup, cache):
    saved = backup / BACKUP_NAME
    if not saved.is_file():
        print("there is no backup of SimConnect.xml, so nothing was restored")
        return 0
    shutil.copy2(saved, base / simconnect.FILE)
    saved.unlink()
    print(f"restored SimConnect.xml from {saved}; {MODULE} stays in {cache}")
    return 0


ACTIONS = {"apply": apply, "check": check, "undo": undo}


def main(argv):
    if len(argv) != 4 or argv[0] not in ACTIONS:
        print("usage: simconnect_tcp.py apply|check|undo <base folder> <backup folder> <cache folder>", file=sys.stderr)
        return 2
    return ACTIONS[argv[0]](Path(argv[1]), Path(argv[2]), Path(argv[3]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
