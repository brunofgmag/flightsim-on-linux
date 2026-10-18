import argparse
import os
import re
import shutil
import sys
from pathlib import Path
from xml.sax.saxutils import unescape

COMMENT = re.compile(r"<!--.*?-->", re.S)
ENTRY = re.compile(r"<Launch\.Addon>.*?</Launch\.Addon>", re.S)
PATH = re.compile(r"<Path>(.*?)</Path>", re.S | re.I)
DISABLED = re.compile(r"<Disabled>\s*true\s*</Disabled>", re.I)
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
        f"{inner}<Name>{name}</Name>",
        f"{inner}<Disabled>false</Disabled>",
        f"{inner}<Path>{path}</Path>",
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
    for command in (status, add, remove):
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
