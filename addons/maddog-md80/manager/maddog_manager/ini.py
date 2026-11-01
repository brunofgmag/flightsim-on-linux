import codecs
import re
from pathlib import Path

SECTION = re.compile(r"^\s*\[([^\]]*)\]")


def decode(data):
    if data.startswith(codecs.BOM_UTF16_LE):
        return data[2:].decode("utf-16-le"), "utf-16-le", codecs.BOM_UTF16_LE
    if data.startswith(codecs.BOM_UTF16_BE):
        return data[2:].decode("utf-16-be"), "utf-16-be", codecs.BOM_UTF16_BE
    if data.startswith(codecs.BOM_UTF8):
        return data[3:].decode("utf-8", "surrogateescape"), "utf-8", codecs.BOM_UTF8
    return data.decode("cp1252", "surrogateescape"), "cp1252", b""


def unquote(value):
    for quote in ('"', "'"):
        if value.startswith(quote):
            end = value.find(quote, 1)
            if end > 0:
                return value[1:end]
    return value


class IniFile:
    def __init__(self, path=None, data=None):
        self.path = Path(path) if path is not None else None
        self.encoding = "cp1252"
        self.bom = b""
        self.newline = "\r\n"
        self.lines = []
        self.changed = False
        if data is None and self.path is not None and self.path.is_file():
            data = self.path.read_bytes()
        if data is not None:
            text, self.encoding, self.bom = decode(data)
            if "\r\n" not in text and "\n" in text:
                self.newline = "\n"
            self.lines = text.splitlines()

    def section_range(self, section):
        start = None
        for index, line in enumerate(self.lines):
            match = SECTION.match(line)
            if not match:
                continue
            if start is not None:
                return start, index
            if match.group(1).strip().lower() == section.lower():
                start = index + 1
        if start is None:
            return None
        return start, len(self.lines)

    def find_key(self, section, key):
        found = self.section_range(section)
        if found is None:
            return None, None
        start, end = found
        for index in range(start, end):
            line = self.lines[index]
            if line.lstrip().startswith(";") or "=" not in line:
                continue
            if line.split("=", 1)[0].strip().lower() == key.lower():
                return found, index
        return found, None

    def has(self, section, key):
        return self.find_key(section, key)[1] is not None

    def get(self, section, key, default=""):
        index = self.find_key(section, key)[1]
        if index is None:
            return default
        return unquote(self.lines[index].split("=", 1)[1].strip())

    def get_int(self, section, key, default=0):
        try:
            return int(self.get(section, key).strip())
        except ValueError:
            return default

    def get_bool(self, section, key, default=False):
        value = self.get(section, key).strip()
        if not value:
            return default
        if value.lower() == "true":
            return True
        try:
            return int(value) >= 1
        except ValueError:
            return False

    def set(self, section, key, value):
        value = str(value)
        found, index = self.find_key(section, key)
        if index is not None:
            name = self.lines[index].split("=", 1)[0]
            line = f"{name}={value}"
            if self.lines[index] == line:
                return
            self.lines[index] = line
        elif found is not None:
            start, end = found
            while end > start and not self.lines[end - 1].strip():
                end -= 1
            self.lines.insert(end, f"{key}={value}")
        else:
            self.lines.extend([f"[{section}]", f"{key}={value}"])
        self.changed = True

    def set_int(self, section, key, value):
        self.set(section, key, int(value))

    def set_bool(self, section, key, value):
        self.set(section, key, "1" if value else "0")

    def save(self):
        if not self.changed:
            return
        text = self.newline.join(self.lines) + self.newline
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(self.bom + text.encode(self.encoding, "surrogateescape"))
        self.changed = False


def write_values(path, values):
    ini = IniFile(path)
    for section, key, value in values:
        ini.set(section, key, value)
    ini.save()
