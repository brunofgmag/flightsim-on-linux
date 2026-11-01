import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape, unescape

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fsol import exe_xml  # noqa: E402

ENGINE = "couatl64_boot.exe"
WRAPPER = "com-environment.exe"
VARIABLE = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"
COMMAND_LINE = re.compile(r"<CommandLine>(.*?)</CommandLine>", re.S | re.I)
WRAPPED = re.compile(r'^\s*"([A-Za-z0-9_]+)=([^"]*)"\s+"([^"]*)"\s*(.*)$', re.S)


def basename(path):
    return re.split(r"[\\/]", path)[-1].casefold()


def command_line_of(block):
    found = COMMAND_LINE.search(block)
    return unescape(found.group(1)).strip() if found else ""


def path_of(block):
    found = exe_xml.PATH.search(block)
    return unescape(found.group(1)).strip() if found else ""


def wrapped_parts(block):
    found = WRAPPED.match(command_line_of(block))
    if basename(path_of(block)) != WRAPPER or not found or found.group(1) != VARIABLE:
        return None
    return found.group(2), found.group(3), found.group(4)


def starts_engine(block):
    parts = wrapped_parts(block)
    return basename(path_of(block)) == ENGINE or bool(parts and basename(parts[1]) == ENGINE)


def find(text):
    for match in exe_xml.ENTRY.finditer(exe_xml.visible(text)):
        block = text[match.start():match.end()]
        if starts_engine(block):
            return match.start(), match.end(), block
    return None


def state(text, options):
    match = find(text)
    if not match:
        return "absent"
    parts = wrapped_parts(match[2])
    if not parts:
        return "plain"
    return "wrapped" if parts[0] == options else "other"


def wrapper_of(text):
    match = find(text)
    return path_of(match[2]) if match and wrapped_parts(match[2]) else None


def replace_command_line(block, command_line):
    element = f"<CommandLine>{escape(command_line)}</CommandLine>"
    if COMMAND_LINE.search(block):
        return COMMAND_LINE.sub(lambda _: element, block, count=1) if command_line else re.sub(r"[ \t]*<CommandLine>.*?</CommandLine>[ \t]*\r?\n?", "", block, count=1, flags=re.S | re.I)
    if not command_line:
        return block
    path = exe_xml.PATH.search(block)
    indent = exe_xml.line_indent(block, path.start()) or "        "
    return block[:path.end()] + exe_xml.newline_of(block) + indent + element + block[path.end():]


def replace_path(block, path):
    return exe_xml.PATH.sub(lambda _: f"<Path>{escape(path)}</Path>", block, count=1)


def rewrite(text, match, path, command_line):
    start, end, block = match
    return text[:start] + replace_command_line(replace_path(block, path), command_line) + text[end:]


def wrap(text, wrapper, options):
    match = find(text)
    if state(text, options) != "plain":
        return text
    original = command_line_of(match[2])
    return rewrite(text, match, wrapper, f'"{VARIABLE}={options}" "{path_of(match[2])}"' + (f" {original}" if original else ""))


def unwrap(text):
    match = find(text)
    parts = wrapped_parts(match[2]) if match else None
    return rewrite(text, match, parts[1], parts[2]) if parts else text


def edit(file, change):
    if not Path(file).is_file():
        return "no-file"
    text = exe_xml.read_text(file)
    if not find(text):
        return "absent"
    changed = change(text)
    if changed == text:
        return "unchanged"
    exe_xml.write_text(file, changed)
    return "changed"


def status(file, options):
    return state(exe_xml.read_text(file), options) if Path(file).is_file() else "no-file"


def main(argv):
    try:
        if len(argv) == 3 and argv[0] == "status":
            print(status(argv[1], argv[2]))
        elif len(argv) == 4 and argv[0] == "wrap":
            print(edit(argv[1], lambda text: wrap(text, argv[2], argv[3])))
        elif len(argv) == 2 and argv[0] == "unwrap":
            print(edit(argv[1], unwrap))
        else:
            print("usage: couatl_entry.py status <exe.xml> <options> | wrap <exe.xml> <wrapper path> <options> | unwrap <exe.xml>", file=sys.stderr)
            return 2
    except (exe_xml.ExeXmlError, OSError) as error:
        print(f"couatl_entry.py: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
