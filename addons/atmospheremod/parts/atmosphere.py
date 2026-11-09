import json
import re
import sys
from pathlib import Path

ADDON_FILE = "AtmosphereMod.addon64"
RESHADE_DLL = "dxgi.dll"
RESHADE_INI = "ReShade.ini"
RESHADE_LOG = "ReShade.log"
SECTION = "ATMOSPHEREMOD"
SPACE_KEY = "RegisterSpace"
DEFAULT_SPACE = 50
PATHS = ("DataPath", "TunablesPath")
RECORD = "install.json"
SHADER_PACK = "SHADERSPAK.*.SM6.PC.PAK"
DATA_FOLDER = ("drive_c", "users", "steamuser", "AppData", "Local", "AtmosphereMod")
REGISTERED = re.compile(r'Registered add-on "AtmosphereMod" v(\S+)')
MOD_PARAMETERS = re.compile(r"root constants b0 space(\d+) x16 dwords")
INSTALL = "flightsim-on-linux addon install atmospheremod"
USAGE = "usage: atmosphere.py check <simulator folder> <prefix folder>"


def data_folder(prefix):
    return Path(prefix).joinpath(*DATA_FOLDER)


def read_text(path):
    path = Path(path)
    return path.read_bytes().decode("utf-8", errors="surrogateescape") if path.is_file() else None


def modified(path):
    path = Path(path)
    return path.stat().st_mtime if path.is_file() else None


def section_bounds(lines, section):
    start = next((i for i, line in enumerate(lines) if line.strip().lower() == f"[{section.lower()}]"), None)
    if start is None:
        return None, None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    return start, end


def key_index(lines, start, end, key):
    for i in range(start + 1, end):
        name, _, _ = lines[i].partition("=")
        if name.strip().lower() == key.lower():
            return i
    return None


def locate(text, section, key):
    lines = text.splitlines(keepends=True)
    start, end = section_bounds(lines, section)
    if start is None:
        return lines, None
    return lines, key_index(lines, start, end, key)


def read_value(text, section, key):
    lines, index = locate(text, section, key)
    return None if index is None else lines[index].partition("=")[2].strip()


def replace_value(text, section, key, value):
    lines, index = locate(text, section, key)
    if index is None:
        return None
    head, _, _ = lines[index].partition("=")
    ending = lines[index][len(lines[index].rstrip("\r\n")):]
    lines[index] = f"{head}={value}{ending}"
    return "".join(lines)


def read_record(prefix):
    try:
        record = json.loads((data_folder(prefix) / RECORD).read_text())
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def installed_at(sim, prefix):
    return modified(data_folder(prefix) / RECORD) or modified(Path(sim) / ADDON_FILE)


def current_pack(sim):
    packs = sorted((Path(sim) / "Shaders").glob(SHADER_PACK), key=lambda pack: pack.stat().st_mtime)
    return packs[-1].name if packs else None


def mod_spaces(sim, prefix):
    layouts = data_folder(prefix) / "logs" / "layouts.txt"
    since = installed_at(sim, prefix)
    text = read_text(layouts)
    if text is None or since is None or modified(layouts) <= since:
        return None
    return sorted({int(space) for space in MOD_PARAMETERS.findall(text)}) or None


def ok(text):
    return True, f"ok {text}"


def fail(text):
    return False, f"FAIL {text}"


def line(good, passed, failed):
    return ok(passed) if good else fail(failed)


def presence_lines(sim):
    sim = Path(sim)
    return [
        line((sim / RESHADE_DLL).is_file(), "ReShade's dxgi.dll is in the simulator folder", "ReShade's dxgi.dll is not in the simulator folder; run: flightsim-on-linux addon install reshade <dxgi.dll>"),
        line((sim / ADDON_FILE).is_file(), f"{ADDON_FILE} is in the simulator folder", f"{ADDON_FILE} is not in the simulator folder; run: {INSTALL}"),
    ]


def settings_line(sim):
    text = read_text(Path(sim) / RESHADE_INI)
    if text is None:
        return fail(f"{RESHADE_INI} does not exist in the simulator folder; run: {INSTALL}")
    missing = [key for key in PATHS if not read_value(text, SECTION, key)]
    return line(not missing, f"[{SECTION}] in {RESHADE_INI} has {' and '.join(PATHS)}", f"[{SECTION}] in {RESHADE_INI} has no {' or '.join(missing)}; run: {INSTALL}")


def record_lines(record, prefix):
    data = data_folder(prefix)
    if record is None:
        return [fail(f"{RECORD} is missing or unreadable in {data}; run: {INSTALL}")]
    failed, patched = record.get("failed"), record.get("patched")
    shaders = len(list((data / "shaders").glob("*.cso")))
    return [
        line(failed == 0, "the installer patched every shader it tried", f"{RECORD} says the installer failed to patch {failed} shaders; run the install again, and if it repeats read {data / 'logs' / 'install.log'}"),
        line(patched == shaders, f"{patched} shaders are patched, and the shaders folder holds {shaders} .cso files", f"{RECORD} says {patched} shaders were patched, but the shaders folder holds {shaders} .cso files; run: {INSTALL}"),
    ]


def pack_line(sim, record):
    pack = current_pack(sim)
    if pack is None:
        return fail(f"the simulator folder has no {SHADER_PACK} in its Shaders folder, so the patched shaders cannot be matched to the simulator")
    recorded = record.get("pak")
    return line(recorded == pack, f"the shaders were patched from {pack}, the shader pack the simulator has now", f"the simulator was updated: its shader pack is {pack} and the shaders were patched from {recorded}; run the install again")


def space_lines(sim, record):
    patched = record.get("register_space")
    text = read_text(Path(sim) / RESHADE_INI)
    if patched is None or text is None or section_bounds(text.splitlines(), SECTION)[0] is None:
        return []
    value = read_value(text, SECTION, SPACE_KEY)
    used = str(DEFAULT_SPACE) if value is None else value
    setting = f"no {SPACE_KEY}, which means space {DEFAULT_SPACE}," if value is None else f"{SPACE_KEY}={value}"
    return [line(used == str(patched), f"the shaders were patched for register space {patched}, and [{SECTION}] in {RESHADE_INI} has {setting} for the add-on to match", f"the shaders were patched for register space {patched}, but [{SECTION}] in {RESHADE_INI} has {setting}, so the add-on looks in another space and the simulator keeps its own shaders; run: {INSTALL}")]


def log_line(sim, prefix):
    text = read_text(Path(sim) / RESHADE_LOG)
    since = installed_at(sim, prefix)
    if text is None or since is None:
        return []
    registered = REGISTERED.search(text)
    if registered:
        return [ok(f"{RESHADE_LOG} says ReShade registered AtmosphereMod {registered.group(1)}")]
    if modified(Path(sim) / RESHADE_LOG) > since:
        return [fail(f"{RESHADE_LOG} was written after the install and does not say ReShade registered AtmosphereMod; read the first error in it")]
    return []


def check_lines(sim, prefix):
    record = read_record(prefix)
    lines = presence_lines(sim) + [settings_line(sim)] + record_lines(record, prefix)
    if record is not None:
        lines.append(pack_line(sim, record))
        lines += space_lines(sim, record)
    return lines + log_line(sim, prefix)


def check(sim, prefix):
    lines = check_lines(sim, prefix)
    for _, text in lines:
        print(text)
    return 0 if all(good for good, _ in lines) else 1


def main(argv):
    if len(argv) != 3 or argv[0] != "check":
        print(USAGE, file=sys.stderr)
        return 2
    _, sim, prefix = argv
    if not sim:
        print("FAIL no simulator install folder found; run: flightsim-on-linux machine")
        return 1
    if not prefix:
        print("FAIL no prefix found; run: flightsim-on-linux machine")
        return 1
    return check(sim, prefix)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
