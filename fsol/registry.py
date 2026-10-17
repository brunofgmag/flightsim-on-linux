import re

SECTION = re.compile(r"^\[(.+?)\](?:\s+\d+)?\s*$")
VALUE = re.compile(r'^(?:"((?:[^"\\]|\\.)*)"|(@))=(.*)$')
STRING = re.compile(r'^(?:str\(\d\):)?"((?:[^"\\]|\\.)*)"$')
DWORD = re.compile(r"^dword:([0-9a-fA-F]{8})$")
ESCAPE = re.compile(r"\\(.)")


def unescape(text):
    return ESCAPE.sub(lambda match: {"n": "\n", "r": "\r", "t": "\t", "0": "\0"}.get(match.group(1), match.group(1)), text)


def decode(raw):
    string = STRING.match(raw)
    if string:
        return unescape(string.group(1))
    dword = DWORD.match(raw)
    if dword:
        return int(dword.group(1), 16)
    return raw


def logical_lines(text):
    pending = ""
    for line in text.splitlines():
        if line.endswith("\\") and not line.startswith("["):
            pending += line[:-1].strip()
            continue
        yield pending + line.strip() if pending else line
        pending = ""


def parse(text):
    keys = {}
    current = None
    for line in logical_lines(text):
        section = SECTION.match(line)
        if section:
            current = keys.setdefault(unescape(section.group(1)).lower(), {})
            continue
        value = VALUE.match(line) if current is not None else None
        if value:
            name = "" if value.group(2) else unescape(value.group(1))
            current[name] = decode(value.group(3))
    return keys


def load(path):
    with open(path, encoding="utf-8", errors="replace") as source:
        return parse(source.read())


def key(keys, path):
    return keys.get(path.lower())


def value(keys, path, name):
    found = key(keys, path) or {}
    return next((data for stored, data in found.items() if stored.lower() == name.lower()), None)
