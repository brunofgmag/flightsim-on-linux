import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fsol import registry  # noqa: E402


def main(argv):
    if len(argv) != 3:
        print("usage: registry-value.py <prefix> <key under HKCU> <value name>", file=sys.stderr)
        return 2
    prefix, key, name = argv
    file = Path(prefix) / "user.reg"
    found = registry.value(registry.load(file), key, name) if file.is_file() else None
    if found is None:
        return 1
    print(found)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
