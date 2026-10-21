import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from fsol import exe_xml, proton_build  # noqa: E402

CONTROL_CENTER = r"C:\FlightSimLabs\ControlCenter\FSLControlCenter.exe"
EFB_BROWSER = "cef_browser.exe"
WRITECOPY_SETTING = "WINE_TRACK_WRITECOPY"
GDIPLUS_PATCH = "gdiplus-path-gradient-and-centered-text.patch"
GDIPLUS_MODULE = "lib/wine/x86_64-windows/gdiplus.dll"


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


def gdiplus_line(proton):
    return proton_build.patch_line(proton, GDIPLUS_PATCH, GDIPLUS_MODULE, "Proton's gdiplus.dll draws the display glow and centred text")


def control_center_state(base):
    file = base / "exe.xml"
    if not file.is_file():
        return "no-file"
    return exe_xml.state(exe_xml.read_text(file), CONTROL_CENTER)


def exe_xml_line(base):
    try:
        state = control_center_state(base)
    except (exe_xml.ExeXmlError, OSError) as error:
        return False, f"exe.xml cannot be read: {error}"
    return {
        "enabled": (True, "exe.xml starts the Control Center with the simulator"),
        "disabled": (False, "exe.xml has the Control Center entry with Disabled true, so the simulator won't start it"),
        "absent": (False, "exe.xml has no entry for the Control Center, which writes its own; start the Control Center and check again"),
        "no-file": (False, f"exe.xml does not exist in {base}; start the Control Center and check again"),
    }[state]


def located(folder, what, line):
    if not folder:
        return False, f"no {what} found; run: flightsim-on-linux machine"
    return line(Path(folder))


def command_check(args):
    lines = [
        located(args.proton, "Proton", writecopy_line),
        located(args.proton, "Proton", gdiplus_line),
        located(args.base, "simulator base folder", exe_xml_line),
    ]
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
