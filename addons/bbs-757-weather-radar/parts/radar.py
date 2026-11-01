import json
import re
import shutil
import sys
from pathlib import Path

PACKAGE = "bluebirdsim-aircraft-757-200"
PANEL = Path("SimObjects/Airplanes/bbs-757-200-base/panel")
EDITED = (PANEL / "panel.cfg", PANEL / "BBS752.wasm", Path("layout.json"))
ADDED = PANEL / "BBS757WX.wasm"
GAUGES = ("wx_l", "wx_r")
MISSING_PACKAGE = "the 757 package {package} is not in the Community folder; install the aircraft with BBS Nest first"


def package_of(community):
    return Path(community) / PACKAGE


def panel_text(package):
    file = package / EDITED[0]
    return file.read_text(errors="replace") if file.is_file() else None


def gauge_pattern(gauge):
    return re.compile(rf"wasm_module={re.escape(ADDED.name)}&wasm_gauge={gauge}\b", re.IGNORECASE)


def holds_radar(text):
    return text is not None and any(gauge_pattern(gauge).search(text) for gauge in GAUGES)


def copy_through_part(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    shutil.copy2(source, part)
    part.replace(target)


def command_backup(package, folder):
    text = panel_text(package)
    if text is None:
        print(MISSING_PACKAGE.format(package=PACKAGE), file=sys.stderr)
        return 2
    if holds_radar(text):
        if (folder / EDITED[0].name).is_file():
            print(f"kept the backup in {folder}: panel.cfg already holds the radar, so the originals are not in the aircraft any more")
            return 0
        print("panel.cfg already holds the radar and there is no backup of the files before it; reinstall the aircraft with BBS Nest, then install the radar again", file=sys.stderr)
        return 2
    for relative in EDITED:
        if (package / relative).is_file():
            copy_through_part(package / relative, folder / relative.name)
    print(f"backed up {', '.join(relative.name for relative in EDITED)} to {folder}")
    return 0


def command_restore(package, folder):
    saved = [relative for relative in EDITED if (folder / relative.name).is_file()]
    if not saved:
        if holds_radar(panel_text(package)):
            print(f"the aircraft holds the radar and {folder} has no backup; reinstall the aircraft with BBS Nest", file=sys.stderr)
            return 1
        print("already no backup to restore")
        return 0
    for relative in saved:
        copy_through_part(folder / relative.name, package / relative)
    (package / ADDED).unlink(missing_ok=True)
    shutil.rmtree(folder)
    print(f"put back {', '.join(relative.name for relative in saved)} and removed {ADDED.name}")
    return 0


def layout_size(package):
    file = package / "layout.json"
    if not file.is_file():
        return None
    try:
        content = json.loads(file.read_text(encoding="utf-8-sig")).get("content", [])
    except ValueError:
        return None
    wanted = ADDED.as_posix().lower()
    return next((entry.get("size") for entry in content if entry.get("path", "").lower() == wanted), None)


def check_lines(package):
    text = panel_text(package)
    if text is None:
        return [(False, MISSING_PACKAGE.format(package=PACKAGE))]
    missing = [gauge for gauge in GAUGES if not gauge_pattern(gauge).search(text)]
    panel = (
        (True, f"panel.cfg gives the left and right radar gauges ({', '.join(GAUGES)}) to {ADDED.name}")
        if not missing
        else (False, f"panel.cfg has no {ADDED.name} gauge for {', '.join(missing)}; install the radar again with: flightsim-on-linux addon install bbs-757-weather-radar <the mod's zip>, or, for a folder you unpacked yourself, flightsim-on-linux addon launch bbs-757-weather-radar install -- <folder>")
    )
    wasm = package / ADDED
    size = wasm.stat().st_size if wasm.is_file() else 0
    module = (True, f"{ADDED.name} is in the panel folder ({size} bytes)") if size else (False, f"{wasm} is missing or empty; install the radar again")
    listed = layout_size(package)
    index = (
        (True, f"layout.json lists {ADDED.name} with the size on disk")
        if size and listed == size
        else (False, f"layout.json does not list {ADDED.name} with the size on disk ({listed} listed, {size} on disk); install the radar again")
    )
    return [panel, module, index]


def command_check(package, _):
    lines = check_lines(package)
    for good, text in lines:
        print(f"ok {text}" if good else f"FAIL {text}")
    return 0 if all(good for good, _ in lines) else 1


def command_state(package, _):
    text = panel_text(package)
    print("missing" if text is None else "modified" if holds_radar(text) else "original")
    return 0


COMMANDS = {"backup": command_backup, "restore": command_restore, "check": command_check, "state": command_state}


def main(argv):
    if len(argv) != 3 or argv[0] not in COMMANDS:
        print("usage: radar.py backup|restore|check|state <Community folder> <backup folder>", file=sys.stderr)
        return 2
    if not argv[1]:
        if argv[0] == "check":
            print("FAIL no Community folder found; run: flightsim-on-linux machine")
            return 1
        print("no Community folder found; run: flightsim-on-linux machine", file=sys.stderr)
        return 2
    return COMMANDS[argv[0]](package_of(argv[1]), Path(argv[2]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
