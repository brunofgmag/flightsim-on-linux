from pathlib import Path

SIMULATOR = b"FlightSimulator2024.exe"


def simulator_running(proc=Path("/proc")):
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if SIMULATOR in command.split(b"\0", 1)[0]:
            return True
    return False
