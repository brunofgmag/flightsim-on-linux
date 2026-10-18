import argparse
import hashlib
import json
import re
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fsol import registry  # noqa: E402

Setting = namedtuple("Setting", "hive path name value wow label")

FENIX_EXECUTABLES = ("Fenix.exe", "FenixBootstrapper.exe", "FenixSystem.exe", "FenixCDU.exe", "FenixDisplay.exe")
SETTINGS = (
    Setting("HKCU", r"Software\Wine\DllOverrides", "mscoree", "builtin", False, "mscoree stays builtin for every other program (wine-mono)"),
    *(
        Setting("HKCU", rf"Software\Wine\AppDefaults\{exe}\DllOverrides", "mscoree", "native", False, f"mscoree is native for {exe}")
        for exe in FENIX_EXECUTABLES
    ),
    Setting("HKLM", r"Software\Microsoft\.NETFramework", "InstallRoot", "C:\\windows\\Microsoft.NET\\Framework64\\", False, ".NETFramework InstallRoot (64-bit view)"),
    Setting("HKLM", r"Software\Microsoft\.NETFramework", "InstallRoot", "C:\\windows\\Microsoft.NET\\Framework\\", True, ".NETFramework InstallRoot (32-bit view)"),
    Setting("HKCU", r"Software\Wine\Mono\AsmOverrides", "*", "gac=n", False, "wine-mono ignores the Windows GAC (gac=n)"),
)
REGISTRY_FILES = {"HKCU": "user.reg", "HKLM": "system.reg"}
MSCOREE_SHA256 = {
    "system32": "93077039e6606decda91ea85e785227e0aeecf01356f257c35fce1446b0a0c91",
    "syswow64": "24d1919b63ed444f5c6629626c239ec78a5bb7a3f29b154feea03a6fb8f26148",
}
CLR_FOLDERS = ("Framework64", "Framework")
CLR_VERSION_PREFIX = "4.8."
PRODUCT_VERSION = re.compile(b"P\0r\0o\0d\0u\0c\0t\0V\0e\0r\0s\0i\0o\0n\0\0\0((?:[^\0]\0)+)")
GROUPS = ("registry", "clr", "mscoree")
WatchedKey = namedtuple("WatchedKey", "hive path wow names")
WINDOWS_VERSION_KEYS = (
    WatchedKey("HKLM", r"Software\Microsoft\Windows NT\CurrentVersion", False, None),
    WatchedKey("HKLM", r"Software\Microsoft\Windows NT\CurrentVersion", True, None),
    WatchedKey("HKCU", r"Software\Wine", False, ("Version",)),
)
RAW_VALUE = re.compile(r"^hex(\([0-9a-f]+\))?:")


def stored_path(setting):
    if not setting.wow:
        return setting.path
    return setting.path.replace("Software\\", "Software\\Wow6432Node\\", 1)


def is_typed(value):
    return isinstance(value, int) or (isinstance(value, str) and not RAW_VALUE.match(value))


def watched_values(prefix, key):
    found = registry.key(load_registry(prefix, key.hive), stored_path(key)) or {}
    return {name: value for name, value in found.items() if is_typed(value) and (key.names is None or name in key.names)}


def windows_version_snapshot(prefix):
    return [watched_values(prefix, key) for key in WINDOWS_VERSION_KEYS]


def in_view(arguments, key):
    return arguments + ["/reg:32"] if key.wow else arguments


def add_arguments(key, name, value):
    kind, data = ("REG_DWORD", str(value)) if isinstance(value, int) else ("REG_SZ", value)
    return in_view(["reg", "add", rf"{key.hive}\{key.path}", "/v", name, "/t", kind, "/d", data, "/f"], key)


def delete_arguments(key, name):
    return in_view(["reg", "delete", rf"{key.hive}\{key.path}", "/v", name, "/f"], key)


def windows_version_restore(prefix, saved):
    for key, wanted, now in zip(WINDOWS_VERSION_KEYS, saved, windows_version_snapshot(prefix)):
        for name, value in wanted.items():
            if now.get(name) != value:
                yield add_arguments(key, name, value)
        for name in now.keys() - wanted.keys():
            yield delete_arguments(key, name)


def load_registry(prefix, hive):
    file = Path(prefix) / REGISTRY_FILES[hive]
    return registry.load(file) if file.is_file() else {}


def current_value(prefix, setting):
    return registry.value(load_registry(prefix, setting.hive), stored_path(setting), setting.name)


def setting_holds(prefix, setting):
    return current_value(prefix, setting) == setting.value


def is_app_override(setting):
    return "AppDefaults" in setting.path


def product_version(path):
    if not path.is_file():
        return None
    found = PRODUCT_VERSION.search(path.read_bytes())
    return found.group(1).decode("utf-16le") if found else ""


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def registry_lines(prefix):
    for setting in SETTINGS:
        if setting_holds(prefix, setting):
            yield True, setting.label
        else:
            yield False, f"{setting.label} (found {current_value(prefix, setting)!r}, want {setting.value!r})"


def clr_lines(prefix):
    for folder in CLR_FOLDERS:
        path = Path(prefix) / "drive_c/windows/Microsoft.NET" / folder / "v4.0.30319/clr.dll"
        version = product_version(path)
        label = f"{folder}/v4.0.30319/clr.dll is .NET {CLR_VERSION_PREFIX}x"
        if version and version.startswith(CLR_VERSION_PREFIX):
            yield True, f"{label} ({version})"
        else:
            yield False, f"{label} (found {version if path.is_file() else 'no file'})"


def mscoree_lines(prefix):
    for folder, wanted in MSCOREE_SHA256.items():
        path = Path(prefix) / "drive_c/windows" / folder / "mscoree.dll"
        found = sha256_of(path)
        label = f"{folder}/mscoree.dll is Microsoft's 4.0.31106.0"
        yield (True, label) if found == wanted else (False, f"{label} (found {found or 'no file'})")


def group_lines(prefix, group):
    return {"registry": registry_lines, "clr": clr_lines, "mscoree": mscoree_lines}[group](prefix)


def command_check(args):
    failed = False
    for group in [args.group] if args.group else GROUPS:
        for good, text in group_lines(args.prefix, group):
            print(f"ok {text}" if good else f"FAIL {text}")
            failed = failed or not good
    return 1 if failed else 0


def print_arguments(lines):
    for arguments in lines:
        print("\t".join(arguments))
    return 0


def command_missing(args):
    return print_arguments(add_arguments(item, item.name, item.value) for item in SETTINGS if not setting_holds(args.prefix, item))


def command_remove_overrides(args):
    present = (item for item in SETTINGS if is_app_override(item) and current_value(args.prefix, item) is not None)
    return print_arguments(delete_arguments(item, item.name) for item in present)


def command_winver_save(args):
    Path(args.file).write_text(json.dumps(windows_version_snapshot(args.prefix)))
    return 0


def command_winver_restore(args):
    saved = json.loads(Path(args.file).read_text())
    return print_arguments(windows_version_restore(args.prefix, saved))


def command_sha256(args):
    print(MSCOREE_SHA256[args.folder])
    return 0


def parser():
    root = argparse.ArgumentParser(prog="dotnet48.py")
    commands = root.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("prefix")
    check.add_argument("group", nargs="?", choices=GROUPS)
    check.set_defaults(run=command_check)
    for name, run in (("missing", command_missing), ("remove-overrides", command_remove_overrides)):
        command = commands.add_parser(name)
        command.add_argument("prefix")
        command.set_defaults(run=run)
    for name, run in (("winver-save", command_winver_save), ("winver-restore", command_winver_restore)):
        command = commands.add_parser(name)
        command.add_argument("prefix")
        command.add_argument("file")
        command.set_defaults(run=run)
    sha = commands.add_parser("sha256")
    sha.add_argument("folder", choices=sorted(MSCOREE_SHA256))
    sha.set_defaults(run=command_sha256)
    return root


if __name__ == "__main__":
    args = parser().parse_args()
    sys.exit(args.run(args))
