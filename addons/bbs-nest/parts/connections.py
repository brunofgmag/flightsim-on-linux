import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from fsol import watcher  # noqa: E402

PATTERN = r"^[A-Za-z]:\\.*\\bbs_nest\.exe"
LIBRARY = "/SubSystem.dll"
LIST_HEAD = 0x26E80
NEXT = 0xA380
SEQUENCE = 0x258
INBOUND_COUNT = 0x298
MAX_CONNECTIONS = 16
MEASURED_STAMP = 0x6ADF9E53


def library_mappings(pid_dir):
    try:
        lines = (pid_dir / "maps").read_text(errors="replace").splitlines()
    except OSError:
        return []
    fields = (line.split(None, 5) for line in lines)
    return [(int(row[0].split("-")[0], 16), row[5]) for row in fields if len(row) == 6 and row[5].endswith(LIBRARY)]


def nest_process(proc):
    found = watcher.matching(PATTERN, watcher.scan(proc))
    loaded = next((pid_dir for pid_dir in found if library_mappings(pid_dir)), None)
    return loaded or (found[0] if found else None), loaded is not None


def time_stamp(path):
    try:
        with open(path, "rb") as file:
            header = file.read(0x400)
        return struct.unpack_from("<I", header, struct.unpack_from("<I", header, 0x3C)[0] + 8)[0]
    except (OSError, struct.error):
        return None


def read_connections(descriptor, base):
    qword = lambda address: struct.unpack("<Q", os.pread(descriptor, 8, address))[0]
    dword = lambda address: struct.unpack("<I", os.pread(descriptor, 4, address))[0]
    connections, seen = [], set()
    walk = qword(base + LIST_HEAD)
    while walk and walk not in seen and len(connections) < MAX_CONNECTIONS:
        seen.add(walk)
        connections.append((dword(walk + SEQUENCE), dword(walk + INBOUND_COUNT)))
        walk = qword(walk + NEXT)
    return connections


def report(connections):
    for sequence, queued in connections:
        print(f"connection {sequence}: {queued} messages waiting")
    if not connections:
        print("No connection read: Nest has not connected yet, or the offsets don't fit this SubSystem.dll.")
        return 1
    if len(connections) > 1:
        print("More than one connection: Nest is leaking. Close it and open it again with the 757 already loaded.")
        return 2
    print("One connection: the orphan SimConnect connection is not there.")
    return 0


def main(proc=Path("/proc")):
    pid_dir, loaded = nest_process(proc)
    if pid_dir is None:
        print("BBS Nest is not open.")
        return 1
    if not loaded:
        print(f"Nest {pid_dir.name} has not loaded SubSystem.dll yet.")
        return 1
    mappings = library_mappings(pid_dir)
    base, path = min(mappings)
    stamp = time_stamp(path)
    if stamp is not None and stamp != MEASURED_STAMP:
        print(f"This SubSystem.dll has TimeDateStamp {stamp:#x}, not the {MEASURED_STAMP:#x} the offsets were measured on, so the numbers below may be wrong.")
    try:
        descriptor = os.open(pid_dir / "mem", os.O_RDONLY)
        try:
            connections = read_connections(descriptor, base)
        finally:
            os.close(descriptor)
    except (OSError, struct.error) as error:
        print(f"Cannot read the connection list of Nest {pid_dir.name}: {error}")
        return 3
    return report(connections)


if __name__ == "__main__":
    sys.exit(main())
