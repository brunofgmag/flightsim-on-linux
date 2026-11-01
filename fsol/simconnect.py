import json
import re
import sys
from pathlib import Path

FILE = "SimConnect.xml"
CFG_FILE = "SimConnect.cfg"
CFG_ADDRESS = "127.0.0.1"
FIX = "run: flightsim-on-linux addon apply simconnect-tcp"
USAGE = "usage: simconnect.py write|check|remove <add-on id> <folder with SimConnect.xml> <folder for SimConnect.cfg>, or watched <add-on id> <folder with SimConnect.xml> <addon.json>"
PRIVILEGED_BELOW = 1024
BLOCK = re.compile(rb"<SimConnect\.Comm>.*?</SimConnect\.Comm>", re.DOTALL)
IPV4 = re.compile(rb"<Protocol>\s*IPv4\s*</Protocol>", re.IGNORECASE)
PORT = re.compile(rb"(<Port>\s*)(\d+)(\s*</Port>)")
CFG_PORT = re.compile(r"^Port=(\d+)\s*$", re.MULTILINE)


def static_port_span(data):
    for block in BLOCK.finditer(data):
        found = PORT.search(block.group(0))
        if found and IPV4.search(block.group(0)) and int(found.group(2)) != 0:
            return block.start() + found.start(2), block.start() + found.end(2), int(found.group(2))
    return None


def read_port(data):
    span = static_port_span(data)
    return span[2] if span else None


def with_port(data, port):
    span = static_port_span(data)
    if not span:
        raise ValueError("no static IPv4 port in the file")
    return data[: span[0]] + str(port).encode() + data[span[1]:]


def config_file(found):
    return found.base / FILE if found.base else None


def port(found):
    path = config_file(found)
    try:
        return read_port(path.read_bytes()) if path else None
    except OSError:
        return None


def declared_port(base):
    if not base:
        return None, "no simulator base folder found; run: flightsim-on-linux machine"
    path = Path(base) / FILE
    if not path.is_file():
        return None, f"{path} does not exist; start the simulator once so it writes the file, then {FIX}"
    found = read_port(path.read_bytes())
    if found is None:
        return None, f"{path} has no static IPv4 port; {FIX}"
    if found < PRIVILEGED_BELOW:
        return None, f"{path} declares port {found}, which Linux will not open for the simulator; {FIX}"
    return found, None


def cfg_text(number):
    return f"[SimConnect]\r\nProtocol=IPv4\r\nAddress={CFG_ADDRESS}\r\nPort={number}\r\n"


def cfg_port(path):
    found = CFG_PORT.search(path.read_text(errors="replace")) if path.is_file() else None
    return int(found.group(1)) if found else None


def write_cfg(addon, base, folder):
    number, problem = declared_port(base)
    if problem:
        print(f"FAIL {problem}")
        return 1
    target = Path(folder) / CFG_FILE
    if cfg_port(target) == number:
        print(f"already {target} points at port {number}")
        return 0
    target.write_bytes(cfg_text(number).encode())
    print(f"changed {target} points SimConnect clients at {CFG_ADDRESS}:{number}")
    return 0


def check_cfg(addon, base, folder):
    number, problem = declared_port(base)
    if problem:
        print(f"FAIL {problem}")
        return 1
    print(f"ok SimConnect.xml declares the static IPv4 port {number}, above 1023")
    target = Path(folder) / CFG_FILE
    if cfg_port(target) == number:
        print(f"ok {target} points at port {number}")
        return 0
    print(f"FAIL {target} does not point at port {number}; run: flightsim-on-linux addon apply {addon}")
    return 1


def remove_cfg(addon, base, folder):
    target = Path(folder) / CFG_FILE
    if not target.is_file():
        print(f"already {target} does not exist")
        return 0
    target.unlink()
    print(f"changed {target} is removed")
    return 0


def watched(addon, base, addon_json):
    number, problem = declared_port(base)
    if problem:
        return 0
    health = json.loads(Path(addon_json).read_text())["watcher"]["health"]
    if health["connected_to"] == number:
        print(f"ok the watcher looks for the Simlink connection on port {number}, the one SimConnect.xml declares")
        return 0
    print(f"FAIL the watcher in {addon_json} looks for the connection on port {health['connected_to']}, but SimConnect.xml declares {number}; set watcher.health.connected_to to {number} in that file")
    return 1


ACTIONS = {"write": write_cfg, "check": check_cfg, "watched": watched, "remove": remove_cfg}


def main(argv):
    if len(argv) != 4 or argv[0] not in ACTIONS:
        print(USAGE, file=sys.stderr)
        return 2
    return ACTIONS[argv[0]](*argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
