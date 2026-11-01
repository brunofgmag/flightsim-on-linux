import argparse
import ast
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from fsol import addons, exe_xml, proton_build, simconnect, watcher  # noqa: E402

ADDON_ID = "fslabs-a321"
CONTROL_CENTER = r"C:\FlightSimLabs\ControlCenter\FSLControlCenter.exe"
EFB_BROWSER = "cef_browser.exe"
WRITECOPY_SETTING = "WINE_TRACK_WRITECOPY"
GDIPLUS_PATCH = "gdiplus-path-gradient-and-centered-text.patch"
GDIPLUS_MODULE = "lib/wine/x86_64-windows/gdiplus.dll"
WIN32U_MODULE = "lib/wine/x86_64-unix/win32u.so"
NTDLL_MODULE = "lib/wine/x86_64-windows/ntdll.dll"
PATCH_GROUPS = (
    (
        GDIPLUS_MODULE,
        (
            GDIPLUS_PATCH,
            "gdiplus-nested-containers.patch",
            "gdiplus-sourcecopy-text-covers-glyphs.patch",
            "gdiplus-font-family-lookup-in-collection.patch",
            "gdiplus-premultiply-sse2.patch",
        ),
        "Proton's gdiplus.dll draws the display glow and centred text, nests drawing containers, keeps text free of black boxes, finds a font family in its collection and premultiplies pixels four at a time",
    ),
    (
        WIN32U_MODULE,
        ("win32u-larger-font-caches.patch", "win32u-banded-region-from-rects.patch", "win32u-alpha-blend-sse2.patch"),
        "Proton's win32u.so keeps 256 fonts in its caches, builds regions in one pass and blends pixels four at a time",
    ),
    (
        NTDLL_MODULE,
        ("ntdll-heap-bounded-free-list-scan.patch",),
        "Proton's ntdll.dll stops walking long heap free lists, which should end the once-a-second stalls",
    ),
)


def user_settings(proton):
    file = proton / "user_settings.py"
    if not file.is_file():
        return {}
    for node in ast.parse(file.read_text(errors="replace")).body:
        if isinstance(node, ast.Assign) and any(getattr(target, "id", "") == "user_settings" for target in node.targets):
            return ast.literal_eval(node.value)
    return {}


def writecopy_apps(proton):
    value = user_settings(proton).get(WRITECOPY_SETTING, "")
    names = value.split(":", 1)[1] if value.startswith("apps:") else value
    return {name.strip().casefold() for name in names.split(",") if name.strip()}


def writecopy_line(proton):
    if EFB_BROWSER in writecopy_apps(proton):
        return True, f"Proton tracks copy-on-write pages in {EFB_BROWSER}, so the EFB browser starts"
    return False, f"{proton / 'user_settings.py'} does not list {EFB_BROWSER} in {WRITECOPY_SETTING}; use Proton FS2024 ({proton_build.PROTON_BUILD})"


def patch_group_lines(proton, module, patches, passed):
    results = [proton_build.patch_line(proton, patch, module, passed) for patch in patches]
    failures = list(dict.fromkeys(text for good, text in results if not good))
    return [(False, text) for text in failures] or [(True, passed)]


def proton_lines(proton):
    if not proton:
        return [(False, "no Proton found; run: flightsim-on-linux machine")]
    folder = Path(proton)
    if not proton_build.build_record(folder):
        return [writecopy_line(folder), (False, f"{folder / proton_build.BUILD_RECORD} is missing or empty, so the display and heap patches can't be confirmed; run: {proton_build.PROTON_BUILD}")]
    patched = (line for module, patches, passed in PATCH_GROUPS for line in patch_group_lines(folder, module, patches, passed))
    return [writecopy_line(folder), *patched]


def watcher_on():
    return watcher.has_unit(SimpleNamespace(id=ADDON_ID), addons.config_home(os.environ))


def control_center_state(base):
    file = base / "exe.xml"
    if not file.is_file():
        return "no-file"
    return exe_xml.state(exe_xml.read_text(file), CONTROL_CENTER)


def exe_xml_line(base, watched=False):
    try:
        state = control_center_state(base)
    except (exe_xml.ExeXmlError, OSError) as error:
        return False, f"exe.xml cannot be read: {error}"
    disabled = (True, "exe.xml has the Control Center entry switched off, and the flightsim-on-linux watcher starts it with an FSLabs aircraft") if watched else (
        False, "exe.xml has the Control Center entry with Disabled true, so the simulator won't start it")
    return {
        "enabled": (True, "exe.xml starts the Control Center with the simulator"),
        "disabled": disabled,
        "absent": (False, "exe.xml has no entry for the Control Center, which writes its own; start the Control Center and check again"),
        "no-file": (False, f"exe.xml does not exist in {base}; start the Control Center and check again"),
    }[state]


def simconnect_line(base):
    port = simconnect.port(SimpleNamespace(base=base))
    if port is None:
        return False, f"{base / simconnect.FILE} has no static IPv4 port for the watcher to read the aircraft from; run: flightsim-on-linux addon apply simconnect-tcp"
    if port < simconnect.PRIVILEGED_BELOW:
        return False, f"SimConnect listens on port {port}, which Linux keeps closed to the watcher; run: flightsim-on-linux addon apply simconnect-tcp"
    return True, f"SimConnect listens on TCP port {port}, where the watcher reads the loaded aircraft"


def located(folder, what, line):
    if not folder:
        return False, f"no {what} found; run: flightsim-on-linux machine"
    return line(Path(folder))


def command_check(args):
    watched = watcher_on()
    lines = [
        *proton_lines(args.proton),
        located(args.base, "simulator base folder", lambda base: exe_xml_line(base, watched)),
    ]
    if watched:
        lines.append(located(args.base, "simulator base folder", simconnect_line))
    for good, text in lines:
        print(f"ok {text}" if good else f"FAIL {text}")
    return 0 if all(good for good, _ in lines) else 1


def command_control_center(args):
    try:
        print(control_center_state(Path(args.base)))
    except (exe_xml.ExeXmlError, OSError) as error:
        print(f"fslabs.py: exe.xml cannot be read: {error}", file=sys.stderr)
        return 2
    return 0


def parser():
    root = argparse.ArgumentParser(prog="fslabs.py")
    commands = root.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("proton")
    check.add_argument("base")
    check.set_defaults(run=command_check)
    control_center = commands.add_parser("control-center")
    control_center.add_argument("base")
    control_center.set_defaults(run=command_control_center)
    return root


if __name__ == "__main__":
    args = parser().parse_args()
    sys.exit(args.run(args))
