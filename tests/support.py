import os
import re
import shutil
import subprocess
from pathlib import Path

from tests.test_machine import write

STAND_IN_DRIVE = "Q:"
REAL_DRIVES = re.compile(r"^[CcSs]:")


def stub(path, text):
    write(path, text)
    path.chmod(0o755)
    return path


PROCESS_STUB = """#!/usr/bin/env python3
import os
import re
import signal
import sys

TOKEN = b"FSOL_TEST_TOKEN=%s"
arguments = sys.argv[1:]
full = exact = ignore = False
while arguments and arguments[0].startswith("-"):
    option = arguments.pop(0)
    if option == "--":
        break
    full = full or "f" in option[1:]
    exact = exact or "x" in option[1:]
    ignore = ignore or "i" in option[1:]
pattern = arguments[0]
found = []
for entry in os.listdir("/proc"):
    if not entry.isdigit() or int(entry) == os.getpid():
        continue
    try:
        if TOKEN not in open(f"/proc/{entry}/environ", "rb").read().split(b"\\0"):
            continue
        if full:
            text = open(f"/proc/{entry}/cmdline", "rb").read().replace(b"\\0", b" ").decode(errors="replace").strip()
        else:
            text = open(f"/proc/{entry}/comm").read().strip()
    except OSError:
        continue
    flags = re.IGNORECASE if ignore else 0
    if (text.lower() == pattern.lower() if ignore else text == pattern) if exact else re.search(pattern, text, flags):
        found.append(int(entry))
if os.path.basename(sys.argv[0]) == "pkill":
    for pid in found:
        os.kill(pid, signal.SIGTERM)
else:
    print("\\n".join(str(pid) for pid in found))
sys.exit(0 if found else 1)
"""


def process_stubs(folder, token):
    for name in ("pgrep", "pkill"):
        stub(Path(folder) / name, PROCESS_STUB % token.encode().decode())


def stand_in_name(windows_path):
    return STAND_IN_DRIVE + windows_path[2:]


def start_stand_in(folder, token, argv0, seconds=30):
    if REAL_DRIVES.match(argv0):
        raise ValueError(f"a stand-in must not look like a program of the real prefix: {argv0}")
    link = Path(folder) / "stand-ins" / re.sub(r"[^A-Za-z0-9._-]", "_", argv0)
    link.parent.mkdir(parents=True, exist_ok=True)
    if not link.exists():
        link.symlink_to(shutil.which("sleep"))
    return subprocess.Popen([argv0, str(seconds)], executable=str(link), env={**os.environ, "FSOL_TEST_TOKEN": token})
