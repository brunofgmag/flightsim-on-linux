import hashlib
import importlib.util
import os
import re
import sys
import zipfile
from collections import namedtuple
from pathlib import Path

ADDONS = Path(__file__).resolve().parents[2]
ADDON_FILE = "renodx-msfs2024.addon64"
ADDON_SHA256 = "304008e72d2fa6a9efe23bea527dec79525ae82decd7c16ef0c36bb27eeecb7e"
NEXUS = "https://www.nexusmods.com/microsoftflightsimulator2024/mods/26"
OTHER_RENODX = "renodx-*.addon64"
REGISTERED = 'Registered add-on "RenoDX"'
FAILED = "Failed to replace pipeline"
INSERTED = re.compile(r"will insert cbuffer (\d+) at root_index (\d+)")
KEY = "InjectGraphicsRootSignature"
SPACE_KEY = "RegisterSpace"
STASH = f"{KEY}.orig"
RENODX_SPACE = 50
FIRST_WITH_RENODX_OPTION = (0, 7, 0)
INSTALL = "flightsim-on-linux addon install renodx-hdr"
APPLY = "flightsim-on-linux addon apply renodx-hdr"
REINSTALL = "flightsim-on-linux addon install atmospheremod"
Situation = namedtuple("Situation", "installed state spaces source version")
USAGE = "usage: renodx.py install|apply|check|undo <simulator folder> <prefix folder> <backup folder>"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


atmosphere = load("atmosphere", ADDONS / "atmospheremod" / "parts" / "atmosphere.py")
ok, fail, line, modified = atmosphere.ok, atmosphere.fail, atmosphere.line, atmosphere.modified


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def other_renodx(sim):
    return sorted(path.name for path in Path(sim).glob(OTHER_RENODX) if path.name != ADDON_FILE)


def addon_bytes(path):
    if not zipfile.is_zipfile(path):
        return path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        member = next((name for name in archive.namelist() if Path(name).name == ADDON_FILE), None)
        return None if member is None else archive.read(member)


def refusal(sim, given):
    if os.environ.get("FSOL_SIM_RUNNING", "0") == "1":
        return "close the simulator before you install the add-on"
    if not (Path(sim) / atmosphere.RESHADE_DLL).is_file():
        return "ReShade is not in the simulator folder; run first: flightsim-on-linux addon install reshade <dxgi.dll of a Windows ReShade install>"
    others = other_renodx(sim)
    if others:
        return f"{', '.join(others)} is in the simulator folder; RenoDX's README allows no other RenoDX add-on beside {ADDON_FILE}, so move it away first"
    if not given:
        return f"no file given; download the mod from {NEXUS} and run: {INSTALL} <the zip you downloaded>"
    if not Path(given).is_file():
        return f"{given} is not a file"
    return None


def install(sim):
    given = os.environ.get("FSOL_INSTALLER", "")
    problem = refusal(sim, given)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    data = addon_bytes(Path(given))
    if data is None:
        print(f"{given} holds no {ADDON_FILE}; nothing was installed", file=sys.stderr)
        return 1
    if sha256(data) != ADDON_SHA256:
        print(f"{ADDON_FILE} in {given} is not the release this add-on was tested with (sha256 {ADDON_SHA256}); nothing was installed", file=sys.stderr)
        return 1
    target = Path(sim) / ADDON_FILE
    target.write_bytes(data)
    target.chmod(0o644)
    print(f"{ADDON_FILE} is in {sim}")
    return 0


def injection_state(text):
    return {"1": "on", "0": "off"}.get(atmosphere.read_value(text, atmosphere.SECTION, KEY), "unknown")


def configured_space(text):
    value = atmosphere.read_value(text, atmosphere.SECTION, SPACE_KEY)
    return [int(value)] if value and value.isdigit() else None


def where_parameters_go(sim, prefix, text):
    measured = atmosphere.mod_spaces(sim, prefix)
    if measured is not None:
        return measured, "layouts.txt"
    configured = configured_space(text)
    if configured is not None:
        return configured, SPACE_KEY
    return None, None


def installed_version(prefix):
    record = atmosphere.read_record(prefix) or {}
    numbers = re.findall(r"\d+", str(record.get("version", "")))[:3]
    return tuple(int(number) for number in numbers) or None


def situation(sim, prefix):
    sim = Path(sim)
    installed = (sim / atmosphere.ADDON_FILE).is_file()
    text = atmosphere.read_text(sim / atmosphere.RESHADE_INI) or ""
    spaces, source = where_parameters_go(sim, prefix, text)
    return Situation(installed, injection_state(text), spaces, source, installed_version(prefix))


def has_renodx_option(now):
    return now.version is not None and now.version >= FIRST_WITH_RENODX_OPTION


def describe_spaces(now):
    if now.spaces is None:
        return f"space 50, which is assumed because layouts.txt is missing or older than the AtmosphereMod install and ReShade.ini has no {SPACE_KEY} under [{atmosphere.SECTION}]"
    named = "space " + ", ".join(str(space) for space in now.spaces)
    if now.source == SPACE_KEY:
        return f"{named}, which {SPACE_KEY} under [{atmosphere.SECTION}] in ReShade.ini sets, because layouts.txt is missing or older than the AtmosphereMod install"
    return named


def version_text(now):
    return ".".join(str(number) for number in now.version) if now.version else "of unknown version"


def move_step(now):
    if has_renodx_option(now):
        return f"run: {REINSTALL}, which runs AtmosphereMod's installer with --renodx because RenoDX is in the simulator folder, and that moves AtmosphereMod to space 51 with the injection on"
    return f"run: {REINSTALL}, which updates AtmosphereMod {version_text(now)} to a release whose installer moves it to space 51 with the injection on, or keep this release and run: {APPLY}, which turns the injection off"


def collides(spaces):
    return spaces is None or RENODX_SPACE in spaces


def verdict(now):
    if not now.installed:
        return "absent"
    if now.state == "off":
        return "off"
    if not collides(now.spaces):
        return "apart"
    return now.state


def apart_message(now):
    return f"AtmosphereMod puts its parameters in {describe_spaces(now)}, and RenoDX uses space {RENODX_SPACE}, so they do not meet"


def unknown_message(next_step):
    return f"{KEY} is not 0 or 1 under [{atmosphere.SECTION}] in ReShade.ini, so this add-on cannot tell what AtmosphereMod does; run the AtmosphereMod install again, {next_step}"


def unknown_step(now):
    return f"with: {REINSTALL}" if has_renodx_option(now) else f"which sets it to 1, and then: {APPLY}"


def skip_reason(now):
    kind = verdict(now)
    if kind == "absent":
        return "AtmosphereMod is not installed, so nothing else puts parameters into the root signature RenoDX needs"
    if kind == "off":
        return f"{KEY} is already 0 under [{atmosphere.SECTION}]"
    if kind == "apart":
        return apart_message(now)
    if kind == "unknown":
        return unknown_message(unknown_step(now))
    return None


def stash_original(backup, value):
    stash = Path(backup) / STASH
    if stash.exists():
        os.utime(stash)
        return
    stash.parent.mkdir(parents=True, exist_ok=True)
    stash.write_text(value)


def write_ini(sim, text):
    (Path(sim) / atmosphere.RESHADE_INI).write_bytes(text.encode("utf-8", errors="surrogateescape"))


def apply(sim, prefix, backup):
    sim = Path(sim)
    if not (sim / ADDON_FILE).is_file():
        print(f"{sim / ADDON_FILE} does not exist; run: {INSTALL}", file=sys.stderr)
        return 1
    now = situation(sim, prefix)
    reason = skip_reason(now)
    if reason:
        print(f"nothing was changed: {reason}")
        return 0
    if has_renodx_option(now):
        print(f"nothing was changed: AtmosphereMod {version_text(now)} puts its parameters in {describe_spaces(now)}, where RenoDX puts its own; {move_step(now)}", file=sys.stderr)
        return 1
    text = atmosphere.read_text(sim / atmosphere.RESHADE_INI)
    stash_original(backup, atmosphere.read_value(text, atmosphere.SECTION, KEY))
    write_ini(sim, atmosphere.replace_value(text, atmosphere.SECTION, KEY, "0"))
    print(f"{KEY}=0 is set under [{atmosphere.SECTION}] in ReShade.ini, because AtmosphereMod {version_text(now)} puts its parameters in {describe_spaces(now)}, where RenoDX puts its own; the value it had is kept for undo")
    print("the cost: AtmosphereMod's ground haze (its ps_deferredRendering pass) stays the simulator's own, because that pass needs the injected root signature; its sky and clouds keep the mod")
    print("the AtmosphereMod install sets the value back to 1, so run apply again after every run of it")
    print(f"to keep the haze, run instead: {REINSTALL}, which updates AtmosphereMod to a release whose installer moves it to space 51 with the injection on")
    return 0


def addon_lines(sim):
    path = Path(sim) / ADDON_FILE
    present = path.is_file()
    lines = [line(present, f"{ADDON_FILE} is in the simulator folder", f"{ADDON_FILE} is not in the simulator folder; run: {INSTALL}")]
    if present:
        digest = sha256(path.read_bytes())
        lines.append(line(digest == ADDON_SHA256, f"{ADDON_FILE} is the release this add-on was tested with", f"{ADDON_FILE} has sha256 {digest}, not the {ADDON_SHA256} this add-on was tested with, so it is untested with this file"))
    others = other_renodx(sim)
    lines.append(line(not others, "no other RenoDX add-on is in the simulator folder", f"{', '.join(others)} is in the simulator folder, and RenoDX's README allows no other RenoDX add-on beside {ADDON_FILE}"))
    return lines


def conflict_line(sim, prefix):
    now = situation(sim, prefix)
    kind = verdict(now)
    if kind == "absent":
        return ok("AtmosphereMod is not installed, so it does not change the root signature RenoDX needs")
    if kind == "off":
        return ok(f"{KEY}=0 under [{atmosphere.SECTION}], so AtmosphereMod leaves the graphics root signature alone and RenoDX can add its own")
    if kind == "apart":
        return ok(apart_message(now))
    if kind == "on":
        return fail(f"RenoDX fails to replace its shaders, because AtmosphereMod adds its parameters to the same graphics root signature in the register space RenoDX uses, {describe_spaces(now)}; {move_step(now)}")
    return fail(unknown_message(unknown_step(now)))


def settings_changed_at(sim, prefix, backup):
    times = [modified(Path(sim) / ADDON_FILE), atmosphere.installed_at(sim, prefix), modified(Path(backup) / STASH)]
    return max((time for time in times if time is not None), default=0)


def log_line(sim, prefix, backup):
    log = Path(sim) / atmosphere.RESHADE_LOG
    text = atmosphere.read_text(log)
    if text is None or REGISTERED not in text:
        return []
    if modified(log) <= settings_changed_at(sim, prefix, backup):
        return [ok(f"{atmosphere.RESHADE_LOG} is older than the last change to the add-ons or their settings, so it says nothing about them yet; start the simulator, then run the check again")]
    failures = text.count(FAILED)
    if failures:
        return [fail(f"{atmosphere.RESHADE_LOG} says RenoDX failed to replace a pipeline {failures} times; read the lines around '{FAILED}' in it")]
    inserted = INSERTED.search(text)
    if inserted:
        return [ok(f"{atmosphere.RESHADE_LOG} says RenoDX inserts its cbuffer {inserted.group(1)} at root_index {inserted.group(2)}, and no pipeline failed to be replaced")]
    return []


def check(sim, prefix, backup):
    reshade = line((Path(sim) / atmosphere.RESHADE_DLL).is_file(), "ReShade's dxgi.dll is in the simulator folder", "ReShade's dxgi.dll is not in the simulator folder; run: flightsim-on-linux addon install reshade <dxgi.dll>")
    lines = [reshade] + addon_lines(sim) + [conflict_line(sim, prefix)] + log_line(sim, prefix, backup)
    for _, text in lines:
        print(text)
    return 0 if all(good for good, _ in lines) else 1


def undo(sim, backup):
    stash = Path(backup) / STASH
    if not stash.exists():
        print("nothing to undo: apply did not change anything")
    else:
        restore(Path(sim), stash.read_text().strip())
        stash.unlink()
    print(f"the add-on itself stays in the simulator's folder; delete {ADDON_FILE} from it to remove it")
    return 0


def restore(sim, value):
    text = atmosphere.read_text(sim / atmosphere.RESHADE_INI)
    restored = None if text is None else atmosphere.replace_value(text, atmosphere.SECTION, KEY, value)
    if restored is None:
        print(f"{KEY} is no longer under [{atmosphere.SECTION}] in ReShade.ini, so there is nothing to put back")
        return
    write_ini(sim, restored)
    print(f"{KEY}={value} is back under [{atmosphere.SECTION}] in ReShade.ini")


def main(argv):
    if len(argv) != 4 or argv[0] not in ("install", "apply", "check", "undo"):
        print(USAGE, file=sys.stderr)
        return 2
    action, sim, prefix, backup = argv
    if not sim:
        print("FAIL no simulator install folder found; run: flightsim-on-linux machine")
        return 1
    if action == "install":
        return install(sim)
    if action == "apply":
        return apply(sim, prefix, backup)
    if action == "check":
        return check(sim, prefix, backup)
    return undo(sim, backup)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
