import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from fsol import vdf

SIM_APPID = "2537590"
SIM_EXE = "FlightSimulator2024.exe"
STEAM_CANDIDATES = (
    ".local/share/Steam",
    ".steam/steam",
    ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    "snap/steam/common/.local/share/Steam",
)
DISPLAY_DRIVERS = ("winewayland", "winex11")
USERCFG = "drive_c/users/steamuser/AppData/Roaming/Microsoft Flight Simulator 2024/UserCfg.opt"
WINDOWS_PATH = re.compile(r"^[A-Za-z]:\\")
STEAM_INSTALL = b"\\common\\"
WINDOWS_FOLDER = "c:\\windows\\"


@dataclass
class Machine:
    steam: Path | None = None
    libraries: list = field(default_factory=list)
    sim_library: Path | None = None
    sim_install: Path | None = None
    compatdata: Path | None = None
    prefix: Path | None = None
    proton: Path | None = None
    compat_tool: str | None = None
    launch_options: dict = field(default_factory=dict)
    base: Path | None = None
    packages: Path | None = None
    community: Path | None = None
    sim_running: bool = False
    sim_pid: int | None = None
    ntsync_device: bool = False
    ntsync_handles: int | None = None
    wayland_sockets: list = field(default_factory=list)
    session_wayland_display: str | None = None
    sim_wayland_display: str | None = None
    sim_x_display: str | None = None
    sim_display_drivers: list = field(default_factory=list)
    monitors: int | None = None
    prefix_in_use: bool = False
    prefix_programs: list = field(default_factory=list)

    @property
    def proton_is_fs2024(self):
        settings = self.proton / "user_settings.py" if self.proton else None
        return bool(settings and settings.is_file() and "WINE_FENIX_HELPER_WINDOWS" in settings.read_text(errors="replace"))

    def as_dict(self):
        result = {name: getattr(self, name) for name in self.__dataclass_fields__}
        result["proton_is_fs2024"] = self.proton_is_fs2024
        return plain(result)


def plain(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def find_steam(home, override=None):
    candidates = [Path(override)] if override else [Path(home) / relative for relative in STEAM_CANDIDATES]
    for candidate in candidates:
        if (candidate / "steamapps" / "libraryfolders.vdf").is_file():
            return candidate.resolve()
    return None


def steam_libraries(steam):
    tree = vdf.load(steam / "steamapps" / "libraryfolders.vdf")
    folders = vdf.find(tree, "libraryfolders") or {}
    paths = [Path(entry["path"]) for entry in folders.values() if isinstance(entry, dict) and "path" in entry]
    unique = []
    for path in paths or [steam]:
        if path.resolve() not in [known.resolve() for known in unique]:
            unique.append(path)
    return unique


def find_sim(libraries):
    for library in libraries:
        manifest = library / "steamapps" / f"appmanifest_{SIM_APPID}.acf"
        if manifest.is_file():
            installdir = vdf.find(vdf.load(manifest), "AppState", "installdir")
            return library, library / "steamapps" / "common" / installdir if installdir else None
    return None, None


def find_compatdata(steam, sim_library):
    for library in [sim_library, steam]:
        if library and (library / "steamapps" / "compatdata" / SIM_APPID / "pfx").is_dir():
            return library / "steamapps" / "compatdata" / SIM_APPID
    return None


def proton_from_config_info(compatdata):
    config_info = compatdata / "config_info"
    if not config_info.is_file():
        return None
    for line in config_info.read_text(errors="replace").splitlines():
        if line.endswith("/files/lib/"):
            candidate = Path(line[: -len("/files/lib/")])
            return candidate if (candidate / "files" / "bin" / "wine").exists() else None
    return None


def compat_tool_name(steam):
    config = steam / "config" / "config.vdf"
    if not config.is_file():
        return None
    mapping = vdf.find(vdf.load(config), "InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping", SIM_APPID)
    return mapping.get("name") or None if isinstance(mapping, dict) else None


def launch_options(steam):
    found = {}
    for localconfig in sorted((steam / "userdata").glob("*/config/localconfig.vdf")):
        apps = vdf.find(vdf.load(localconfig), "UserLocalConfigStore", "Software", "Valve", "Steam", "apps")
        app = vdf.find(apps, SIM_APPID) if isinstance(apps, dict) else None
        if isinstance(app, dict):
            found[localconfig.parent.parent.name] = app.get("LaunchOptions", "")
    return found


def windows_to_linux(prefix, windows_path):
    match = re.match(r"^([A-Za-z]):\\?(.*)$", windows_path)
    if not match:
        return None
    letter, rest = match.group(1).lower(), match.group(2).replace("\\", "/")
    drive = prefix / "dosdevices" / f"{letter}:"
    if not drive.exists():
        return None
    return drive.resolve() / rest


def packages_folder(prefix):
    usercfg = prefix / USERCFG
    if not usercfg.is_file():
        return None
    match = re.search(r'^InstalledPackagesPath\s+"([^"]+)"', usercfg.read_text(errors="replace"), re.MULTILINE)
    return windows_to_linux(prefix, match.group(1)) if match else None


def sim_pid(proc=Path("/proc")):
    for cmdline in sorted(proc.glob("[0-9]*/cmdline"), key=lambda path: int(path.parent.name)):
        try:
            first = cmdline.read_bytes().split(b"\0", 1)[0]
        except OSError:
            continue
        lowered = first.lower()
        if lowered.endswith(SIM_EXE.lower().encode()) and first[1:3] == b":\\" and STEAM_INSTALL in lowered:
            return int(cmdline.parent.name)
    return None


def process_environ(pid_dir):
    try:
        items = (pid_dir / "environ").read_bytes().split(b"\0")
    except OSError:
        return {}
    return dict(item.decode(errors="replace").split("=", 1) for item in items if b"=" in item)


def in_prefix(pid_dir, wanted):
    winepfx = process_environ(pid_dir).get("WINEPREFIX", "")
    return bool(winepfx) and os.path.realpath(winepfx) == wanted


def prefix_wineserver(prefix, proc):
    wanted = os.path.realpath(prefix)
    for comm in proc.glob("[0-9]*/comm"):
        try:
            if comm.read_text().strip() != "wineserver":
                continue
        except OSError:
            continue
        if in_prefix(comm.parent, wanted):
            return comm.parent
    return None


def windows_program(pid_dir):
    try:
        first = (pid_dir / "cmdline").read_bytes().split(b"\0", 1)[0].decode(errors="replace")
    except OSError:
        return None
    if not WINDOWS_PATH.match(first) or first.lower().startswith(WINDOWS_FOLDER):
        return None
    return first.rsplit("\\", 1)[-1]


def prefix_programs(prefix, proc):
    wanted = os.path.realpath(prefix)
    names = []
    for pid_dir in sorted(proc.glob("[0-9]*"), key=lambda path: int(path.name)):
        name = windows_program(pid_dir)
        if name and name not in names and in_prefix(pid_dir, wanted):
            names.append(name)
    return names


def ntsync_handles(pid_dir):
    count = 0
    for fd in (pid_dir / "fd").glob("*"):
        try:
            if os.readlink(fd) == "/dev/ntsync":
                count += 1
        except OSError:
            continue
    return count


def wayland_sockets(runtime_dir):
    return sorted(entry.name for entry in Path(runtime_dir).glob("wayland-*") if entry.is_socket())


def display_drivers(pid_dir):
    try:
        mapped = (pid_dir / "maps").read_text(errors="replace")
    except OSError:
        return []
    return [driver for driver in DISPLAY_DRIVERS if f"{driver}.drv" in mapped]


def connector_reads(connector, name):
    try:
        return (connector / name).read_text().strip()
    except OSError:
        return None


def monitor_is_on(connector):
    return connector_reads(connector, "status") == "connected" and connector_reads(connector, "enabled") == "enabled"


def connected_monitors(drm):
    drm = Path(drm)
    if not drm.is_dir():
        return None
    return sum(1 for connector in drm.glob("card*-*") if monitor_is_on(connector))


def discover(home=None, steam_override=None, proc=Path("/proc"), dev=Path("/dev"), runtime_dir=None, drm=Path("/sys/class/drm"), env=None):
    env = os.environ if env is None else env
    machine = Machine()
    machine.steam = find_steam(home or Path.home(), steam_override or env.get("STEAM_DIR"))
    if not machine.steam:
        return machine
    machine.libraries = steam_libraries(machine.steam)
    machine.sim_library, machine.sim_install = find_sim(machine.libraries)
    machine.compatdata = find_compatdata(machine.steam, machine.sim_library)
    if machine.compatdata:
        machine.prefix = machine.compatdata / "pfx"
        machine.proton = proton_from_config_info(machine.compatdata)
        machine.base = (machine.prefix / USERCFG).parent if (machine.prefix / USERCFG).is_file() else None
        machine.packages = packages_folder(machine.prefix)
        machine.community = machine.packages / "Community" if machine.packages else None
    machine.compat_tool = compat_tool_name(machine.steam)
    machine.launch_options = launch_options(machine.steam)
    machine.sim_pid = sim_pid(proc)
    machine.sim_running = machine.sim_pid is not None
    machine.ntsync_device = (dev / "ntsync").exists()
    server = prefix_wineserver(machine.prefix, proc) if machine.prefix else None
    machine.prefix_in_use = server is not None
    machine.prefix_programs = prefix_programs(machine.prefix, proc) if machine.prefix else []
    machine.ntsync_handles = ntsync_handles(server) if server and machine.sim_running else None
    machine.wayland_sockets = wayland_sockets(runtime_dir or env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    machine.session_wayland_display = env.get("WAYLAND_DISPLAY")
    if machine.sim_running:
        sim_dir = proc / str(machine.sim_pid)
        sim_env = process_environ(sim_dir)
        machine.sim_wayland_display = sim_env.get("WAYLAND_DISPLAY")
        machine.sim_x_display = sim_env.get("DISPLAY")
        machine.sim_display_drivers = display_drivers(sim_dir)
    machine.monitors = connected_monitors(drm)
    return machine
