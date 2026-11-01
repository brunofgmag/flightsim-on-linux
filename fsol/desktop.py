import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from fsol import addons

PREFIX = "flightsim-on-linux"
MARKER = "X-FlightsimOnLinux-Addon"
ICON_SIZE = "256x256"
ICON_RESOURCES = (".rsrc/*/ICON/*", ".rsrc/ICON/*")
DATABASES = (("update-desktop-database", True), ("kbuildsycoca6", False))


class Tools:
    def which(self, name):
        return shutil.which(name)

    def run(self, argv):
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode


def stem(addon, number):
    return f"{PREFIX}-{addon.id}" if number == 0 else f"{PREFIX}-{addon.id}-{number + 1}"


def entry_path(addon, number, data_home):
    return data_home / "applications" / f"{stem(addon, number)}.desktop"


def icon_path(addon, number, data_home):
    return data_home / "icons" / "hicolor" / ICON_SIZE / "apps" / f"{stem(addon, number)}.png"


def exec_argument(value):
    quoted = re.sub(r'(["`$\\])', r"\\\1", value)
    return '"' + quoted.replace("\\", "\\\\").replace("%", "%%") + '"'


def entry_value(text):
    return text.replace("\\", "\\\\")


def launch_command(addon, cli, script, args=(), mime=False):
    words = [exec_argument(str(cli)), "addon", "launch", addon.id, script]
    tail = [exec_argument(arg) for arg in args] + (["%u"] if mime else [])
    return " ".join(words + (["--"] + tail if tail else []))


def entry_lines(addon, shortcut, cli, icon):
    title = shortcut["title"]
    script = shortcut.get("script", "launch")
    mime = shortcut.get("mime", [])
    close = shortcut.get("close")
    comment = shortcut.get("comment") or f"{title}, set up by the {addon.name} add-on"
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={entry_value(title)}",
        f"Comment={entry_value(comment)}",
        f"Exec={launch_command(addon, cli, script, shortcut.get('args', []), bool(mime))}",
    ]
    if icon:
        lines.append(f"Icon={entry_value(str(icon))}")
    if mime:
        lines.append("MimeType=" + "".join(f"{kind};" for kind in mime))
    if close:
        lines.append("Actions=close;")
    lines += ["Categories=Game;", "Terminal=false", f"{MARKER}={addon.id}"]
    if close:
        lines += ["", "[Desktop Action close]", f"Name=Close {entry_value(title)}", f"Exec={launch_command(addon, cli, close)}"]
    return lines


def icon_source(shortcut, found):
    spec = shortcut.get("icon")
    if not spec:
        return None, "the add-on names no program to take an icon from"
    folder = addons.place_folder(found, spec["place"])
    if not folder or not folder.is_dir():
        return None, f"the {spec['place']} folder was not found"
    source = next(iter(sorted(folder.glob(spec["glob"]))), None)
    if not source:
        return None, f"nothing in the {spec['place']} folder matches {spec['glob']}"
    if source.suffix.lower() == ".appimage":
        return None, "an AppImage has no icon resources to extract"
    return source, None


def extract_icon(source, target, tools):
    for program in ("7z", "magick"):
        if not tools.which(program):
            return f"{program} is not installed"
    with tempfile.TemporaryDirectory() as work:
        tools.run(["7z", "x", "-y", f"-o{work}", str(source), *ICON_RESOURCES])
        files = [path for path in Path(work).rglob("*") if path.is_file()]
        if not files:
            return f"{source.name} has no icon resources"
        target.parent.mkdir(parents=True, exist_ok=True)
        if tools.run(["magick", str(max(files, key=lambda path: path.stat().st_size)), "-resize", "256x256>", str(target)]) != 0:
            target.unlink(missing_ok=True)
            return "magick could not read the icon"
    return None


def make_icon(addon, number, shortcut, found, data_home, tools, say):
    source, reason = icon_source(shortcut, found)
    target = icon_path(addon, number, data_home)
    reason = reason or extract_icon(source, target, tools)
    if reason:
        say(f"no icon for {shortcut['title']}: {reason}; the menu entry has the default icon")
        return None
    return target


def written_files(addon, data_home):
    pattern = re.compile(rf"^{re.escape(PREFIX)}-{re.escape(addon.id)}(-\d+)?\.desktop$")
    found = []
    for path in sorted((data_home / "applications").glob(f"{PREFIX}-{addon.id}*.desktop")):
        if pattern.match(path.name) and f"{MARKER}={addon.id}" in path.read_text(errors="replace").splitlines():
            found += [path, data_home / "icons" / "hicolor" / ICON_SIZE / "apps" / f"{path.stem}.png"]
    return [path for path in found if path.exists()]


def refresh(data_home, tools):
    for program, wants_folder in DATABASES:
        if tools.which(program):
            tools.run([program, *([str(data_home / "applications")] if wants_folder else [])])


def has_shortcuts(addon, data_home):
    return bool(addon.shortcuts) and all(entry_path(addon, number, data_home).is_file() for number in range(len(addon.shortcuts)))


def remove_shortcuts(addon, data_home, tools=None):
    removed = written_files(addon, data_home)
    for path in removed:
        path.unlink()
    if removed:
        refresh(data_home, tools or Tools())
    return removed


def write_shortcuts(addon, found, cli, data_home, tools=None, say=print):
    tools = tools or Tools()
    for path in written_files(addon, data_home):
        path.unlink()
    (data_home / "applications").mkdir(parents=True, exist_ok=True)
    written = []
    for number, shortcut in enumerate(addon.shortcuts):
        icon = make_icon(addon, number, shortcut, found, data_home, tools, say)
        path = entry_path(addon, number, data_home)
        path.write_text("\n".join(entry_lines(addon, shortcut, cli, icon)) + "\n")
        written.append(path)
    refresh(data_home, tools)
    return written
