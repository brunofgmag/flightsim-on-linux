import re
import sys
from pathlib import Path

PARTS = Path(__file__).resolve().parent
ROOT = PARTS.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PARTS))

import clr  # noqa: E402
import couatl_entry  # noqa: E402
import qlm_reg  # noqa: E402
from fsol import proton_build, registry  # noqa: E402

ADDON_MANAGER = "drive_c/Program Files (x86)/Addon Manager"
MANIFEST = f"{ADDON_MANAGER}/QlmLicenseLib.dll.manifest"
MONO = "drive_c/windows/mono/mono-2.0"
MSCORLIB = f"{MONO}/lib/mono/4.5/mscorlib.dll"
SYSTEM = f"{MONO}/lib/mono/gac/System/4.0.0.0__b77a5c561934e089/System.dll"
UNPATCHED_MARKER = "lib/mono/4.5/System.Core.dll"
PATCHED_METHODS = ((MSCORLIB, "register-assembly"), (SYSTEM, "process-responding"))
COPY_RECORD = "flightsim-on-linux-backup/mono-copy/created"
REGASM = (("Framework", "i386-windows"), ("Framework64", "x86_64-windows"))
CURL_FOLDERS = ("system32", "syswow64")
WEBVIEW2_OPTIONS = "--in-process-gpu --disable-gpu --disable-gpu-compositing --disable-software-rasterizer"
CHILD_WINDOW_PATCH = "win32u-cross-process-child-windows.patch"
CHILD_WINDOW_MODULE = "lib/wine/x86_64-unix/win32u.so"


def line(good, text):
    print(f"ok {text}" if good else f"FAIL {text}")
    return good


def unix_path(prefix, windows_path):
    drive = windows_path[:2].upper()
    rest = windows_path[3:].replace("\\", "/")
    if drive == "C:":
        return Path(prefix) / "drive_c" / rest
    return Path("/") / rest if drive == "Z:" else None


def registered(prefix):
    manifest = Path(prefix) / MANIFEST
    system = Path(prefix) / "system.reg"
    if not manifest.is_file() or not system.is_file():
        return False
    keys = registry.load(system)
    return all(registry.value(keys, f"Software\\Classes\\CLSID\\{clsid}\\InprocServer32", "") == "mscoree.dll" for clsid in qlm_reg.clsids(manifest))


def regasm_line(prefix):
    missing = [folder for folder, _ in REGASM if not (Path(prefix) / "drive_c/windows/Microsoft.NET" / folder / "v4.0.30319/RegAsm.exe").exists()]
    if missing:
        return line(False, f"RegAsm.exe is missing in Microsoft.NET/{' and '.join(missing)}/v4.0.30319, so the FSDT updater cannot start it; run: flightsim-on-linux addon apply gsx-pro")
    return line(True, "RegAsm.exe in Framework and Framework64 v4.0.30319 lets the FSDT updater start it")


def registration_line(prefix):
    manifest = Path(prefix) / MANIFEST
    if not manifest.is_file():
        return line(False, f"{manifest} does not exist: run the FSDT universal installer once, as the GSX Pro page says")
    registered = registry.load(Path(prefix) / "system.reg") if (Path(prefix) / "system.reg").is_file() else {}
    wanted = qlm_reg.clsids(manifest)
    missing = [clsid for clsid in wanted if registry.value(registered, f"Software\\Classes\\CLSID\\{clsid}\\InprocServer32", "") != "mscoree.dll"]
    if missing:
        return line(False, f"{len(missing)} of {len(wanted)} QlmLicenseLib classes are not registered in the prefix; run: flightsim-on-linux addon apply gsx-pro")
    return line(True, f"QlmLicenseLib is registered as COM ({len(wanted)} classes)")


def mono_line(prefix, relative, spec, passed, repair):
    path = Path(prefix) / relative
    if not path.is_file():
        return line(False, f"{path} does not exist; run: flightsim-on-linux addon apply gsx-pro")
    try:
        state = clr.state_of(path, spec)
    except clr.ClrError as error:
        return line(False, f"cannot read {path}: {error}")
    return line(True, passed) if state == "patched" else line(False, f"{path} is {state}: {repair}")


def methods_patched(prefix):
    try:
        return all(clr.state_of(Path(prefix) / relative, spec) == "patched" for relative, spec in PATCHED_METHODS)
    except (OSError, clr.ClrError):
        return False


def copy_version(compatdata):
    record = Path(compatdata, COPY_RECORD) if compatdata else None
    return record.read_text().strip() if record and record.is_file() else None


def stale_line(prefix, proton, compatdata):
    sources = sorted(Path(proton, "files/share/wine/mono").glob("wine-mono-*"), key=lambda folder: [int(part) for part in re.findall(r"\d+", folder.name)])
    copy = Path(prefix) / MONO / UNPATCHED_MARKER
    if not sources or not copy.is_file():
        return line(False, "there is no wine-mono copy to compare with Proton's")
    newest = sources[-1].name
    if (sources[-1] / UNPATCHED_MARKER).read_bytes() == copy.read_bytes():
        return line(True, f"the wine-mono copy in the prefix is {newest}, the one Proton ships")
    if not methods_patched(prefix):
        return line(False, f"the wine-mono copy in the prefix is not {newest}, the one Proton ships now, and it lacks the patches; run: flightsim-on-linux addon apply gsx-pro")
    made = copy_version(compatdata)
    which = f"is {made} and Proton ships {newest} now" if made else f"is not {newest}, the one Proton ships now (the version of the copy is not recorded)"
    return line(True, f"the wine-mono copy in the prefix {which}; it holds the patches, so it still works; to refresh it run: flightsim-on-linux addon apply gsx-pro, then flightsim-on-linux addon apply axis-and-ohs")


def curl_line(prefix):
    missing = [f"{folder}/{name}" for folder in CURL_FOLDERS for name in ("curl.exe", "curl-ca-bundle.crt") if not (Path(prefix) / "drive_c/windows" / folder / name).is_file()]
    if missing:
        return line(False, f"{', '.join(missing)} missing: the updater downloads the GSX engine with curl.exe; run: flightsim-on-linux addon apply gsx-pro")
    return line(True, "curl.exe and its certificates are in system32 and syswow64, so the updater can download the GSX engine")


def entry_lines(prefix, base):
    exe_xml = Path(base) / "exe.xml"
    state = couatl_entry.status(exe_xml, WEBVIEW2_OPTIONS)
    messages = {
        "no-file": f"{exe_xml} does not exist",
        "absent": "exe.xml has no entry that starts the GSX engine (couatl64_boot.exe): install GSX Pro with the updater, then run: flightsim-on-linux addon apply gsx-pro",
        "plain": "exe.xml starts couatl64_boot.exe directly, so the GSX window comes up white; run: flightsim-on-linux addon apply gsx-pro",
        "other": "exe.xml wraps the GSX engine with other WebView2 options than the four software ones; apply does not rewrite an entry it did not make: put the entry back to start couatl64_boot.exe directly, then run: flightsim-on-linux addon apply gsx-pro",
    }
    if state != "wrapped":
        return [line(False, messages[state])]
    wrapper = couatl_entry.wrapper_of(couatl_entry.exe_xml.read_text(exe_xml))
    target = unix_path(prefix, wrapper)
    exists = target is not None and target.is_file()
    return [
        line(True, "exe.xml starts the GSX engine through com-environment.exe, which sets the four WebView2 software options for it"),
        line(exists, f"com-environment.exe exists at {wrapper}" if exists else f"{wrapper} does not exist; run: flightsim-on-linux addon apply gsx-pro"),
    ]


def check(prefix, base, proton, compatdata=""):
    results = [regasm_line(prefix), registration_line(prefix)]
    results.append(mono_line(prefix, MSCORLIB, "register-assembly", "the wine-mono copy's RegisterAssembly returns true, so the FSDT updater accepts the licence library", "run: flightsim-on-linux addon apply gsx-pro"))
    results.append(mono_line(prefix, SYSTEM, "process-responding", "the wine-mono copy's Process.Responding returns true, so the GSX watchdog stops killing a working engine", "run: flightsim-on-linux addon apply gsx-pro"))
    results.append(stale_line(prefix, proton, compatdata))
    results.append(curl_line(prefix))
    if proton:
        good, text = proton_build.patch_line(Path(proton), CHILD_WINDOW_PATCH, CHILD_WINDOW_MODULE, "Proton draws the flightsim.to page inside the GSX window")
        results.append(line(good, text))
    else:
        results.append(line(False, "no Proton found; run: flightsim-on-linux machine"))
    results.extend(entry_lines(prefix, base))
    return 0 if all(results) else 1


def main(argv):
    if len(argv) == 2 and argv[0] == "registered":
        return 0 if registered(argv[1]) else 1
    if len(argv) not in (4, 5) or argv[0] != "check":
        print("usage: gsx.py check <prefix> <base folder> <Proton folder> [<compatdata folder>] | gsx.py registered <prefix>", file=sys.stderr)
        return 2
    return check(*argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
