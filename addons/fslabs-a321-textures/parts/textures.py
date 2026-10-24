import argparse
import json
import os
import shutil
import struct
import sys
from pathlib import Path

DEFAULT_EXTERIOR = 2048
DEFAULT_COCKPIT = 4096
SMALLEST_CAP = 4
HEADER = 128
DX10_HEADER = 148
CUBEMAP_OR_VOLUME = 0x200200
DX10_TEXTURE2D = 3
DX10_CUBEMAP_FLAG = 4
EIGHT_BYTE_FOURCC = {b"DXT1", b"BC4U", b"BC4S", b"ATI1"}
SIXTEEN_BYTE_FOURCC = {b"DXT2", b"DXT3", b"DXT4", b"DXT5", b"BC5U", b"BC5S", b"ATI2"}
EIGHT_BYTE_DXGI = {70, 71, 72, 79, 80, 81}
SIXTEEN_BYTE_DXGI = {73, 74, 75, 76, 77, 78, 82, 83, 84, 94, 95, 96, 97, 98, 99}
FILETIME_EPOCH = 116444736000000000
MANIFEST = "manifest.json"
MIB = 1024 * 1024


class PackageError(Exception):
    pass


def filetime(mtime_ns):
    return mtime_ns // 100 + FILETIME_EPOCH


def level_size(width, height, block):
    return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * block


def block_and_offset(header):
    if struct.unpack_from("<I", header, 112)[0] & CUBEMAP_OR_VOLUME:
        return None, None
    fourcc = header[84:88]
    if fourcc in EIGHT_BYTE_FOURCC:
        return 8, HEADER
    if fourcc in SIXTEEN_BYTE_FOURCC:
        return 16, HEADER
    if fourcc != b"DX10" or len(header) < DX10_HEADER:
        return None, None
    dxgi, dimension, misc, array = struct.unpack_from("<IIII", header, HEADER)
    if dimension != DX10_TEXTURE2D or misc & DX10_CUBEMAP_FLAG or array != 1:
        return None, None
    if dxgi in EIGHT_BYTE_DXGI:
        return 8, DX10_HEADER
    if dxgi in SIXTEEN_BYTE_DXGI:
        return 16, DX10_HEADER
    return None, None


def mip_chain(header, file_size):
    if len(header) < HEADER or header[:4] != b"DDS ":
        return None
    block, offset = block_and_offset(header)
    if block is None:
        return None
    height, width = struct.unpack_from("<II", header, 12)
    mips = max(1, struct.unpack_from("<I", header, 28)[0])
    levels = [level_size(max(1, width >> n), max(1, height >> n), block) for n in range(mips)]
    if offset + sum(levels) != file_size:
        return None
    return offset, width, height, levels


def levels_to_drop(chain, cap):
    _, width, height, levels = chain
    drop = 0
    while max(width, height) >> drop > cap:
        drop += 1
    return drop if 0 < drop < len(levels) else 0


def read_header(file):
    with open(file, "rb") as handle:
        return handle.read(DX10_HEADER)


def oversized(file, cap):
    chain = mip_chain(read_header(file), file.stat().st_size)
    return chain is not None and levels_to_drop(chain, cap) > 0


def shrink(data, cap):
    chain = mip_chain(data[:DX10_HEADER], len(data))
    if chain is None:
        return None
    drop = levels_to_drop(chain, cap)
    if drop == 0:
        return None
    offset, width, height, levels = chain
    header = bytearray(data[:offset])
    struct.pack_into("<III", header, 12, max(1, height >> drop), max(1, width >> drop), levels[drop])
    struct.pack_into("<I", header, 28, len(levels) - drop)
    return bytes(header) + data[offset + sum(levels[:drop]):]


def package_files(package):
    for folder, _, names in os.walk(package):
        for name in names:
            yield Path(folder) / name


def texture_caps(package, exterior, cockpit):
    caps = {}
    for model in package_files(package):
        if model.suffix.lower() != ".gltf":
            continue
        cap = cockpit if "interior" in model.name.lower() else exterior
        try:
            images = json.loads(model.read_text(errors="replace")).get("images", [])
        except json.JSONDecodeError:
            continue
        for image in images:
            name = image.get("uri", "").replace("\\", "/").rsplit("/", 1)[-1].lower()
            if name:
                caps[name] = max(caps.get(name, 0), cap)
    return caps


def textures(package, exterior, cockpit):
    caps = texture_caps(package, exterior, cockpit)
    for file in sorted(package_files(package)):
        cap = caps.get(file.name.lower())
        if cap and file.suffix.lower() == ".dds":
            yield file, cap


def read_layout(package):
    file = package / "layout.json"
    try:
        layout = json.loads(file.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise PackageError(f"{file} cannot be read: {error}") from error
    if not isinstance(layout.get("content"), list):
        raise PackageError(f"{file} has no content list")
    return layout


def write_atomically(file, data):
    temporary = file.with_name(file.name + ".fsol-tmp")
    temporary.write_bytes(data)
    os.replace(temporary, file)


def copy_atomically(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".fsol-tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, target)


def write_layout(package, layout):
    write_atomically(package / "layout.json", json.dumps(layout, separators=(",", ":")).encode())


def read_manifest(backup):
    file = backup / MANIFEST
    if not file.is_file():
        return {"files": {}}
    return json.loads(file.read_text())


def write_manifest(backup, manifest):
    backup.mkdir(parents=True, exist_ok=True)
    write_atomically(backup / MANIFEST, json.dumps(manifest, indent=1, sort_keys=True).encode())


def layout_index(layout):
    return {entry["path"].casefold(): entry for entry in layout["content"]}


def sync_entry(entries, package, rel):
    entry = entries.get(rel.casefold())
    if entry is not None:
        stat = (package / rel).stat()
        entry["size"] = stat.st_size
        entry["date"] = filetime(stat.st_mtime_ns)


def chosen_caps(args, manifest):
    exterior = args.exterior or manifest.get("exterior") or DEFAULT_EXTERIOR
    cockpit = args.cockpit or manifest.get("cockpit") or DEFAULT_COCKPIT
    if min(exterior, cockpit) < SMALLEST_CAP:
        raise PackageError(f"a texture size cap below {SMALLEST_CAP} pixels makes no sense")
    return exterior, cockpit


def apply_texture(package, backup, manifest, entries, file, cap):
    rel = file.relative_to(package).as_posix()
    record = manifest["files"].get(rel)
    original = backup / "files" / rel
    ours = record is not None and original.is_file() and file.stat().st_size == record["size"]
    if ours and record["cap"] == cap:
        return 0, 0
    source = original if ours else file
    if not ours and not oversized(file, cap):
        return 0, 0
    data = source.read_bytes()
    shrunk = shrink(data, cap)
    if not ours:
        copy_atomically(file, original)
        entry = entries.get(rel.casefold())
        record = {"layout": dict(entry) if entry else None}
    if shrunk is None:
        copy_atomically(original, file)
        original.unlink()
        if record["layout"] is not None:
            entries[rel.casefold()].update(record["layout"])
        manifest["files"].pop(rel, None)
        return len(data), len(data)
    write_atomically(file, shrunk)
    record.update(size=len(shrunk), cap=cap)
    manifest["files"][rel] = record
    sync_entry(entries, package, rel)
    return len(data), len(shrunk)


def apply(args):
    package, backup = args.package, args.backup
    manifest = read_manifest(backup)
    exterior, cockpit = chosen_caps(args, manifest)
    layout = read_layout(package)
    entries = layout_index(layout)
    manifest.update(exterior=exterior, cockpit=cockpit)
    changed, before, after = 0, 0, 0
    for file, cap in textures(package, exterior, cockpit):
        old, new = apply_texture(package, backup, manifest, entries, file, cap)
        if old:
            changed += 1
            before += old
            after += new
            write_manifest(backup, manifest)
    for rel in manifest["files"]:
        sync_entry(entries, package, rel)
    write_layout(package, layout)
    write_manifest(backup, manifest)
    if changed:
        print(f"changed {changed} textures to at most {exterior} pixels outside and {cockpit} in the cockpit, "
              f"{before / MIB:.0f} MB down to {after / MIB:.0f} MB; the originals are in {backup / 'files'}")
    else:
        print(f"already every texture is at most {exterior} pixels outside and {cockpit} in the cockpit")
    return 0


def undo(args):
    package, backup = args.package, args.backup
    manifest = read_manifest(backup)
    if not manifest["files"]:
        print("already nothing to undo: no texture was changed")
        return 0
    layout = read_layout(package)
    entries = layout_index(layout)
    restored, left = 0, []
    for rel, record in sorted(manifest["files"].items()):
        file, original = package / rel, backup / "files" / rel
        if original.is_file() and file.is_file() and file.stat().st_size == record["size"]:
            copy_atomically(original, file)
            if record["layout"] is not None and rel.casefold() in entries:
                entries[rel.casefold()].update(record["layout"])
            restored += 1
        else:
            left.append(rel)
        if original.is_file():
            original.unlink()
    write_layout(package, layout)
    (backup / MANIFEST).unlink()
    shutil.rmtree(backup / "files", ignore_errors=True)
    print(f"changed {restored} textures back to the originals")
    if left:
        print(f"left {len(left)} textures an update replaced after the change, such as {left[0]}")
    return 0


def check(args):
    package, backup = args.package, args.backup
    exterior, cockpit = chosen_caps(args, read_manifest(backup))
    layout = read_layout(package)
    big = [file.relative_to(package).as_posix() for file, cap in textures(package, exterior, cockpit) if oversized(file, cap)]
    wrong = [entry["path"] for entry in layout["content"]
             if not (package / entry["path"]).is_file() or (package / entry["path"]).stat().st_size != entry["size"]]
    good = True
    if big:
        good = False
        print(f"FAIL {len(big)} textures are larger than {exterior} pixels outside or {cockpit} in the cockpit, such as {big[0]}")
    else:
        print(f"ok every texture the models use is at most {exterior} pixels outside and {cockpit} in the cockpit")
    if wrong:
        good = False
        print(f"FAIL layout.json lists {len(wrong)} files with a size that differs from the disk, such as {wrong[0]}")
    else:
        print("ok layout.json lists the size each file has on disk")
    return 0 if good else 1


def guarded(action):
    def run(args):
        if not args.package.name or not (args.package / "layout.json").is_file():
            print(f"FAIL the FSLabs package was not found at {args.package}")
            return 1
        if not str(args.backup) or args.backup == Path():
            print("FAIL the machine has no compatdata folder to keep the original textures in")
            return 1
        try:
            return action(args)
        except PackageError as error:
            print(f"FAIL {error}")
            return 1
    return run


def package_path(value):
    return Path(value).resolve() if value else Path()


def parser():
    result = argparse.ArgumentParser(description="Caps the size of the FSLabs A321 textures by dropping their largest mip levels.")
    actions = result.add_subparsers(required=True)
    for name, action in (("apply", apply), ("check", check), ("undo", undo)):
        sub = actions.add_parser(name)
        sub.add_argument("package", type=package_path)
        sub.add_argument("backup", type=lambda value: Path(value) if value else Path())
        sub.add_argument("--exterior", type=int)
        sub.add_argument("--cockpit", type=int)
        sub.set_defaults(run=guarded(action))
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
