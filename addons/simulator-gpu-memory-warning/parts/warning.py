import json
import re
import shutil
import sys
from pathlib import Path

PACKAGE = "flightsim-on-linux-gpu-memory-warning"
PAGE = Path("html_ui/Global/notifications.html")
BUNDLE_TAG = '<script type="module" src="/Global/notifications.bundle.js"></script>'
FILTER_MARK = "flightsimOnLinuxHidesGpuMemoryWarning"
LEGACY_TAG = '<script type="module" src="/Global/flightsim-on-linux-gpu-memory-warning.js"></script>'
KEY = "SYSTEM.NOTIF_LOW_GPU_MEMORY_CAPACITY_DESC"

FILTER = """const KEY = "NOTIF_LOW_GPU_MEMORY_CAPACITY";
const TEXTS = %s;

function isGpuMemoryWarning(notification) {
  const data = JSON.stringify(notification || {});
  return data.includes(KEY) || TEXTS.some((text) => data.includes(text));
}

function hideGpuMemoryWarning(manager) {
  const prototype = Object.getPrototypeOf(manager);
  if (prototype.flightsimOnLinuxHidesGpuMemoryWarning) return;
  for (const name of ["NewNotification", "UpdateNotificationData", "UpdateNotificationText"]) {
    const original = prototype[name];
    prototype[name] = function (notification, ...rest) {
      if (isGpuMemoryWarning(notification)) return Promise.resolve();
      return original.call(this, notification, ...rest);
    };
  }
  prototype.flightsimOnLinuxHidesGpuMemoryWarning = true;
}

const waiting = setInterval(() => {
  if (!window.NotificationsManager) return;
  clearInterval(waiting);
  hideGpuMemoryWarning(window.NotificationsManager);
}, 100);
"""


class WarningError(Exception):
    pass


class Places:
    def __init__(self, sim_install, community, backup):
        self.base_ui = sim_install / "Packages" / "fs-base-ui"
        self.fs_base = sim_install / "Packages" / "fs-base"
        self.page = self.base_ui / PAGE
        self.layout = self.base_ui / "layout.json"
        self.legacy_package = community / PACKAGE
        self.backup = backup
        self.backup_page = backup / "notifications.html"


def read_exact(path):
    with open(path, encoding="utf-8", newline="") as file:
        return file.read()


def write_exact(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as file:
        file.write(text)


def byte_size(text):
    return len(text.encode("utf-8"))


def filtered_page(text, script):
    if text.count(BUNDLE_TAG) != 1:
        raise WarningError(f"the simulator's notifications.html no longer loads the bundle with {BUNDLE_TAG}")
    newline = "\r\n" if "\r\n" in text else "\n"
    body = newline.join(script.splitlines())
    return text.replace(BUNDLE_TAG, f'{BUNDLE_TAG}{newline}\t\t<script type="module">{newline}{body}{newline}\t\t</script>')


def listed_page_path(places):
    if not places.layout.is_file():
        raise WarningError(f"the simulator has no {places.layout}")
    listed = json.loads(read_exact(places.layout).lstrip("﻿")).get("content", [])
    wanted = PAGE.as_posix().lower()
    page = next((entry["path"] for entry in listed if entry.get("path", "").lower() == wanted), None)
    if page is None:
        raise WarningError(f"{places.layout} does not list {PAGE.as_posix()}")
    return page


def size_pattern(listed_path):
    return re.compile(r'("path"\s*:\s*"' + re.escape(listed_path) + r'"\s*,\s*"size"\s*:\s*)(\d+)')


def listed_page_size(places):
    found = size_pattern(listed_page_path(places)).findall(read_exact(places.layout))
    if len(found) != 1:
        raise WarningError(f"{places.layout} does not give the size of {PAGE.as_posix()} right after its path")
    return int(found[0][1])


def set_listed_page_size(places, size):
    pattern = size_pattern(listed_page_path(places))
    text = read_exact(places.layout)
    if len(pattern.findall(text)) != 1:
        raise WarningError(f"{places.layout} does not give the size of {PAGE.as_posix()} right after its path")
    write_exact(places.layout, pattern.sub(lambda match: match.group(1) + str(size), text))


def warning_texts(places):
    texts = set()
    for pack in sorted(places.fs_base.glob("*.locPak")):
        strings = json.loads(pack.read_text(encoding="utf-8-sig")).get("LocalisationPackage", {}).get("Strings", {})
        if strings.get(KEY):
            texts.add(strings[KEY])
    if not texts:
        raise WarningError(f"no language file of the simulator has {KEY}")
    return sorted(texts)


def filter_script(places):
    return FILTER % json.dumps(warning_texts(places), ensure_ascii=False, indent=2)


def holds_filter(page):
    return FILTER_MARK in page or LEGACY_TAG in page


def original_page(places):
    if not places.page.is_file():
        raise WarningError(f"the simulator has no {places.page}")
    current = read_exact(places.page)
    if not holds_filter(current):
        return current, True
    if not places.backup_page.is_file():
        raise WarningError(f"{places.page} already loads the filter, and its original is not in {places.backup_page}")
    return read_exact(places.backup_page), False


def remove_legacy_package(places):
    if places.legacy_package.exists():
        shutil.rmtree(places.legacy_package)
        print(f"removed {places.legacy_package}, which the simulator mounts too late for this page")


def apply(places):
    original, pristine = original_page(places)
    page = filtered_page(original, filter_script(places))
    if pristine:
        write_exact(places.backup_page, original)
        shutil.copy2(places.layout, places.backup / "layout.json")
    write_exact(places.page, page)
    set_listed_page_size(places, byte_size(page))
    remove_legacy_package(places)
    print(f"put the filter inside {places.page}; the original is in {places.backup}")


def check(places):
    if not places.backup_page.is_file():
        print(f"FAIL the original notifications.html is not in {places.backup}; run apply")
        return 1
    failures = 0
    page = read_exact(places.page) if places.page.is_file() else ""
    texts = warning_texts(places)
    if page == filtered_page(read_exact(places.backup_page), filter_script(places)):
        print(f"ok {places.page} is the simulator's page plus the filter, which drops the warning by its key and by its text in {len(texts)} languages")
    elif holds_filter(page):
        print(f"FAIL {places.page} holds an older filter or an older page; run apply again")
        failures += 1
    else:
        print(f"FAIL {places.page} does not hold the filter; a simulator update probably replaced it, run apply again")
        failures += 1
    if page and listed_page_size(places) == byte_size(page):
        print(f"ok {places.layout} gives the page's new size, {byte_size(page)} bytes")
    else:
        print(f"FAIL {places.layout} does not give the page's size on disk; run apply again")
        failures += 1
    if places.legacy_package.exists():
        print(f"FAIL {places.legacy_package} is still in the Community folder; run apply again")
        failures += 1
    return 1 if failures else 0


def undo(places):
    if places.backup_page.is_file() and places.page.is_file() and holds_filter(read_exact(places.page)):
        original = read_exact(places.backup_page)
        write_exact(places.page, original)
        set_listed_page_size(places, byte_size(original))
        print(f"put the original {places.page} back")
    else:
        print(f"{places.page} does not hold the filter; left it as it is")
    remove_legacy_package(places)
    if places.backup.exists():
        shutil.rmtree(places.backup)


def main(argv):
    if len(argv) != 4 or argv[0] not in ("apply", "check", "undo"):
        print("usage: warning.py apply|check|undo <simulator install folder> <Community folder> <backup folder>", file=sys.stderr)
        return 2
    if not all(argv[1:]):
        print("the simulator install folder, the Community folder or the prefix was not found", file=sys.stderr)
        return 2
    places = Places(Path(argv[1]), Path(argv[2]), Path(argv[3]))
    try:
        if argv[0] == "apply":
            apply(places)
            return 0
        if argv[0] == "check":
            return check(places)
        undo(places)
        return 0
    except WarningError as error:
        print(f"FAIL {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
