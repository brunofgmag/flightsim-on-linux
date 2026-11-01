import hashlib
import json
import re
import sys
from collections import namedtuple
from pathlib import Path

PARTS = Path(__file__).resolve().parent
ROOT = PARTS.parents[2]
sys.path.insert(0, str(ROOT))

from fsol import container, machine, proton_build, registry  # noqa: E402

Setting = namedtuple("Setting", "hive key name value")

OPTIONS = "--in-process-gpu --disable-gpu --disable-gpu-compositing --disable-software-rasterizer"
FONTS_KEY = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"
FONTS = {
    "Tahoma (TrueType)": "tahoma.ttf",
    "Tahoma Bold (TrueType)": "tahomabd.ttf",
    "Marlett (TrueType)": "marlett.ttf",
    "Symbol (TrueType)": "symbol.ttf",
    "Webdings (TrueType)": "webdings.ttf",
    "Wingdings (TrueType)": "wingdings.ttf",
}
POLICY = Setting("HKCU", r"Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments", "SayIntentionsAI.exe", OPTIONS)
WEBVIEW2_VERSION = Setting("HKCU", r"Software\Wine\AppDefaults\msedgewebview2.exe", "Version", "win7")
TELEMETRY = Setting("HKCU", r"Software\Wine\AppDefaults\si_telemetry.exe\DllOverrides", "mscoree", "native")
FILES = {"HKCU": "user.reg", "HKLM": "system.reg"}
ROOT_KEYS = {"HKCU": "HKEY_CURRENT_USER", "HKLM": "HKEY_LOCAL_MACHINE"}
APP = "drive_c/Program Files/SayIntentions.AI/SayIntentionsAI"
LOADER = "WebView2Loader.dll"
LOADER_SOURCE = f"{APP}/runtimes/win-x64/native/{LOADER}"
CLR_FOLDERS = ("Framework64", "Framework")
PRODUCT_VERSION = re.compile(b"P\0r\0o\0d\0u\0c\0t\0V\0e\0r\0s\0i\0o\0n\0\0\0((?:[^\0]\0)+)")
CHILD_WINDOWS = ("win32u-cross-process-child-windows.patch", "lib/wine/x86_64-unix/win32u.so", "Proton draws the WebView2 page inside the SayIntentions window")
OPAQUE = ("winewayland-opaque-xrgb.patch", "lib/wine/x86_64-unix/winewayland.so", "Proton hands the compositor opaque pixels, so the window does not go black on some outputs under KWin 6.8")
HEAP = ("ntdll-heap-bounded-free-list-scan.patch", "lib/wine/x86_64-windows/ntdll.dll", "Proton's heap stops walking a long free list, which is expected to remove the once-a-second simulator stalls with SayIntentions open (not yet seen in flight)")
MMDEVAPI = "lib/wine/x86_64-windows/mmdevapi.dll"


def line(good, text):
    print(f"ok {text}" if good else f"FAIL {text}")
    return good


def load(prefix, hive):
    path = Path(prefix) / FILES[hive]
    return registry.load(path) if path.is_file() else {}


def wanted(net48):
    settings = [POLICY, WEBVIEW2_VERSION]
    if net48:
        settings.append(TELEMETRY)
    settings += [Setting("HKLM", FONTS_KEY, name, value) for name, value in FONTS.items()]
    return settings


def clr_version(path):
    if not path.is_file():
        return None
    found = PRODUCT_VERSION.search(path.read_bytes())
    return found.group(1).decode("utf-16le") if found else None


def net48_present(prefix):
    versions = [clr_version(Path(prefix) / "drive_c/windows/Microsoft.NET" / folder / "v4.0.30319/clr.dll") for folder in CLR_FOLDERS]
    return any(version and version.startswith("4.8.") for version in versions)


def current(prefix, setting):
    return registry.value(load(prefix, setting.hive), setting.key, setting.name)


def reg_string(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def reg_value(value):
    return f"dword:{value:08x}" if isinstance(value, int) else reg_string(value)


def reg_text(entries):
    sections = {}
    for hive, key, name, value in entries:
        data = '=-' if value is None else "=" + reg_value(value)
        sections.setdefault(f"{ROOT_KEYS[hive]}\\{key}", []).append(reg_string(name) + data)
    lines = ["REGEDIT4", ""]
    for section, values in sections.items():
        lines += [f"[{section}]", *values, ""]
    return "\r\n".join(lines)


def load_record(path):
    return json.loads(Path(path).read_text()) if Path(path).is_file() else []


def remembered(record, setting):
    return next((item for item in record if (item["hive"], item["key"].lower(), item["name"].lower()) == (setting.hive, setting.key.lower(), setting.name.lower())), None)


def plan_apply(prefix, record_path, out):
    record = load_record(record_path)
    changes = [setting for setting in wanted(net48_present(prefix)) if current(prefix, setting) != setting.value]
    for setting in changes:
        if not remembered(record, setting):
            record.append({"hive": setting.hive, "key": setting.key, "name": setting.name, "previous": current(prefix, setting)})
    if changes:
        Path(record_path).parent.mkdir(parents=True, exist_ok=True)
        Path(record_path).write_text(json.dumps(record, indent=1))
        Path(out).write_bytes(reg_text([(item.hive, item.key, item.name, item.value) for item in changes]).encode("latin-1"))
    return len(changes)


def plan_undo(record_path, out):
    record = load_record(record_path)
    if record:
        Path(out).write_bytes(reg_text([(item["hive"], item["key"], item["name"], item["previous"]) for item in record]).encode("latin-1"))
    return len(record)


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def setting_line(prefix, setting, passed, repair):
    found = current(prefix, setting)
    if found == setting.value:
        return line(True, passed)
    return line(False, f"{setting.name} under {setting.key} is {found!r}, not {setting.value!r}; {repair}")


def telemetry_line(prefix):
    if not net48_present(prefix):
        return line(False, "there is no .NET Framework 4.8 in the prefix, and si_telemetry.exe of SayIntentions 5.6.5.4 and later cannot run on wine-mono: the interface stays on its loading screen; run: flightsim-on-linux addon apply dotnet48, then: flightsim-on-linux addon apply sayintentions")
    return setting_line(prefix, TELEMETRY, "si_telemetry.exe runs on the prefix's .NET Framework 4.8 (mscoree is native for it)", "run: flightsim-on-linux addon apply sayintentions")


def loader_line(prefix):
    source = Path(prefix) / LOADER_SOURCE
    target = Path(prefix) / APP / LOADER
    if not source.is_file():
        return line(False, f"{source} does not exist: SayIntentions is not installed, or its package changed; run: flightsim-on-linux addon install sayintentions <the Update Hub installer>")
    if sha256_of(source) != sha256_of(target):
        return line(False, f"{LOADER} is not beside SayIntentionsAI.exe, and wine-mono does not look in runtimes\\win-x64\\native, so the embedded browser does not start; run: flightsim-on-linux addon apply sayintentions")
    return line(True, f"{LOADER} sits beside SayIntentionsAI.exe, where wine-mono looks for it")


def fonts_line(prefix):
    system = load(prefix, "HKLM")
    missing = [name for name in FONTS if registry.value(system, FONTS_KEY, name) is None]
    if missing:
        return line(False, f"the fonts registry key lacks {', '.join(missing)}: the WebView2 browser overflows its stack asking DirectWrite for Tahoma; run: flightsim-on-linux addon apply sayintentions")
    return line(True, "the six Wine fonts (Tahoma, Tahoma Bold, Marlett, Symbol, Webdings, Wingdings) are in the registry, so the WebView2 browser can start")


def mmdevapi_line(proton):
    record = proton_build.build_record(Path(proton))
    path = Path(proton) / "files" / MMDEVAPI
    overlay = any(kind == "overlay" for kind, _, _ in record)
    if overlay and proton_build.recorded_module(record, MMDEVAPI) == sha256_of(path):
        return line(True, "Proton's mmdevapi.dll is the one the build recorded, with the audio session notification the Fenix overlay patch adds, so SayIntentions stays open after it speaks")
    return line(False, f"{MMDEVAPI} is not the one a Proton FS2024 build recorded, so SayIntentions can close after it speaks; run: {proton_build.PROTON_BUILD}")


def voice_line(steam):
    libraries = machine.steam_libraries(Path(steam)) if steam else []
    folder = container.voice_files(libraries)
    if folder:
        return line(True, f"the Steam app Proton Voice Files is installed ({folder}), so the voices SayIntentions speaks with exist")
    return line(False, "the Steam app Proton Voice Files (app 3086180) is not installed: SayIntentions warns that no en-US voice is available; install it from Steam")


def proton_lines(proton):
    if not proton:
        return [line(False, "no Proton found; run: flightsim-on-linux machine")]
    results = [line(*proton_build.patch_line(Path(proton), *fix)) for fix in (CHILD_WINDOWS, OPAQUE, HEAP)]
    return results + [mmdevapi_line(proton)]


def check(prefix, proton, steam):
    results = [
        setting_line(prefix, POLICY, "the WebView2 policy gives SayIntentionsAI.exe the four software options, so its browser draws inside the window", "run: flightsim-on-linux addon apply sayintentions"),
        setting_line(prefix, WEBVIEW2_VERSION, "msedgewebview2.exe sees Windows 7, which its Wine build needs to start", "run: flightsim-on-linux addon apply sayintentions"),
        telemetry_line(prefix),
        loader_line(prefix),
        fonts_line(prefix),
    ]
    return results + proton_lines(proton) + [voice_line(steam)]


def main(argv):
    if len(argv) == 4 and argv[0] == "check":
        return 0 if all(check(*argv[1:])) else 1
    if len(argv) == 4 and argv[0] == "plan-apply":
        print(plan_apply(*argv[1:]))
        return 0
    if len(argv) == 2 and argv[0] == "net48":
        print("present" if net48_present(argv[1]) else "missing")
        return 0
    if len(argv) == 3 and argv[0] == "plan-undo":
        print(plan_undo(*argv[1:]))
        return 0
    print("usage: sayintentions.py check <prefix> <Proton folder> <Steam folder> | net48 <prefix> | plan-apply <prefix> <record.json> <out.reg> | plan-undo <record.json> <out.reg>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
