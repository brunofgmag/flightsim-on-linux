import os
import shutil
import struct
import sys
from collections import namedtuple
from pathlib import Path

CALL = 0x28
CALLVIRT = 0x6F
MEMBER_REF_TABLE = 0x0A
Spec = namedtuple("Spec", "namespace type method size original patched")
Method = namedtuple("Method", "name rva offset")

SPECS = {
    "register-assembly": Spec(
        "System.Runtime.InteropServices", "RegistrationServices", "RegisterAssembly", 6,
        lambda body: body[0] == 0x73 and body[5] == 0x7A,
        bytes([0x00, 0x00, 0x00, 0x00, 0x17, 0x2A]),
    ),
    "process-responding": Spec(
        "System.Diagnostics", "Process", "get_Responding", 2,
        lambda body: body == bytes([0x16, 0x2A]),
        bytes([0x17, 0x2A]),
    ),
}
CALL_SPECS = {
    "dispatcher-timer": ("System.Windows.Threading", "DispatcherTimer", "set_Interval", "get_TotalMilliseconds", "FromTicks"),
}
CODED = {
    "ResolutionScope": (2, (0x00, 0x1A, 0x23, 0x01)),
    "TypeDefOrRef": (2, (0x02, 0x01, 0x1B)),
    "MemberRefParent": (3, (0x02, 0x01, 0x1A, 0x06, 0x1B)),
}
SCHEMA = {
    0x00: ("u2", "string", "guid", "guid", "guid"),
    0x01: ("ResolutionScope", "string", "string"),
    0x02: ("u4", "string", "string", "TypeDefOrRef", 0x04, 0x06),
    0x03: (0x04,),
    0x04: ("u2", "string", "blob"),
    0x05: (0x06,),
    0x06: ("u4", "u2", "u2", "string", "blob", 0x08),
    0x07: (0x08,),
    0x08: ("u2", "u2", "string"),
    0x09: (0x02, "TypeDefOrRef"),
    0x0A: ("MemberRefParent", "string", "blob"),
}
TABLE_TYPEDEF = 0x02
TABLE_METHODDEF = 0x06


class ClrError(Exception):
    pass


def unpack(fmt, data, offset):
    try:
        return struct.unpack_from(fmt, data, offset)
    except struct.error:
        raise ClrError("the file ends before the structure it announces") from None


class Image:
    def __init__(self, data):
        self.data = data
        self.read_sections()
        self.read_streams()
        self.read_tables()

    def read_sections(self):
        data = self.data
        if data[:2] != b"MZ":
            raise ClrError("not a PE file")
        pe = unpack("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise ClrError("no PE signature")
        count, = unpack("<H", data, pe + 6)
        optional_size, = unpack("<H", data, pe + 20)
        optional = pe + 24
        magic, = unpack("<H", data, optional)
        directories = optional + (96 if magic == 0x10B else 112)
        self.sections = [unpack("<IIII", data, optional + optional_size + 40 * number + 8) for number in range(count)]
        self.sections = [(address, max(virtual, raw_size), pointer) for virtual, address, raw_size, pointer in self.sections]
        self.cli_rva, = unpack("<I", data, directories + 14 * 8)
        if not self.cli_rva:
            raise ClrError("no CLI header: this is not a .NET assembly")

    def offset_of(self, rva):
        for address, size, pointer in self.sections:
            if address <= rva < address + size:
                return rva - address + pointer
        raise ClrError(f"RVA {rva:#x} is in no section")

    def read_streams(self):
        data = self.data
        metadata = self.offset_of(unpack("<I", data, self.offset_of(self.cli_rva) + 8)[0])
        if unpack("<I", data, metadata)[0] != 0x424A5342:
            raise ClrError("no metadata signature")
        version_length, = unpack("<I", data, metadata + 12)
        position = metadata + 16 + version_length
        count, = unpack("<H", data, position + 2)
        position += 4
        self.streams = {}
        for _ in range(count):
            offset, size = unpack("<II", data, position)
            end = data.index(b"\0", position + 8)
            self.streams[data[position + 8:end].decode()] = (metadata + offset, size)
            position = (end + 4) & ~3
        for name in ("#~", "#Strings"):
            if name not in self.streams:
                raise ClrError(f"no {name} stream")

    def read_tables(self):
        data = self.data
        start, _ = self.streams["#~"]
        heap_sizes = data[start + 6]
        valid, = unpack("<Q", data, start + 8)
        position = start + 24
        self.rows = {}
        for table in range(64):
            if valid >> table & 1:
                self.rows[table], = unpack("<I", data, position)
                position += 4
        self.string_width = 4 if heap_sizes & 1 else 2
        self.guid_width = 4 if heap_sizes & 2 else 2
        self.blob_width = 4 if heap_sizes & 4 else 2
        self.layout = {}
        for table in sorted(SCHEMA):
            count = self.rows.get(table, 0)
            widths = [self.column_width(column) for column in SCHEMA[table]]
            self.layout[table] = (position, widths, sum(widths), count)
            position += sum(widths) * count

    def simple_width(self, table):
        return 2 if self.rows.get(table, 0) < 65536 else 4

    def coded_width(self, name):
        bits, tables = CODED[name]
        return 2 if max(self.rows.get(table, 0) for table in tables) < 1 << (16 - bits) else 4

    def column_width(self, column):
        if isinstance(column, int):
            return self.simple_width(column)
        fixed = {"u2": 2, "u4": 4, "string": self.string_width, "guid": self.guid_width, "blob": self.blob_width}
        return fixed[column] if column in fixed else self.coded_width(column)

    def cell(self, table, row, column):
        position, widths, size, count = self.layout[table]
        if not 1 <= row <= count:
            raise ClrError(f"row {row} is outside table {table:#x}")
        offset = position + (row - 1) * size + sum(widths[:column])
        return int.from_bytes(self.data[offset:offset + widths[column]], "little")

    def string(self, index):
        start, size = self.streams["#Strings"]
        end = self.data.index(b"\0", start + index)
        return self.data[start + index:end].decode("utf-8", "replace")

    def methods_of(self, type_row):
        count = self.rows[TABLE_METHODDEF]
        first = self.cell(TABLE_TYPEDEF, type_row, 5)
        following = self.cell(TABLE_TYPEDEF, type_row + 1, 5) if type_row < self.rows[TABLE_TYPEDEF] else count + 1
        for row in range(first, min(following, count + 1)):
            name = self.string(self.cell(TABLE_METHODDEF, row, 3))
            rva = self.cell(TABLE_METHODDEF, row, 0)
            yield Method(name, rva, self.offset_of(rva) if rva else None)

    def find_method(self, namespace, type_name, method_name):
        for row in range(1, self.rows[TABLE_TYPEDEF] + 1):
            if self.string(self.cell(TABLE_TYPEDEF, row, 2)) == namespace and self.string(self.cell(TABLE_TYPEDEF, row, 1)) == type_name:
                found = [method for method in self.methods_of(row) if method.name == method_name]
                if len(found) != 1:
                    raise ClrError(f"expected one {method_name} in {namespace}.{type_name}, found {len(found)}")
                return found[0]
        raise ClrError(f"type {namespace}.{type_name} not found")

    def body(self, method):
        if method.offset is None:
            raise ClrError(f"{method.name} has no body")
        header = self.data[method.offset]
        if header & 3 == 2:
            return method.offset + 1, header >> 2
        if header & 3 == 3:
            header_size = (self.data[method.offset + 1] >> 4) * 4
            return method.offset + header_size, unpack("<I", self.data, method.offset + 4)[0]
        raise ClrError(f"unexpected method header {header:#x}")

    def member_name(self, row):
        return self.string(self.cell(MEMBER_REF_TABLE, row, 1))

    def called_names(self, code):
        names = set()
        for index in range(len(code) - 4):
            if code[index] in (CALL, CALLVIRT):
                token = int.from_bytes(code[index + 1:index + 5], "little")
                row = token & 0xFFFFFF
                if token >> 24 == MEMBER_REF_TABLE and 1 <= row <= self.layout[MEMBER_REF_TABLE][3]:
                    names.add(self.member_name(row))
        return names


def locate(image, spec):
    method = image.find_method(spec.namespace, spec.type, spec.method)
    start, size = image.body(method)
    return method, start, size


def spec_state(data, spec):
    image = Image(data)
    method, start, size = locate(image, spec)
    body = bytes(data[start:start + size])
    if body == spec.patched:
        return "patched", method, start
    if size == spec.size and spec.original(body):
        return "original", method, start
    return "unknown", method, start


def call_state(data, name):
    namespace, type_name, method_name, original_call, patched_call = CALL_SPECS[name]
    image = Image(data)
    start, size = image.body(image.find_method(namespace, type_name, method_name))
    names = image.called_names(bytes(data[start:start + size]))
    if patched_call in names:
        return "patched"
    return "original" if original_call in names else "unknown"


def state_of(path, name):
    data = Path(path).read_bytes()
    if name in CALL_SPECS:
        return call_state(data, name)
    return spec_state(data, SPECS[name])[0]


def apply_patch(path, name, out=None):
    data = bytearray(Path(path).read_bytes())
    state, method, start = spec_state(data, SPECS[name])
    if state == "unknown":
        raise ClrError(f"{SPECS[name].method} is neither the original nor the patched body: not touching it")
    if state == "original":
        data[start:start + SPECS[name].size] = SPECS[name].patched
    target = Path(out or path)
    temporary = target.with_name(target.name + ".fsol-new")
    temporary.write_bytes(data)
    shutil.copymode(path, temporary)
    os.replace(temporary, target)
    return state


def main(argv):
    if len(argv) < 3 or argv[0] not in ("state", "apply") or (argv[0] == "apply" and argv[2] not in SPECS) or argv[2] not in {**SPECS, **CALL_SPECS}:
        print(f"usage: clr.py state <dll> <{'|'.join({**SPECS, **CALL_SPECS})}> | clr.py apply <dll> <{'|'.join(SPECS)}> [<out>]", file=sys.stderr)
        return 2
    try:
        if argv[0] == "state":
            print(state_of(argv[1], argv[2]))
        else:
            print(apply_patch(argv[1], argv[2], argv[3] if len(argv) > 3 else None))
    except (ClrError, OSError) as error:
        print(f"clr.py: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
