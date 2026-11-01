import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .ini import IniFile
from .locations import child

VERSION_URL = "https://www.leonardosh.it/download/MDX/MDXVersion.asp"


@dataclass(frozen=True)
class Toggle:
    title: str
    section: str
    key: str
    invert: bool = False

    def read(self, ini):
        return ini.get_bool(self.section, self.key) != self.invert

    def write(self, ini, checked):
        ini.set_bool(self.section, self.key, checked != self.invert)


@dataclass(frozen=True)
class Slider:
    title: str
    section: str
    key: str
    minimum: int = 0
    maximum: int = 100
    offset: int = 0
    default: int = 0
    marks_change: bool = False

    def read(self, ini):
        return ini.get_int(self.section, self.key, self.default) - self.offset

    def write(self, ini, value):
        ini.set_int(self.section, self.key, value + self.offset)


TOGGLES = [
    Toggle("Realistic mode (turns on failures, maintenance and random variations)", "FAILURES", "DisableAllFailures", invert=True),
    Toggle("The autopilot disconnects when the flight controls are moved by hand", "AUTOPILOT", "APDisconnects"),
    Toggle("Update weight, center of gravity and flight number by itself", "SYSTEM", "AutoZfwCg"),
    Toggle("Aircraft fueled", "SYSTEM", "AutoFuel"),
    Toggle("Mouse wheel acceleration", "SYSTEM", "InputAccelleration"),
    Toggle("Sync the CM1 and CM2 settings", "SYSTEM", "SyncBaro"),
    Toggle("Sync the services with GSX Pro", "SYSTEM", "gsxSync"),
    Toggle("Turnaround mode", "SYSTEM", "TurnaroundMode"),
    Toggle("ACARS prints and reads by itself", "SYSTEM", "ACARSAutoPrint"),
    Toggle("Filter the CLAMP mode on takeoff", "SYSTEM", "FilterCLAMPinTO"),
    Toggle("Pause at TOD or on a failure", "SYSTEM", "PauseAtTOD"),
    Toggle("Realistic nose wheel steering, ±17°, with the pedals", "SYSTEM", "RealisticNoseSteering"),
]

LIGHTS = [
    Slider("Flood lights", "SYSTEM", "FloodLights", marks_change=True),
    Slider("Panel integral lights", "SYSTEM", "PanelLights", marks_change=True),
    Slider("Annunciators", "SYSTEM", "AnnLights", marks_change=True),
    Slider("CRT displays", "SYSTEM", "CRTMaxLum", marks_change=True),
    Slider("LED displays", "SYSTEM", "LEDMaxLum", marks_change=True),
    Slider("LCD displays", "SYSTEM", "LCDMaxLum", marks_change=True),
    Slider("Engine displays", "SYSTEM", "EdpSdpMaxLum", marks_change=True),
]

BOARDING_MUSIC = Slider("Boarding music", "SOUND", "VolBGMusic", default=60, marks_change=True)
CREW_VOLUME = Slider("Crew volume", "SOUND", "VolCrew", default=80, marks_change=True)
PM_VOLUME = Slider("PM callouts volume", "SOUND", "VolPNF", default=100, marks_change=True)

SOUNDS = [
    Slider("Cockpit ambience", "SOUND", "VolCockpit", default=100, marks_change=True),
    Slider("Warnings", "SOUND", "VolWarnings", default=100, marks_change=True),
    Slider("Air conditioning airflow", "SOUND", "VolAC", default=100, marks_change=True),
    Slider("Radio rack fan", "SOUND", "VolRadioRack", default=100, marks_change=True),
    Slider("Standby horizon vibration", "SOUND", "VolStbyHor", default=100, marks_change=True),
    Slider("Autothrottle", "SOUND", "VolAT", default=100, marks_change=True),
    Slider("Switches and buttons", "SOUND", "VolSwitches", default=100, marks_change=True),
    Slider("APU (inside)", "SOUND", "VolAPU", default=100, marks_change=True),
    Slider("Hydraulic pump (inside)", "SOUND", "VolHYD", default=100, marks_change=True),
    Slider("Engines (inside)", "SOUND", "VolEngineCockpit", default=10, marks_change=True),
    Slider("Passenger cabin", "SOUND", "VolCabin", default=100, marks_change=True),
]

AXES = [
    ("Elevator", [Slider("Response curve", "SYSTEM", "ClbElevator"), Slider("Scale", "SYSTEM", "SclElevator")]),
    ("Ailerons", [Slider("Response curve", "SYSTEM", "ClbAilerons"), Slider("Scale", "SYSTEM", "SclAilerons")]),
    ("Rudder", [Slider("Response curve", "SYSTEM", "ClbRudder"), Slider("Scale", "SYSTEM", "SclRudder")]),
    ("Throttles", [Slider("Response curve", "SYSTEM", "ClbThrottle", minimum=-50, maximum=50, offset=50)]),
]

WINDOW_SHADES = ["Automatic", "All up", "Mixed"]
DATA_PROVIDERS = ["None", "VATSIM", "IVAO", "SayIntentions.AI", "BeyondATC"]
SAYINTENTIONS = 3
NO_LOGON = 4
PM_VOICES = ["Male", "Female"]
PANEL_STATES = ["Do not change", "Cold and dark (closed)", "Cold and dark (open)", "Ready at gate", "Ready for takeoff"]
PANEL_STATE_NAMES = ["DO NOT CHANGE", "Cold and dark (closed)", "Cold and dark (open)", "Ready at gate", "Ready to Takeoff"]
PANEL_STATE_FILES = [None, "cold and dark.mdx", "cold and dark open.mdx", "ready at gate.mdx", "ready for takeoff.mdx"]


def mark_changed(files):
    if files.manager_ini.is_file():
        ini = IniFile(files.manager_ini)
        ini.set_int("MANAGER", "Changed", 1)
        ini.save()


def write_option(files, path, option, value):
    ini = IniFile(path)
    option.write(ini, value)
    ini.save()
    if getattr(option, "marks_change", False):
        mark_changed(files)


def cpdlc_file(files, provider):
    if provider == SAYINTENTIONS:
        return child(files.common_data, "sayintentionsai.ini"), "SAYINTENTIONSAI"
    return child(files.common_data, "hoppie.ini"), "HOPPIE"


def cpdlc_settings(files, provider):
    path, section = cpdlc_file(files, provider)
    ini = IniFile(path)
    show_logon = provider != NO_LOGON and path.is_file()
    return {
        "show_logon": show_logon,
        "logon": ini.get(section, "LogonCode") if show_logon else "",
        "enable": ini.get_bool(section, "Enable"),
        "white_list": ini.get_bool(section, "AutoWhiteList"),
    }


def write_cpdlc(files, provider, key, value):
    path, section = cpdlc_file(files, provider)
    if not files.common_data.is_dir():
        return
    ini = IniFile(path)
    if isinstance(value, bool):
        ini.set_bool(section, key, value)
    else:
        ini.set(section, key, value)
    ini.save()


def write_common(files, name, section, key, value):
    if files.common_data.is_dir():
        ini = IniFile(child(files.common_data, name))
        ini.set(section, key, value)
        ini.save()


def crew_sets(files):
    folder = files.crew_dir
    if not folder.is_dir():
        return []
    return sorted(path.stem for path in folder.iterdir() if path.suffix.lower() == ".pck")


def export_settings(files, target):
    source = IniFile(files.maddogx_ini)
    out = IniFile(target)
    for option in [*TOGGLES, *LIGHTS]:
        option.write(out, option.read(source))
    out.save()


def import_settings(files, source_path):
    source = IniFile(source_path)
    target = IniFile(files.maddogx_ini)
    for option in [*TOGGLES, *LIGHTS]:
        option.write(target, option.read(source))
    target.save()


def apply_panel_state(files, index):
    if index <= 0:
        return
    data = child(files.package, "Data")
    source = child(data, PANEL_STATE_FILES[index])
    if source.is_file():
        shutil.copyfile(source, child(data, "maddogx.mdx"))
    if files.manager_ini.is_file():
        ini = IniFile(files.manager_ini)
        ini.set_int("MANAGER", "PanelState", index)
        ini.set("MANAGER", "PanelStateName", PANEL_STATE_NAMES[index])
        ini.save()
    mark_changed(files)


def installed_build(files):
    version = IniFile(files.lm_ini).get("GENERAL", "Version")
    match = re.search(r"b(\d+)", version)
    return version, int(match.group(1)) if match else None


def server_build(text, tag="MDX24BUILD"):
    match = re.search(rf"\[{tag}=(\d+)\]", text)
    return int(match.group(1)) if match else None


def airac_info(files):
    work = files.lm_path("WorkPath")
    if work is None:
        return None
    path = Path(work) / "Navigraph" / "Navdata" / "cycle_info.txt"
    if not path.is_file():
        return None
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    info = {}
    patterns = {
        "cycle": r"^AIRAC\s+cycle\s*:\s*(\d+)\s*$",
        "version": r"^Version\s*:\s*(.+)$",
        "valid": r"^Valid\s*\(from/to\)\s*:\s*(.+)$",
    }
    for line in text.splitlines()[:80]:
        line = line.rstrip()
        for name, pattern in patterns.items():
            match = re.match(pattern, line, re.IGNORECASE)
            if match and name not in info:
                info[name] = match.group(1).strip()
    return info
