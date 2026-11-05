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
STASH = f"{KEY}.orig"
RENODX_SPACE = 50
INSTALL = "flightsim-on-linux addon install renodx-hdr"
APPLY = "flightsim-on-linux addon apply renodx-hdr"
Situation = namedtuple("Situation", "installed state spaces")
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


def situation(sim, prefix):
    sim = Path(sim)
    installed = (sim / atmosphere.ADDON_FILE).is_file()
    state = injection_state(atmosphere.read_text(sim / atmosphere.RESHADE_INI) or "")
    return Situation(installed, state, atmosphere.mod_spaces(sim, prefix))


def describe_spaces(spaces):
    if spaces is None:
        return "space 50, which is assumed because layouts.txt is missing or older than the AtmosphereMod install"
    return "space " + ", ".join(str(space) for space in spaces)


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
    return f"AtmosphereMod puts its parameters in {describe_spaces(now.spaces)}, and RenoDX uses space {RENODX_SPACE}, so they do not meet"


def unknown_message(next_step):
    return f"{KEY} is not 0 or 1 under [{atmosphere.SECTION}] in ReShade.ini, so this add-on cannot tell what AtmosphereMod does; run the AtmosphereMod install again, {next_step}"


def skip_reason(now):
    kind = verdict(now)
    if kind == "absent":
        return "AtmosphereMod is not installed, so nothing else puts parameters into the root signature RenoDX needs"
    if kind == "off":
        return f"{KEY} is already 0 under [{atmosphere.SECTION}]"
    if kind == "apart":
        return apart_message(now)
    if kind == "unknown":
        return unknown_message("which sets it to 1, and then apply again")
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
    text = atmosphere.read_text(sim / atmosphere.RESHADE_INI)
    stash_original(backup, atmosphere.read_value(text, atmosphere.SECTION, KEY))
    write_ini(sim, atmosphere.replace_value(text, atmosphere.SECTION, KEY, "0"))
    print(f"{KEY}=0 is set under [{atmosphere.SECTION}] in ReShade.ini, because AtmosphereMod puts its parameters in {describe_spaces(now.spaces)}, where RenoDX puts its own; the value it had is kept for undo")
    print("the cost: AtmosphereMod's ground haze (its ps_deferredRendering pass) stays the simulator's own, because that pass needs the injected root signature; its sky and clouds keep the mod")
    print("the AtmosphereMod install sets the value back to 1, so run apply again after every run of it")
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
        return fail(f"AtmosphereMod adds parameters in {describe_spaces(now.spaces)} to the graphics root signature, where RenoDX adds its own, and RenoDX then fails to replace its shaders; run: {APPLY}")
    return fail(unknown_message(f"then: {APPLY}"))


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
