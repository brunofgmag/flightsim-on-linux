import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fsol.exe_xml import linux_path  # noqa: E402

PACKAGE = "tfdidesign-aircraft-md11"
NAVDATA = "NavData_FS24"
LOCAL = "drive_c/users/*/AppData/Local"
PRIMARY = "data/primary/"
ZIP_VERSION = re.compile(r"NavData_FS24\.([0-9.]+)\.zip$")
NAVDATA_ENTRY = re.compile(r'(<Package Name="NavData_FS24" Version=")[^"]*(")')
FS24_BLOCK = re.compile(r'<Installation\b[^>]*\bPlatform="FS24"[^>]*>')
WINDOWS_EPOCH = 11644473600
USAGE = "usage: navdata.py fix|restore <prefix> <community> <backup folder> | check <prefix> <community>"


class NavdataError(Exception):
    pass


def say(text):
    print(f"  {text}")


def first(prefix, pattern):
    return next(iter(sorted(Path(prefix).glob(pattern))), None)


def state_folder(prefix):
    folder = first(prefix, f"{LOCAL}/TFDi Design MD-11")
    if not folder or not (folder / "installation.xml").is_file():
        raise NavdataError("installation.xml of the MD-11 Install Manager is not in the prefix; install the MD-11 with its Install Manager first")
    return folder


def installations(installation):
    try:
        return ElementTree.parse(installation).getroot().iter("Installation")
    except ElementTree.ParseError as error:
        raise NavdataError(f"{installation} cannot be read: {error}")


def install_dir(prefix, installation):
    for node in installations(installation):
        if node.get("Platform") == "FS24" and node.get("InstallDir"):
            return linux_path(prefix, node.get("InstallDir"))
    return None


def package_folder(prefix, community):
    candidates = [install_dir(prefix, state_folder(prefix) / "installation.xml"), Path(community) if community else None]
    folders = [folder / PACKAGE for folder in candidates if folder]
    found = next((folder for folder in folders if folder.is_dir()), None)
    if not found:
        raise NavdataError("the MD-11 package is not in " + (" or ".join(str(folder) for folder in folders) or "any folder this tool knows"))
    return found


def newest_zip(prefix):
    zips = [path for path in Path(prefix).glob(f"{LOCAL}/Temp/TFDi_Design_MD11.{NAVDATA}.*.zip") if ZIP_VERSION.search(path.name)]
    if not zips:
        raise NavdataError("no NavData zip in the prefix's Temp folder; open the Install Manager and let it download the NavData, then close it")
    return max(zips, key=lambda path: path.stat().st_mtime)


def disk_files(package):
    primary = package / "Data" / "Primary"
    for root, dirs, files in os.walk(primary):
        dirs.sort()
        files.sort()
        yield Path(root), dirs, files


def layout_path(package, path):
    return path.relative_to(package).as_posix().lower()


def cache_line(package, path):
    return f"{PACKAGE}\\{path.relative_to(package)}".replace("/", "\\")


def filetime(mtime):
    return int((int(mtime) + WINDOWS_EPOCH) * 10_000_000)


def inventory(package):
    entries, cache = [], [f"{PACKAGE}\\Data\\Primary\\"]
    for root, dirs, files in disk_files(package):
        cache += [cache_line(package, root / name) + "\\" for name in dirs]
        for name in files:
            path = root / name
            stat = path.stat()
            entries.append({"path": layout_path(package, path), "size": stat.st_size, "date": filetime(stat.st_mtime)})
            cache.append(cache_line(package, path))
    return entries, cache


def read_layout(package):
    return json.loads((package / "layout.json").read_text(encoding="utf-8"))


def merged_layout(layout, entries):
    kept = [entry for entry in layout["content"] if not entry["path"].startswith(PRIMARY)]
    return {**layout, "content": sorted(kept + entries, key=lambda entry: entry["path"])}, len(kept)


def with_navdata_version(text, version):
    if NAVDATA_ENTRY.search(text):
        return NAVDATA_ENTRY.sub(lambda match: match.group(1) + version + match.group(2), text)
    block = FS24_BLOCK.search(text)
    if not block:
        raise NavdataError("installation.xml has no FS24 installation to list the NavData in")
    newline = "\r\n" if "\r\n" in text else "\n"
    sample = re.search(r"([ \t]*)<Package ", text)
    indent = sample.group(1) if sample else "    "
    line = text.rfind("\n", 0, text.index("</Installation>", block.end())) + 1
    return text[:line] + f'{indent}<Package Name="{NAVDATA}" Version="{version}" />{newline}' + text[line:]


def navdata_version(installation):
    for node in installations(installation):
        if node.get("Platform") == "FS24":
            return next((package.get("Version") for package in node.iter("Package") if package.get("Name") == NAVDATA), None)
    return None


def targets(package, state):
    return {"layout.json": package / "layout.json", "installation.xml": state / "installation.xml", f"{NAVDATA}.cache": state / "Cache" / f"{NAVDATA}.cache"}


def back_up(package, state, root):
    folder = Path(root) / time.strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True)
    for name, source in targets(package, state).items():
        if source.is_file():
            shutil.copy2(source, folder / name)
        else:
            (folder / f"{name}.absent").touch()
    return folder


def run_unzip(arguments, failure):
    if not shutil.which("unzip"):
        raise NavdataError("unzip is not installed")
    if subprocess.run(["unzip", *arguments], capture_output=True).returncode != 0:
        raise NavdataError(failure)


def write_installation(state, version):
    path = state / "installation.xml"
    raw = path.read_bytes()
    path.write_bytes(with_navdata_version(raw.decode("utf-8"), version).encode("utf-8"))


def command_fix(prefix, community, backup_root):
    state = state_folder(prefix)
    package = package_folder(prefix, community)
    archive = newest_zip(prefix)
    version = ZIP_VERSION.search(archive.name).group(1)
    say(f"found {archive.name} (version {version})")
    run_unzip(["-tq", str(archive)], f"{archive.name} is damaged; open the Install Manager and let it download the NavData again")
    folder = back_up(package, state, backup_root)
    say(f"the three files this changes are copied to {folder}")
    run_unzip(["-o", "-q", str(archive), "-d", str(package.parent)], f"unzip could not unpack {archive.name} into {package.parent}")
    if not (package / "Data" / "Primary").is_dir():
        raise NavdataError(f"{archive.name} did not hold {PACKAGE}/Data/Primary")
    entries, cache = inventory(package)
    layout, kept = merged_layout(read_layout(package), entries)
    with open(package / "layout.json", "w", encoding="utf-8", newline="\n") as file:
        json.dump(layout, file, indent=2, ensure_ascii=False)
    say(f"layout.json: {kept} entries kept and {len(entries)} NavData files listed, {len(layout['content'])} in all")
    write_installation(state, version)
    say(f"installation.xml: {NAVDATA} is {version}")
    (state / "Cache").mkdir(exist_ok=True)
    (state / "Cache" / f"{NAVDATA}.cache").write_bytes(("\r\n".join(cache) + "\r\n").encode("utf-8"))
    say(f"{NAVDATA}.cache: {len(cache)} lines")
    print("changed the NavData is unpacked and listed; start the simulator and load the MD-11 (the first load compiles for about 4 minutes)")
    return 0


def newest_backup(root):
    folders = sorted(path for path in Path(root).glob("*") if path.is_dir())
    return folders[-1] if folders else None


def command_restore(prefix, community, backup_root):
    folder = newest_backup(backup_root)
    if not folder:
        print("already nothing to undo: fix-navdata has not run, so there is no backup to restore")
        return 0
    state = state_folder(prefix)
    package = package_folder(prefix, community)
    for name, target in targets(package, state).items():
        if (folder / name).is_file():
            shutil.copy2(folder / name, target)
            print(f"changed {name} is back as it was before the last fix-navdata (copy in {folder})")
        elif (folder / f"{name}.absent").exists():
            target.unlink(missing_ok=True)
            print(f"removed {name}, which did not exist before the last fix-navdata")
    print("left the unpacked NavData files in the package; undo only puts the three files back")
    return 0


def installation_line(state):
    version = navdata_version(state / "installation.xml")
    if version:
        return True, f"installation.xml lists {NAVDATA} {version}"
    return False, f"installation.xml does not list {NAVDATA}; the Install Manager has not installed the NavData"


def layout_line(package):
    if not (package / "layout.json").is_file():
        return False, f"{package} has no layout.json"
    listed = {entry["path"] for entry in read_layout(package)["content"] if entry["path"].startswith(PRIMARY)}
    on_disk = {layout_path(package, root / name) for root, _, files in disk_files(package) for name in files}
    if not on_disk:
        return False, "the package has no Data/Primary files on disk; the NavData is not unpacked"
    if on_disk <= listed:
        spare = f", and {len(listed - on_disk)} more that are not" if listed - on_disk else ""
        return True, f"layout.json lists the {len(on_disk)} NavData files that are on disk{spare}"
    return False, (f"layout.json lists {len(listed)} NavData files and the disk holds {len(on_disk)}; if the Install Manager stopped at Installing {NAVDATA}, "
                   "close it and run: flightsim-on-linux addon launch tfdi-md-11 fix-navdata")


def command_check(prefix, community):
    lines = []
    try:
        state = state_folder(prefix)
        lines.append(installation_line(state))
        package = package_folder(prefix, community)
        lines.append((True, f"the MD-11 package is in {package.parent}"))
        lines.append(layout_line(package))
    except (NavdataError, OSError, ValueError, KeyError) as error:
        lines.append((False, str(error)))
    for good, text in lines:
        print(f"ok {text}" if good else f"FAIL {text}")
    return 0 if all(good for good, _ in lines) else 1


COMMANDS = {"fix": (command_fix, 3), "restore": (command_restore, 3), "check": (command_check, 2)}

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in COMMANDS or len(args) - 1 != COMMANDS[args[0]][1]:
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    try:
        sys.exit(COMMANDS[args[0]][0](*args[1:]))
    except (NavdataError, OSError, ValueError, KeyError) as error:
        print(f"tfdi-md-11: {error}", file=sys.stderr)
        sys.exit(2)
