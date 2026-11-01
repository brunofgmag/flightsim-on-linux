import argparse
import os
import re
import shutil
import sys
from pathlib import Path
from xml.sax.saxutils import escape, unescape

COMMENT = re.compile(r"<!--.*?-->", re.S)
ENTRY = re.compile(r"<Launch\.Addon>.*?</Launch\.Addon>", re.S)
PATH = re.compile(r"<Path>(.*?)</Path>", re.S | re.I)
DISABLED = re.compile(r"<Disabled>\s*true\s*</Disabled>", re.I)
DISABLED_TAG = re.compile(r"(<Disabled>\s*)\w*(\s*</Disabled>)", re.I)
LINE_END = re.compile(r"[ \t]*(\r?\n)?")
CLOSE = "</SimBase.Document>"
UTF16_MARKS = (b"\xff\xfe", b"\xfe\xff")
HEAD = '<?xml version="1.0" encoding="windows-1252"?>\n<SimBase.Document Type="Launch" version="1,0">\n    <Filename>exe.xml</Filename>\n'
DEFAULT_OUTER = "    "
DEFAULT_STEP = "    "


class ExeXmlError(Exception):
    pass


def read_text(path):
    data = Path(path).read_bytes()
    if data.startswith(UTF16_MARKS):
        raise ExeXmlError(f"{path} is UTF-16, which this tool does not edit")
    return data.decode("latin-1")


def write_text(path, text):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".fsol-new")
    temporary.write_bytes(text.encode("latin-1"))
    if target.exists():
        shutil.copymode(target, temporary)
    os.replace(temporary, target)


def visible(text):
    return COMMENT.sub(lambda match: " " * len(match.group(0)), text)


def entry_path(block):
    found = PATH.search(block)
    return unescape(found.group(1)).strip().casefold() if found else None


def find_entry(text, path):
    wanted = path.casefold()
    return next((match for match in ENTRY.finditer(visible(text)) if entry_path(match.group(0)) == wanted), None)


def state(text, path):
    match = find_entry(text, path)
    if not match:
        return "absent"
    return "disabled" if DISABLED.search(match.group(0)) else "enabled"


def entries_ending(text, suffix):
    wanted = suffix.casefold()
    for match in ENTRY.finditer(visible(text)):
        found = PATH.search(match.group(0))
        path = unescape(found.group(1)).strip() if found else ""
        if path.casefold().endswith(wanted):
            yield match.group(0), path


def state_ending(file, suffix):
    if not Path(file).is_file():
        return "no-file", None
    found = list(entries_ending(read_text(file), suffix))
    if not found:
        return "absent", None
    for block, path in found:
        if not DISABLED.search(block):
            return "enabled", path
    return "disabled", found[0][1]


def drive_root(prefix, letter):
    link = Path(prefix) / "dosdevices" / f"{letter.lower()}:"
    if link.exists():
        return link.resolve()
    return Path(prefix) / "drive_c" if letter.lower() == "c" else None


def find_ignoring_case(root, relative):
    current = Path(root)
    for part in [part for part in relative.replace("\\", "/").split("/") if part]:
        if (current / part).exists():
            current = current / part
            continue
        match = next((child for child in current.iterdir() if child.name.casefold() == part.casefold()), None) if current.is_dir() else None
        if match is None:
            return None
        current = match
    return current


def linux_path(prefix, windows_path):
    letter, separator, rest = windows_path.partition(":")
    root = drive_root(prefix, letter) if len(letter) == 1 and separator else None
    return find_ignoring_case(root, rest) if root else None


def newline_of(text):
    return "\r\n" if "\r\n" in text else "\n"


def own_line_start(text, position):
    start = text.rfind("\n", 0, position) + 1
    return start if not text[start:position].strip() else None


def line_indent(text, position):
    start = own_line_start(text, position)
    return None if start is None else text[start:position]


def indents(text):
    first = ENTRY.search(visible(text))
    if not first:
        return DEFAULT_OUTER, DEFAULT_OUTER + DEFAULT_STEP
    outer = line_indent(text, first.start())
    outer = DEFAULT_OUTER if outer is None else outer
    child = re.search(r"\n([ \t]+)<Name>", first.group(0))
    return outer, child.group(1) if child else outer + DEFAULT_STEP


def entry_lines(name, path, outer, inner):
    return [
        f"{outer}<Launch.Addon>",
        f"{inner}<Name>{escape(name)}</Name>",
        f"{inner}<Disabled>false</Disabled>",
        f"{inner}<Path>{escape(path)}</Path>",
        f"{outer}</Launch.Addon>",
    ]


def insert(text, lines):
    close = visible(text).rfind(CLOSE)
    if close < 0:
        raise ExeXmlError(f"no {CLOSE} in the file")
    newline = newline_of(text)
    block = newline.join(lines) + newline
    start = own_line_start(text, close)
    if start is None:
        return text[:close] + newline + block + text[close:]
    return text[:start] + block + text[start:]


def add_entry(text, name, path):
    outer, inner = indents(text)
    return insert(text, entry_lines(name, path, outer, inner))


def remove_entry(text, path):
    match = find_entry(text, path)
    start = own_line_start(text, match.start())
    if start is None:
        start = match.start()
    end = LINE_END.match(text, match.end()).end()
    return text[:start] + text[end:]


def with_disabled(block, value):
    if DISABLED_TAG.search(block):
        return DISABLED_TAG.sub(rf"\g<1>{value}\g<2>", block, count=1)
    return PATH.sub(lambda found: f"<Disabled>{value}</Disabled>{found.group(0)}", block, count=1)


def switch_entries(text, suffix, value):
    ending = suffix.casefold()
    pieces = []
    last = 0
    for match in ENTRY.finditer(visible(text)):
        if (entry_path(match.group(0)) or "").endswith(ending):
            pieces += [text[last:match.start()], with_disabled(text[match.start():match.end()], value)]
            last = match.end()
    return "".join(pieces) + text[last:]


def has_entries(text):
    return bool(ENTRY.search(visible(text)))


def command_status(args):
    if not Path(args.file).is_file():
        return "no-file"
    return state(read_text(args.file), args.path)


def command_add(args):
    exists = Path(args.file).is_file()
    text = read_text(args.file) if exists else HEAD + CLOSE + "\n"
    if state(text, args.path) != "absent":
        return "present"
    write_text(args.file, add_entry(text, args.name, args.path))
    return "added" if exists else "created"


def command_remove(args):
    if command_status(args) in ("no-file", "absent"):
        return "absent"
    text = remove_entry(read_text(args.file), args.path)
    if args.delete_if_empty and not has_entries(text):
        Path(args.file).unlink()
        return "deleted"
    write_text(args.file, text)
    return "removed"


def command_disable(args):
    state_now = state_ending(args.file, args.path)[0]
    if state_now != "enabled":
        return "already" if state_now == "disabled" else state_now
    write_text(args.file, switch_entries(read_text(args.file), args.path, "True"))
    return "disabled"


def command_enable(args):
    state_now = state_ending(args.file, args.path)[0]
    if state_now != "disabled":
        return "already" if state_now == "enabled" else state_now
    write_text(args.file, switch_entries(read_text(args.file), args.path, "False"))
    return "enabled"


def parser():
    root = argparse.ArgumentParser(prog="exe_xml.py")
    commands = root.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    status.set_defaults(run=command_status)
    add = commands.add_parser("add")
    add.add_argument("--name", required=True)
    add.set_defaults(run=command_add)
    remove = commands.add_parser("remove")
    remove.add_argument("--delete-if-empty", action="store_true")
    remove.set_defaults(run=command_remove)
    disable = commands.add_parser("disable")
    disable.set_defaults(run=command_disable)
    enable = commands.add_parser("enable")
    enable.set_defaults(run=command_enable)
    for command in (status, add, remove, disable, enable):
        command.add_argument("file")
        command.add_argument("path")
    return root


def main(argv):
    args = parser().parse_args(argv)
    try:
        print(args.run(args))
    except (ExeXmlError, OSError) as error:
        print(f"exe_xml.py: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
