import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from fsol import registry

BUILD_TOOLS = ("git", "curl", "patch", "perl", "autoreconf", "make", "gcc", "flex", "bison", "x86_64-w64-mingw32-gcc",
               "x86_64-w64-mingw32-g++", "meson", "ninja", "glslangValidator")
IDL_COMPILERS = ("widl", "widl-stable", "x86_64-w64-mingw32-widl")
CLI = Path(__file__).resolve().parent.parent / "bin" / "flightsim-on-linux"
SIMULATOR_CLOSED = "Run this with the simulator closed:"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""
    command: str = ""

    def as_dict(self):
        return dict(self.__dict__)


def check_steam(machine):
    if machine.steam:
        return Check("steam", "ok", f"Steam found in {machine.steam}")
    return Check("steam", "fail", "Steam was not found", "Install Steam, or set STEAM_DIR to the folder that holds steamapps/libraryfolders.vdf.")


def check_sim(machine):
    if machine.sim_install:
        return Check("simulator", "ok", f"Microsoft Flight Simulator 2024 installed in {machine.sim_install}")
    return Check("simulator", "fail", "Microsoft Flight Simulator 2024 (Steam app 2537590) is not installed in any Steam library", "Install it from Steam. The Microsoft Store and Xbox app versions are not covered here.")


def check_prefix(machine):
    if machine.prefix:
        return Check("prefix", "ok", f"Proton prefix in {machine.prefix}")
    return Check("prefix", "fail", "The simulator has no Proton prefix yet", "Start the simulator once from Steam and quit it. Proton creates the prefix on the first start.")


def check_proton(machine):
    name = machine.compat_tool or "Steam default"
    if machine.proton_is_fs2024:
        return Check("proton", "ok", f"Proton in use: {name} ({machine.proton}), a Proton FS2024 build")
    where = f" ({machine.proton})" if machine.proton else ""
    return Check("proton", "warn", f"Proton in use: {name}{where}, not a Proton FS2024 build",
                 "The Fenix A320, the FSLabs displays and the AutoFPS tray icon need Proton FS2024. Build it with the command below, then pick it under Compatibility > Select compatibility tool in the simulator's Properties.",
                 f"{shlex.quote(str(CLI))} proton build")


NTSYNC_REMEDY = ("If the simulator freezes in the menu or while loading an aircraft, turn ntsync off first: put "
                 "PROTON_NO_NTSYNC=1 before %command% under Launch Options in the simulator's Properties in Steam. "
                 "PROTON_USE_NTSYNC=0 does nothing here. The Fenix A320 has flown with ntsync on.")


def ntsync_off_by_options(machine):
    return bool(machine.launch_options) and all(re.search(r"\bPROTON_NO_NTSYNC=0*[1-9]", options) for options in machine.launch_options.values())


def check_ntsync(machine):
    if machine.ntsync_handles is not None:
        if machine.ntsync_handles:
            return Check("ntsync", "info", f"ntsync is on in the running simulator: its wineserver holds {machine.ntsync_handles} /dev/ntsync handle{'s' if machine.ntsync_handles > 1 else ''}", NTSYNC_REMEDY)
        return Check("ntsync", "info", "ntsync is off in the running simulator: its wineserver holds no /dev/ntsync handle")
    if not machine.ntsync_device:
        return Check("ntsync", "info", "This kernel has no /dev/ntsync, so Proton synchronizes another way")
    if ntsync_off_by_options(machine):
        return Check("ntsync", "info", "ntsync is off: the launch options set PROTON_NO_NTSYNC=1")
    return Check("ntsync", "info", "ntsync will be on when the simulator starts", NTSYNC_REMEDY)


SCREEN_SECTION = 'See "Your screen setup changes what works" in the ground rules (site/pages/rules.html).'
SCREEN_RULES = f"Add-on windows, tray icons and WebView2 behave differently under each driver. {SCREEN_SECTION}"
MONITOR_RULES = f"The simulator reads the screen once, when it starts: start it on the monitor you play on. {SCREEN_SECTION}"
WAYLAND_DRIVER = "Wine's Wayland driver (winewayland.drv)"
X11_DRIVER = "Wine's X11 driver (winex11.drv)"


def check_wayland(machine):
    if not machine.wayland_sockets:
        warns = wayland_in_options(machine)
        fix = "Remove PROTON_ENABLE_WAYLAND=1 from the launch options, or log in to a Wayland session." if warns else ""
        return Check("wayland", "warn" if warns else "info", "This session has no Wayland socket, so Wine's Wayland driver can't start", fix)
    detail = f"Wayland sockets in this session: {', '.join(machine.wayland_sockets)}. This shell uses {machine.session_wayland_display or 'none'}"
    if machine.sim_running:
        detail += f", the simulator {machine.sim_wayland_display or 'none'}"
    return Check("wayland", "info", detail)


def running_driver_detail(drivers):
    if not drivers:
        return "The simulator has not loaded a Wine display driver yet"
    if len(drivers) > 1:
        return f"The simulator has loaded both Wine display drivers: {WAYLAND_DRIVER} and {X11_DRIVER}"
    return f"The simulator draws through {WAYLAND_DRIVER if drivers == ['winewayland'] else X11_DRIVER}"


def check_graphics_driver(machine):
    if machine.sim_running:
        detail = running_driver_detail(machine.sim_display_drivers)
        return Check("graphics-driver", "info", detail, SCREEN_RULES if machine.sim_display_drivers else "")
    if wayland_in_options(machine):
        return Check("graphics-driver", "info", "The simulator will start with Wine's Wayland driver: the launch options set PROTON_ENABLE_WAYLAND=1", SCREEN_RULES)
    return Check("graphics-driver", "info", "The simulator will start with Wine's X11 driver, through XWayland in a Wayland session", SCREEN_RULES)


def check_monitors(machine):
    if machine.monitors is None:
        return None
    detail = "1 monitor is on" if machine.monitors == 1 else f"{machine.monitors} monitors are on"
    return Check("monitors", "info", detail, MONITOR_RULES if machine.monitors > 1 else "")


def check_library_paths(machine):
    if any("STEAM_COMPAT_LIBRARY_PATHS=" in options for options in machine.launch_options.values()):
        return Check("library-paths", "ok", "Launch options set STEAM_COMPAT_LIBRARY_PATHS")
    return Check("library-paths", "info", "Launch options do not set STEAM_COMPAT_LIBRARY_PATHS",
                 "Only the ChasePlane bridge needs it: add STEAM_COMPAT_LIBRARY_PATHS=<the Steam library folder that holds the simulator, the one with steamapps inside> before %command%.")


def check_community(machine):
    if machine.community and machine.community.is_dir():
        return Check("community", "ok", f"Community folder: {machine.community}")
    if machine.packages:
        return Check("community", "warn", f"Packages folder {machine.packages} has no Community folder yet", "Start the simulator once; it creates the folder.")
    return Check("community", "warn", "The simulator's UserCfg.opt was not found, so the Community folder is unknown", "Start the simulator once and finish the first setup.")


def check_running(machine):
    if machine.sim_running:
        return Check("running", "info", "The simulator is running", "Installers must run with the simulator and every other program of the prefix closed.")
    return Check("running", "ok", "The simulator is not running")


def check_build_tools(machine):
    missing = [tool for tool in BUILD_TOOLS if not shutil.which(tool)]
    if not any(shutil.which(tool) for tool in IDL_COMPILERS):
        missing.append(f"{', '.join(IDL_COMPILERS[:-1])} or {IDL_COMPILERS[-1]}")
    if not missing:
        return Check("build-tools", "ok", "Every program needed to build Proton FS2024 is installed")
    return Check("build-tools", "info", f"Missing for building Proton FS2024: {', '.join(missing)}", "Install them with your distribution's package manager before running the build.")


WPF_KEY = r"Software\Microsoft\Avalon.Graphics"
WPF_APPS = "Lorby AAO, AutoFPS, vRAAS, the FSLabs Control Center and the Fenix runner"
WPF_COMMAND = "protontricks -c 'wine reg add \"HKCU\\Software\\Microsoft\\Avalon.Graphics\" /v DisableHWAcceleration /t REG_DWORD /d 1 /f' 2537590"
FONTS_KEY = r"Software\Wine\Fonts\Replacements"
FONT_REPLACEMENTS = (
    ("Segoe UI", "Noto Sans"),
    ("Segoe UI Bold", "Noto Sans"),
    ("Segoe UI Semibold", "Noto Sans Medium"),
    ("Consolas", "DejaVu Sans Mono"),
    ("Verdana", "DejaVu Sans"),
    ("Palatino Linotype", "Noto Serif"),
)
FALLBACK_FONT = "times new roman"
SYSTEM_DISABLED = re.compile(r'<Package name="community[^"]*" active="SystemDisabled"')
REACTIVATE = "s/(<Package name=\"community[^\"]*\" active=\")SystemDisabled(\")/\\1Activated\\2/"


def user_registry(machine):
    path = machine.prefix / "user.reg" if machine.prefix else None
    return registry.load(path) if path and path.is_file() else None


def wayland_in_options(machine):
    return any(re.search(r"\bPROTON_ENABLE_WAYLAND=0*[1-9]", options) for options in machine.launch_options.values())


def check_wpf(machine):
    keys = user_registry(machine)
    if keys is None:
        return None
    if registry.value(keys, WPF_KEY, "DisableHWAcceleration") == 1:
        return Check("wpf", "ok", "WPF programs in the prefix draw in software (Avalon.Graphics DisableHWAcceleration is 1)")
    status = "warn" if wayland_in_options(machine) else "info"
    return Check("wpf", status, "WPF programs in the prefix draw with Direct3D 9: HKCU\\Software\\Microsoft\\Avalon.Graphics has no DisableHWAcceleration=1",
                 f"Under Wine's Wayland driver, {WPF_APPS} crash when their window opens. {SIMULATOR_CLOSED}", WPF_COMMAND)


def installed_font_families():
    if not shutil.which("fc-list"):
        return None
    listing = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True).stdout
    return {name.strip().lower() for line in listing.splitlines() for name in line.split(",") if name.strip()}


def font_problem(current, families):
    if not current:
        return "has no replacement"
    if current.lower() == FALLBACK_FONT:
        return "is replaced by Times New Roman"
    if families is not None and current.lower() not in families:
        return f"is replaced by {current}, which this system doesn't have"
    return None


def font_command(fixes):
    adds = "".join(f' &&\n  wine reg add "$key" /v "{windows}" /d "{target}" /f' for windows, target in fixes)
    return f"protontricks -c 'key=\"HKCU\\{FONTS_KEY}\"{adds}' 2537590"


def check_fonts(machine):
    keys = user_registry(machine)
    if keys is None:
        return None
    families = installed_font_families()
    problems = []
    fixes = []
    for windows, target in FONT_REPLACEMENTS:
        problem = font_problem(registry.value(keys, FONTS_KEY, windows), families)
        if problem:
            problems.append(f"{windows} {problem}")
            fixes.append((windows, target))
    if not problems:
        return Check("fonts", "ok", f"The {len(FONT_REPLACEMENTS)} Windows fonts add-ons ask for most have a replacement this system has")
    missing = sorted({target for _, target in fixes if families is not None and target.lower() not in families})
    install = f"Install {', '.join(missing)} first. " if missing else ""
    return Check("fonts", "warn", "Text in add-on windows can come out thin and jagged: " + "; ".join(problems),
                 f"{install}{SIMULATOR_CLOSED}", font_command(fixes))


def content_files(machine):
    return sorted(machine.base.glob("*/Content.xml")) if machine.base else []


def check_community_disabled(machine):
    files = content_files(machine)
    if not files:
        return None
    disabled = {path: len(SYSTEM_DISABLED.findall(path.read_text(errors="replace"))) for path in files}
    total = sum(disabled.values())
    if not total:
        return Check("community-disabled", "ok", "The simulator has not switched off any Community package")
    paths = " ".join(shlex.quote(str(path)) for path, count in disabled.items() if count)
    return Check("community-disabled", "warn", f"The simulator switched off {total} Community package{'s' if total > 1 else ''} (SystemDisabled in Content.xml)",
                 f"It does this when it is killed while loading. {SIMULATOR_CLOSED}", f"sed -i -E {shlex.quote(REACTIVATE)} {paths}")


CHECKS = (check_steam, check_sim, check_prefix, check_proton, check_ntsync, check_wayland, check_graphics_driver, check_monitors, check_library_paths, check_community, check_community_disabled, check_wpf, check_fonts, check_running, check_build_tools)


def run(machine):
    if not machine.steam:
        return [check_steam(machine)]
    return [found for found in (check(machine) for check in CHECKS) if found]
