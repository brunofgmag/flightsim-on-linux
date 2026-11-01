import argparse
import importlib.util
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2] / "dotnet48" / "parts" / "dotnet48.py"


def load_base():
    spec = importlib.util.spec_from_file_location("dotnet48_base", BASE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_base()
FENIX_EXECUTABLES = ("Fenix.exe", "FenixBootstrapper.exe", "FenixSystem.exe", "FenixCDU.exe", "FenixDisplay.exe")
SETTINGS = tuple(
    base.Setting("HKCU", rf"Software\Wine\AppDefaults\{exe}\DllOverrides", "mscoree", "native", False, f"mscoree is native for {exe}")
    for exe in FENIX_EXECUTABLES
)


def registry_lines(prefix):
    for setting in SETTINGS:
        if base.setting_holds(prefix, setting):
            yield True, setting.label
        else:
            yield False, f"{setting.label} (found {base.current_value(prefix, setting)!r}, want {setting.value!r})"


def command_check(args):
    failed = False
    for good, text in registry_lines(args.prefix):
        print(f"ok {text}" if good else f"FAIL {text}")
        failed = failed or not good
    return 1 if failed else 0


def command_missing(args):
    return base.print_arguments(base.add_arguments(item, item.name, item.value) for item in SETTINGS if not base.setting_holds(args.prefix, item))


def command_remove(args):
    present = (item for item in SETTINGS if base.current_value(args.prefix, item) is not None)
    return base.print_arguments(base.delete_arguments(item, item.name) for item in present)


def parser():
    root = argparse.ArgumentParser(prog="overrides.py")
    commands = root.add_subparsers(dest="command", required=True)
    for name, run in (("check", command_check), ("missing", command_missing), ("remove", command_remove)):
        command = commands.add_parser(name)
        command.add_argument("prefix")
        command.set_defaults(run=run)
    return root


if __name__ == "__main__":
    args = parser().parse_args()
    sys.exit(args.run(args))
