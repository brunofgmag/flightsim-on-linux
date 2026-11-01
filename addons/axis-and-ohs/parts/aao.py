import sys
from pathlib import Path

PARTS = Path(__file__).resolve().parent
ROOT = PARTS.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "addons/gsx-pro/parts"))

import clr  # noqa: E402
from fsol import proton_build, registry  # noqa: E402

HEAP_PATCH = "ntdll-heap-read-slack.patch"
HEAP_MODULE = "lib/wine/x86_64-windows/ntdll.dll"
WINDOWSBASE = "drive_c/windows/mono/mono-2.0/lib/mono/gac/WindowsBase/4.0.0.0__31bf3856ad364e35/WindowsBase.dll"
ASM_OVERRIDES = r"Software\Wine\Mono\AsmOverrides"


def line(good, text):
    print(f"ok {text}" if good else f"FAIL {text}")
    return good


def asm_override(prefix):
    user = Path(prefix) / "user.reg"
    return registry.value(registry.load(user), ASM_OVERRIDES, "*") if user.is_file() else None


def heap_line(proton):
    if not proton:
        return line(False, "no Proton found; run: flightsim-on-linux machine")
    good, text = proton_build.patch_line(Path(proton), HEAP_PATCH, HEAP_MODULE, "Proton's ntdll.dll leaves readable memory after a heap block, so Axis and Ohs does not crash reading SimConnect messages")
    return line(good, text)


def timer_line(prefix):
    path = Path(prefix) / WINDOWSBASE
    if not path.is_file():
        return line(False, f"{path} does not exist; run: flightsim-on-linux addon apply axis-and-ohs")
    try:
        state = clr.state_of(path, "dispatcher-timer")
    except clr.ClrError as error:
        return line(False, f"cannot read {path}: {error}")
    if state == "patched":
        return line(True, "WindowsBase.dll in the wine-mono copy rounds timer intervals up to 15.625 ms, so the script handler does not run out of interval")
    return line(False, f"{path} is {state}; run: flightsim-on-linux addon apply axis-and-ohs")


def override_line(prefix):
    value = asm_override(prefix)
    if value == "gac=n":
        return line(True, "wine-mono ignores the Windows GAC (*=gac=n), so it does not load Microsoft's WPF from .NET 4.8")
    return line(False, f"HKCU\\{ASM_OVERRIDES} * is {value!r}, not 'gac=n'; run: flightsim-on-linux addon apply axis-and-ohs")


def main(argv):
    if len(argv) == 2 and argv[0] == "asm-override":
        print(asm_override(argv[1]) or "")
        return 0
    if len(argv) != 3 or argv[0] != "check":
        print("usage: aao.py check <prefix> <Proton folder> | aao.py asm-override <prefix>", file=sys.stderr)
        return 2
    results = [heap_line(argv[2]), timer_line(argv[1]), override_line(argv[1])]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
