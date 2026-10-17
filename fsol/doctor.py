import re
import shutil
from dataclasses import dataclass

BUILD_TOOLS = ("git", "curl", "patch", "perl", "autoreconf", "make", "gcc", "flex", "bison", "x86_64-w64-mingw32-gcc")


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""

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
    if machine.proton_is_fenix:
        return Check("proton", "ok", f"Proton in use: {name} ({machine.proton}), a Proton Fenix A320 build")
    where = f" ({machine.proton})" if machine.proton else ""
    return Check("proton", "warn", f"Proton in use: {name}{where}, not a Proton Fenix A320 build",
                 "The Fenix A320, the FSLabs displays and the AutoFPS tray icon need Proton Fenix A320. Build it with: flightsim-on-linux proton build, then pick it under Compatibility > Select compatibility tool in the simulator's Properties.")


NTSYNC_REMEDY = ("If the simulator freezes in the menu or while loading an aircraft, turn ntsync off first: put "
                 "PROTON_NO_NTSYNC=1 before %command% under Launch Options in the simulator's Properties in Steam. "
                 "PROTON_USE_NTSYNC=0 does nothing here. The fenix-a320-linux-patch overlay was only verified with ntsync off.")


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
    if not missing:
        return Check("build-tools", "ok", "Every program needed to build Proton Fenix A320 is installed")
    return Check("build-tools", "info", f"Missing for building Proton Fenix A320: {', '.join(missing)}", "Install them with your distribution's package manager before running the build.")


CHECKS = (check_steam, check_sim, check_prefix, check_proton, check_ntsync, check_library_paths, check_community, check_running, check_build_tools)


def run(machine):
    if not machine.steam:
        return [check_steam(machine)]
    return [check(machine) for check in CHECKS]
