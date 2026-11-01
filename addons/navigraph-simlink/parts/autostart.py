import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fsol import addons, desktop  # noqa: E402

FOLDER = Path(__file__).resolve().parents[1]
NAME = f"{desktop.PREFIX}-{FOLDER.name}.desktop"


def entry(addon, cli):
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={addon.name}",
        f"Comment=Opens {addon.name} when you log in, as it does on Windows",
        f"Exec={desktop.launch_command(addon, cli, 'launch')}",
        "Terminal=false",
        f"{desktop.MARKER}={addon.id}",
    ]) + "\n"


def write(folder, cli):
    target = Path(folder) / NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(entry(addons.load(FOLDER), cli))
    print(f"changed {target} opens Simlink when you log in; addon launch navigraph-simlink autostart -- --remove takes it out")
    return 0


def remove(folder):
    target = Path(folder) / NAME
    marker = f"{desktop.MARKER}={FOLDER.name}"
    if not target.is_file() or marker not in target.read_text(errors="replace").splitlines():
        print(f"already {target} was not written by this add-on")
        return 0
    target.unlink()
    print(f"changed {target} is removed")
    return 0


def main(argv):
    if len(argv) == 3 and argv[0] == "write":
        return write(argv[1], argv[2])
    if len(argv) == 2 and argv[0] == "remove":
        return remove(argv[1])
    print("usage: autostart.py write <autostart folder> <CLI> | remove <autostart folder>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
