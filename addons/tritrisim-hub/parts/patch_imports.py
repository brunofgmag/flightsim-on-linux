import os
import shutil
import sys
import tempfile
from pathlib import Path

ORIGINAL = b"ole32.dll\x00"
PATCHED = b"oletr.dll\x00"
ORIGINAL_STATE = "original"
PATCHED_STATE = "patched"
UNKNOWN_STATE = "unknown"
USAGE = "usage: patch_imports.py status|patch|restore <tritrisim-hub.exe>"


def state(data):
    original, patched = data.count(ORIGINAL), data.count(PATCHED)
    if original == 1 and patched == 0:
        return ORIGINAL_STATE
    if original == 0 and patched == 1:
        return PATCHED_STATE
    return UNKNOWN_STATE


def replace_contents(path, data):
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temp = Path(name)
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
        shutil.copymode(path, temp)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def swap(path, before, after, expected):
    data = path.read_bytes()
    if state(data) != expected:
        print(f"{path} is not in the expected state: it has {data.count(ORIGINAL)} ole32.dll and {data.count(PATCHED)} oletr.dll import names", file=sys.stderr)
        return 1
    replace_contents(path, data.replace(before, after))
    return 0


def main(argv):
    if len(argv) != 2 or argv[0] not in ("status", "patch", "restore"):
        print(USAGE, file=sys.stderr)
        return 2
    action, path = argv[0], Path(argv[1])
    if action == "status":
        print(state(path.read_bytes()))
        return 0
    if action == "patch":
        return swap(path, ORIGINAL, PATCHED, ORIGINAL_STATE)
    return swap(path, PATCHED, ORIGINAL, PATCHED_STATE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
