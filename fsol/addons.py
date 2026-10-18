import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ACTIONS = ("apply", "check", "undo")
NEEDS = ("proton-fs2024", "simulator-closed", "simulator-running")
PLACES = ("prefix", "base", "community", "sim")
ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
FIELDS = {"name": str, "tested_version": str, "tested_on": str, "needs": list, "touches_addon_files": bool, "installed": dict}


class AddonError(Exception):
    pass


@dataclass
class Addon:
    id: str
    folder: Path
    name: str
    tested_version: str
    tested_on: str
    needs: list
    touches_addon_files: bool
    installed: dict

    def script(self, action):
        return self.folder / action


def is_relative_glob(pattern):
    return isinstance(pattern, str) and bool(pattern) and not pattern.startswith("/") and ".." not in Path(pattern).parts


def installed_problems(installed):
    problems = [f"unknown place {place!r} in installed" for place in installed if place not in PLACES]
    problems.extend(f"installed.{place} must be a list of paths" for place, value in installed.items() if not isinstance(value, list))
    patterns = [pattern for value in installed.values() if isinstance(value, list) for pattern in value]
    if not patterns:
        problems.append("installed must name at least one path")
    problems.extend(f"installed path {pattern!r} must be a relative glob inside its place" for pattern in patterns if not is_relative_glob(pattern))
    return problems


def validate(folder, data):
    problems = []
    if not ID.match(folder.name):
        problems.append("the folder name must be lower case letters, digits and dashes")
    for field, kind in FIELDS.items():
        if not isinstance(data.get(field), kind):
            problems.append(f"{field} must be a {kind.__name__}")
    if isinstance(data.get("tested_on"), str):
        try:
            date.fromisoformat(data["tested_on"])
        except ValueError:
            problems.append("tested_on must be a date like 2026-10-17")
    if isinstance(data.get("needs"), list):
        problems.extend(f"unknown need {need!r}" for need in data["needs"] if need not in NEEDS)
    if isinstance(data.get("installed"), dict):
        problems.extend(installed_problems(data["installed"]))
    for action in ACTIONS:
        if not os.access(folder / action, os.X_OK):
            problems.append(f"{action} is missing or not executable")
    unknown = sorted(set(data) - set(FIELDS))
    problems.extend(f"unknown field {field!r}" for field in unknown)
    return problems


def load(folder):
    try:
        data = json.loads((folder / "addon.json").read_text())
    except (OSError, ValueError) as error:
        raise AddonError(f"{folder.name}: cannot read addon.json: {error}")
    if not isinstance(data, dict):
        raise AddonError(f"{folder.name}: addon.json must hold an object")
    problems = validate(folder, data)
    if problems:
        raise AddonError(f"{folder.name}: " + "; ".join(problems))
    return Addon(id=folder.name, folder=folder, **data)


def load_all(root):
    if not root.is_dir():
        return []
    return sorted((load(folder) for folder in root.iterdir() if (folder / "addon.json").is_file()), key=lambda addon: addon.name.lower())


def find(root, wanted):
    addon = next((addon for addon in load_all(root) if addon.id == wanted), None)
    if not addon:
        raise AddonError(f"no add-on called {wanted!r}; run: flightsim-on-linux addon list")
    return addon


def place_folder(found, place):
    return {"prefix": found.prefix, "base": found.base, "community": found.community, "sim": found.sim_install}[place]


def is_installed(addon, found):
    for place, patterns in addon.installed.items():
        folder = place_folder(found, place)
        if folder and folder.is_dir() and any(next(folder.glob(pattern), None) for pattern in patterns):
            return True
    return False


def unmet_needs(addon, found):
    unmet = []
    if "proton-fs2024" in addon.needs and not found.proton_is_fs2024:
        unmet.append("it needs Proton FS2024 selected for the simulator (flightsim-on-linux proton build)")
    if "simulator-closed" in addon.needs and found.sim_running:
        unmet.append("close the simulator and every program of its prefix first")
    if "simulator-running" in addon.needs and not found.sim_running:
        unmet.append("start the simulator first")
    return unmet


def state_folder(environ=os.environ):
    base = Path(environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "flightsim-on-linux" / "addons"


def remember(addon, state):
    state.mkdir(parents=True, exist_ok=True)
    (state / f"{addon.id}.json").write_text(json.dumps({"fixed_on": date.today().isoformat(), "tested_version": addon.tested_version}) + "\n")


def forget(addon, state):
    (state / f"{addon.id}.json").unlink(missing_ok=True)


def fixed_on(addon, state):
    try:
        return json.loads((state / f"{addon.id}.json").read_text()).get("fixed_on")
    except (OSError, ValueError):
        return None


def status(addon, found, state):
    if not is_installed(addon, found):
        return "not installed"
    when = fixed_on(addon, state)
    return f"fixed on {when}" if when else "installed, not fixed"


def describe(addon, found, state):
    return {
        "id": addon.id,
        "name": addon.name,
        "status": status(addon, found, state),
        "installed": is_installed(addon, found),
        "fixed_on": fixed_on(addon, state),
        "tested_version": addon.tested_version,
        "tested_on": addon.tested_on,
        "needs": addon.needs,
        "touches_addon_files": addon.touches_addon_files,
    }


def cache_folder(environ):
    if environ.get("FSOL_CACHE"):
        return Path(environ["FSOL_CACHE"])
    return Path(environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "flightsim-on-linux"


def script_environment(addon, found, cli, base=os.environ):
    values = {
        "FSOL_ADDON": addon.id,
        "FSOL_ADDON_DIR": addon.folder,
        "FSOL_CLI": cli,
        "FSOL_STEAM": found.steam,
        "FSOL_SIM_INSTALL": found.sim_install,
        "FSOL_COMPATDATA": found.compatdata,
        "FSOL_PREFIX": found.prefix,
        "FSOL_PROTON": found.proton,
        "FSOL_BASE": found.base,
        "FSOL_PACKAGES": found.packages,
        "FSOL_COMMUNITY": found.community,
        "FSOL_SIM_RUNNING": "1" if found.sim_running else "0",
        "FSOL_CACHE": cache_folder(base),
    }
    env = dict(base)
    env.update({key: str(value) for key, value in values.items() if value is not None})
    return env


def run_script(addon, action, found, cli, write):
    with subprocess.Popen([str(addon.script(action))], cwd=addon.folder, env=script_environment(addon, found, cli),
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace") as process:
        for line in process.stdout:
            write(f"  {line.rstrip()}")
    return process.returncode
