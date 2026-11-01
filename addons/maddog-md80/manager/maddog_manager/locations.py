import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .ini import IniFile

CLI = Path(__file__).resolve().parents[4] / "bin" / "flightsim-on-linux"
SIM_FOLDER = "MSFS 2024"
PACKAGE = "lsh-maddogx-aircraft"
AIRPLANE = "MaddogX20thNV"
MADDOG_ROOT = "Maddog X Files"


def child(base, name):
    candidate = base / name
    if candidate.exists() or not base.is_dir():
        return candidate
    lowered = name.lower()
    for entry in base.iterdir():
        if entry.name.lower() == lowered:
            return entry
    return candidate


def resolve(base, parts):
    path = Path(base)
    for part in parts:
        path = child(path, part)
    return path


def windows_to_linux(prefix, windows_path):
    drive, colon, rest = windows_path.strip().partition(":")
    if not colon or len(drive) != 1:
        raise ValueError(f"Windows path without a drive letter: {windows_path}")
    base = (Path(prefix) / "dosdevices" / f"{drive.lower()}:").resolve()
    parts = [part for part in rest.replace("/", "\\").split("\\") if part]
    return resolve(base, parts)


def machine(environ=None, run=subprocess.run):
    environ = os.environ if environ is None else environ
    found = {"prefix": environ.get("FSOL_PREFIX", ""), "community": environ.get("FSOL_COMMUNITY", "")}
    if all(found.values()):
        return found
    cli = environ.get("FSOL_CLI") or str(CLI)
    try:
        data = json.loads(run([cli, "machine", "--json"], capture_output=True, text=True, timeout=60, check=True).stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return found
    return {key: value or data.get(key) or "" for key, value in found.items()}


def find_maddog_root(prefix):
    users = Path(prefix) / "drive_c" / "users"
    candidates = sorted(users.glob(f"*/AppData/Local/{MADDOG_ROOT}"), key=lambda path: path.parts[-4] != "steamuser")
    for candidate in candidates:
        if child(candidate, SIM_FOLDER).is_dir():
            return candidate
    return None


class MaddogNotFound(Exception):
    pass


@dataclass(frozen=True)
class MaddogFiles:
    prefix: Path
    root: Path
    simulator_community: Path | None = None

    @classmethod
    def discover(cls, prefix=None, community=None, environ=None, run=subprocess.run):
        if not prefix:
            known = machine(environ, run)
            prefix, community = known["prefix"], community or known["community"]
        if not prefix:
            raise MaddogNotFound("no simulator prefix found; run: flightsim-on-linux machine")
        prefix = Path(prefix)
        root = find_maddog_root(prefix)
        if root is None:
            raise MaddogNotFound(f"the {MADDOG_ROOT} folder with {SIM_FOLDER} does not exist in {prefix}; install the Maddog first")
        files = cls(prefix, root, Path(community) if community else None)
        if files.community is None:
            raise MaddogNotFound(f"the FSPath in {files.lm_ini} is not a folder that exists, and no Community folder was given")
        return files

    @property
    def sim_dir(self):
        return child(self.root, SIM_FOLDER)

    @property
    def lm_ini(self):
        return resolve(self.sim_dir, ["Data", "LM.ini"])

    @property
    def registration_ini(self):
        return resolve(self.sim_dir, ["Data", "registration.ini"])

    @property
    def manager_ini(self):
        return child(self.root, "Manager.ini")

    @property
    def common_data(self):
        return child(self.root, "Data")

    @property
    def crew_dir(self):
        return child(self.root, "Crew")

    @property
    def navdata_dir(self):
        return child(self.root, "NavData")

    @property
    def temp_dir(self):
        return child(self.sim_dir, "Temp")

    def lm_path(self, key):
        value = IniFile(self.lm_ini).get("GENERAL", key).strip()
        if not value:
            return None
        try:
            path = windows_to_linux(self.prefix, value)
        except ValueError:
            return None
        return path if path.is_dir() else None

    @property
    def community(self):
        fallback = self.simulator_community
        return self.lm_path("FSPath") or (fallback if fallback is not None and fallback.is_dir() else None)

    @property
    def livery_base(self):
        return self.lm_path("LiveriesDebugPath") or self.community

    @property
    def package(self):
        return child(self.community, PACKAGE)

    @property
    def layout_package(self):
        return child(self.livery_base, PACKAGE)

    @property
    def livery_dir(self):
        return resolve(self.layout_package, ["SimObjects", "Airplanes", AIRPLANE, "liveries", "lsh"])

    @property
    def aircrafts_dir(self):
        return child(self.package, "Aircrafts")

    @property
    def maddogx_ini(self):
        return resolve(self.package, ["Data", "maddogx.ini"])

    @property
    def airplanes_dir(self):
        return resolve(self.package, ["SimObjects", "Airplanes"])
