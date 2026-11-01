import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ACTIONS = ("apply", "check", "undo")
WINE_UNFRIENDLY = ("NoDefaultCurrentDirectoryInExePath",)
NEEDS = ("proton-fs2024", "simulator-closed", "simulator-running")
PLACES = ("prefix", "base", "community", "sim", "data")
COMMAND = "command"
ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
FIELDS = {"name": str, "tested_version": str, "tested_on": str, "needs": list, "touches_addon_files": bool, "installed": dict}
OPTIONAL_FIELDS = {"install": dict, "shortcuts": list, "watcher": dict}
KINDS = ("installer", "store", "native", "manual")
FILE_KINDS = ("installer", "store")
STOPS = ("never", "with_simulator", "with_simulator_if_started", "on_other_aircraft")
TRIGGERS = ("simulator", "none", "always", "while_simulator")
TRIGGER_REGEXES = ("aircraft", "process")
OPTIONAL_SCRIPTS = ("install", "launch", "close")
SCRIPT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
INSTALL_FIELDS = {"from", "paid", "kind", "own_prefix"}
SHORTCUT_FIELDS = {"title", "script", "args", "comment", "icon", "close", "mime"}
WATCHER_FIELDS = {"app", "script", "args", "grace", "poll", "wait_for", "trigger", "stop", "health", "loop"}
HEALTH_FIELDS = {"listening", "connected_to", "lost_for", "restart"}


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
    install: dict | None = None
    shortcuts: list = field(default_factory=list)
    watcher: dict | None = None

    def script(self, action):
        return self.folder / action

    def has_script(self, name):
        return self.script(name).is_file() and os.access(self.script(name), os.X_OK)

    @property
    def can_install(self):
        return self.has_script("install")

    @property
    def install_needs_file(self):
        return (self.install or {}).get("kind", "installer") in FILE_KINDS


def is_relative_glob(pattern):
    return isinstance(pattern, str) and bool(pattern) and not pattern.startswith("/") and ".." not in Path(pattern).parts


def installed_problems(installed):
    problems = [f"unknown place {place!r} in installed" for place in installed if place not in PLACES and place != COMMAND]
    problems.extend(f"installed.{place} must be a list of paths" for place, value in installed.items() if not isinstance(value, list))
    patterns = [pattern for value in installed.values() if isinstance(value, list) for pattern in value]
    if not patterns:
        problems.append("installed must name at least one path")
    problems.extend(f"installed path {pattern!r} must be a relative glob inside its place" for pattern in patterns if not is_relative_glob(pattern))
    return problems


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_port(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 65536


def is_regex(value):
    if not isinstance(value, str) or not value:
        return False
    try:
        re.compile(value)
    except re.error:
        return False
    return True


def script_problems(folder, name, label):
    if not isinstance(name, str) or not SCRIPT_NAME.match(name):
        return [f"{label} {name!r} is not a plain script name"]
    if not os.access(folder / name, os.X_OK) or not (folder / name).is_file():
        return [f"{label} {name!r} is missing or not executable"]
    return []


def install_problems(install):
    problems = []
    if not isinstance(install.get("from"), str) or not install["from"]:
        problems.append("install.from must say where to get the program")
    if install.get("kind") not in KINDS:
        problems.append(f"install.kind must be one of {', '.join(KINDS)}")
    if "paid" in install and install["paid"] is not None and not isinstance(install["paid"], bool):
        problems.append("install.paid must be true, false or null")
    if "own_prefix" in install and not isinstance(install["own_prefix"], bool):
        problems.append("install.own_prefix must be true or false")
    problems.extend(f"unknown install field {name!r}" for name in sorted(set(install) - INSTALL_FIELDS))
    return problems


def icon_problems(icon, label):
    if not isinstance(icon, dict) or icon.get("place") not in PLACES or not is_relative_glob(icon.get("glob")):
        return [f"{label}.icon must be {{\"place\": one of {', '.join(PLACES)}, \"glob\": a relative glob}}"]
    return [f"unknown field {name!r} in {label}.icon" for name in sorted(set(icon) - {"place", "glob"})]


def shortcut_fields_problems(shortcut, label):
    problems = []
    if not isinstance(shortcut.get("title"), str) or not shortcut["title"].strip() or "\n" in shortcut["title"]:
        problems.append(f"{label}.title must be a one-line string")
    for name, kind in (("args", list), ("mime", list)):
        if name in shortcut and not (isinstance(shortcut[name], kind) and all(isinstance(item, str) for item in shortcut[name])):
            problems.append(f"{label}.{name} must be a list of strings")
    if "comment" in shortcut and not isinstance(shortcut["comment"], str):
        problems.append(f"{label}.comment must be a string")
    if "icon" in shortcut:
        problems.extend(icon_problems(shortcut["icon"], label))
    problems.extend(f"unknown field {name!r} in {label}" for name in sorted(set(shortcut) - SHORTCUT_FIELDS))
    return problems


def shortcut_problems(folder, shortcuts):
    problems = []
    for number, shortcut in enumerate(shortcuts):
        label = f"shortcuts[{number}]"
        if not isinstance(shortcut, dict):
            problems.append(f"{label} must be an object")
            continue
        problems.extend(shortcut_fields_problems(shortcut, label))
        problems.extend(script_problems(folder, shortcut.get("script", "launch"), f"{label}.script"))
        if shortcut.get("close") is not None:
            problems.extend(script_problems(folder, shortcut["close"], f"{label}.close"))
    return problems


def health_problems(folder, health):
    problems = [f"unknown field {name!r} in watcher.health" for name in sorted(set(health) - HEALTH_FIELDS)]
    if "listening" in health and not is_port(health["listening"]):
        problems.append("watcher.health.listening must be a port number")
    if "connected_to" in health:
        if not is_port(health["connected_to"]):
            problems.append("watcher.health.connected_to must be a port number")
        if not is_number(health.get("lost_for")) or health["lost_for"] <= 0:
            problems.append("watcher.health.lost_for must be a number of seconds")
        problems.extend(script_problems(folder, health.get("restart"), "watcher.health.restart"))
    if ("listening" in health) == ("connected_to" in health):
        problems.append("watcher.health takes either listening or connected_to")
    return problems


def trigger_problems(trigger):
    if trigger in TRIGGERS:
        return []
    if isinstance(trigger, dict) and len(trigger) == 1 and all(name in TRIGGER_REGEXES and is_regex(regex) for name, regex in trigger.items()):
        return []
    return [f"watcher.trigger must be one of {', '.join(TRIGGERS)}, {{\"aircraft\": a regex}} or {{\"process\": a regex}}"]


def watcher_option_problems(watcher):
    problems = []
    if not is_regex(watcher.get("app")):
        problems.append("watcher.app must be a regex")
    if "args" in watcher and not (isinstance(watcher["args"], list) and all(isinstance(item, str) for item in watcher["args"])):
        problems.append("watcher.args must be a list of strings")
    for name, least in (("grace", 0), ("poll", 0.1)):
        if name in watcher and not (is_number(watcher[name]) and watcher[name] >= least):
            problems.append(f"watcher.{name} must be a number of seconds, at least {least}")
    if "stop" in watcher and watcher["stop"] not in STOPS:
        problems.append(f"watcher.stop must be one of {', '.join(STOPS)}")
    if "trigger" in watcher:
        problems.extend(trigger_problems(watcher["trigger"]))
    if watcher.get("stop") == "on_other_aircraft" and not (isinstance(watcher.get("trigger"), dict) and "aircraft" in watcher["trigger"]):
        problems.append("watcher.stop on_other_aircraft needs an aircraft trigger")
    return problems


def wait_for_problems(wait_for):
    if not (isinstance(wait_for, dict) and set(wait_for) == {"process", "timeout"} and is_regex(wait_for["process"]) and is_number(wait_for["timeout"]) and wait_for["timeout"] > 0):
        return ['watcher.wait_for must be {"process": a regex, "timeout": seconds}']
    return []


def loop_problems(folder, watcher):
    others = sorted(set(watcher) - {"loop"})
    problems = [f"watcher.loop takes no other option, but {', '.join(others)} is set"] if others else []
    return problems + script_problems(folder, watcher["loop"], "watcher.loop")


def watcher_problems(folder, watcher):
    if "loop" in watcher:
        return loop_problems(folder, watcher)
    problems = watcher_option_problems(watcher)
    problems.extend(script_problems(folder, watcher.get("script", "launch"), "watcher.script"))
    if "wait_for" in watcher:
        problems.extend(wait_for_problems(watcher["wait_for"]))
    if "health" in watcher:
        problems.extend(health_problems(folder, watcher["health"]) if isinstance(watcher["health"], dict) else ["watcher.health must be an object"])
    problems.extend(f"unknown field {name!r} in watcher" for name in sorted(set(watcher) - WATCHER_FIELDS))
    return problems


def optional_problems(folder, data):
    problems = [f"{name} must be a {kind.__name__}" for name, kind in OPTIONAL_FIELDS.items() if name in data and not isinstance(data[name], kind)]
    problems.extend(f"{name} is present but not executable" for name in OPTIONAL_SCRIPTS if (folder / name).exists() and not os.access(folder / name, os.X_OK))
    if isinstance(data.get("install"), dict):
        problems.extend(install_problems(data["install"]))
    if isinstance(data.get("shortcuts"), list):
        problems.extend(shortcut_problems(folder, data["shortcuts"]))
    if isinstance(data.get("watcher"), dict):
        problems.extend(watcher_problems(folder, data["watcher"]))
    return problems


def validate(folder, data):
    problems = []
    if not ID.match(folder.name):
        problems.append("the folder name must be lower case letters, digits and dashes")
    for name, kind in FIELDS.items():
        if not isinstance(data.get(name), kind):
            problems.append(f"{name} must be a {kind.__name__}")
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
    problems.extend(optional_problems(folder, data))
    unknown = sorted(set(data) - set(FIELDS) - set(OPTIONAL_FIELDS))
    problems.extend(f"unknown field {name!r}" for name in unknown)
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
    return {"prefix": found.prefix, "base": found.base, "community": found.community, "sim": found.sim_install, "data": data_home() / "flightsim-on-linux"}[place]


def place_folders(found, place):
    folder = place_folder(found, place)
    if not folder:
        return []
    return [folder, folder.with_name("Community2024")] if place == "community" else [folder]


def is_installed(addon, found):
    for place, patterns in addon.installed.items():
        if place == COMMAND:
            if any(shutil.which(name) for name in patterns):
                return True
            continue
        for folder in place_folders(found, place):
            if folder.is_dir() and any(next(folder.glob(pattern), None) for pattern in patterns):
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


def describe(addon, found, state, shortcut=False, watcher=False):
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
        "can_install": addon.can_install,
        "shortcut": shortcut,
        "watcher": watcher,
    }


def cache_folder(environ):
    if environ.get("FSOL_CACHE"):
        return Path(environ["FSOL_CACHE"])
    return Path(environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "flightsim-on-linux"


def data_home(environ=os.environ):
    return Path(environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def config_home(environ=os.environ):
    return Path(environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def script_environment(addon, found, cli, base=os.environ, extra=None, drop=()):
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
        **(extra or {}),
    }
    env = {key: value for key, value in base.items() if key not in drop}
    env.update({key: str(value) for key, value in values.items() if value is not None})
    return env


def run_script(addon, action, found, cli, write, extra=None, drop=()):
    with subprocess.Popen([str(addon.script(action))], cwd=addon.folder, env=script_environment(addon, found, cli, extra=extra, drop=drop),
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace") as process:
        for line in process.stdout:
            write(f"  {line.rstrip()}")
    return process.returncode


def call_script(addon, action, found, cli, args=(), extra=None, drop=(), **options):
    return subprocess.run([str(addon.script(action)), *args], cwd=addon.folder, env=script_environment(addon, found, cli, extra=extra, drop=drop), **options).returncode
