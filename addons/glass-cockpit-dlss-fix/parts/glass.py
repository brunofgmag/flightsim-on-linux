import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON_FILE = "glass-cockpit-dlss-fix.addon64"
INI = "glass-cockpit-dlss-fix.ini"
RESHADE_DLL = "dxgi.dll"
VKD3D_PATCHES = ("root-signature-reuse-identical-blob.patch", "descriptor-heap-covers-its-cpu-handles.patch")
VKD3D_MODULE = "lib/wine/vkd3d-proton/x86_64-windows/d3d12core.dll"
NVIDIA = "10de"
DARK_SCREEN = ("experimental", "dark_screen_fix", "1")
TRACKING = ("debug", "descriptor_tracking", "1")
APPLY = "flightsim-on-linux addon apply glass-cockpit-dlss-fix"
USAGE = "usage: glass.py apply|check|undo <simulator folder> <Proton folder> <backup folder>"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proton_build = load("proton_build", ROOT / "fsol" / "proton_build.py")
discrete = load("discrete_gpu", ROOT / "tools" / "discrete-gpu.py")


def simulator_gpu_vendor():
    if not shutil.which("vulkaninfo"):
        return None, "vulkaninfo is missing; install your distribution's vulkan-tools package"
    summary = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True).stdout
    gpus = [gpu for gpu in discrete.devices(summary) if gpu.kind != discrete.CPU]
    try:
        chosen = gpus[0] if len(gpus) == 1 else discrete.discrete_choice(summary)
    except discrete.Ambiguous as error:
        return None, f"cannot pick one GPU: {error}"
    if chosen is None:
        return None, "vulkaninfo lists no GPU"
    return discrete.pci_id(chosen.vendor), chosen.name


def split_lines(text):
    return text.splitlines(keepends=True)


def line_ending(lines):
    return "\r\n" if lines and lines[0].endswith("\r\n") else "\n"


def section_bounds(lines, section):
    start = next((i for i, line in enumerate(lines) if line.strip().lower() == f"[{section}]"), None)
    if start is None:
        return None, None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    return start, end


def key_index(lines, start, end, key):
    for i in range(start + 1, end):
        name, _, _ = lines[i].partition("=")
        if name.strip().lower() == key:
            return i
    return None


def read_value(text, section, key):
    lines = split_lines(text)
    start, end = section_bounds(lines, section)
    if start is None:
        return None
    index = key_index(lines, start, end, key)
    return None if index is None else lines[index].partition("=")[2].strip()


def with_value(text, section, key, value):
    lines = split_lines(text)
    eol = line_ending(lines)
    start, end = section_bounds(lines, section)
    if start is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += eol
        return "".join(lines + [f"[{section}]{eol}", f"{key}={value}{eol}"])
    index = key_index(lines, start, end, key)
    if index is None:
        lines.insert(start + 1, f"{key}={value}{eol}")
    else:
        lines[index] = f"{key}={value}{eol}"
    return "".join(lines)


def read_ini(sim):
    path = Path(sim) / INI
    return path.read_bytes().decode("utf-8", errors="surrogateescape") if path.is_file() else None


def backup_before_first_change(sim, backup):
    backup = Path(backup)
    original, absent = backup / f"{INI}.orig", backup / f"{INI}.absent"
    if original.exists() or absent.exists():
        return
    backup.mkdir(parents=True, exist_ok=True)
    if (Path(sim) / INI).is_file():
        shutil.copy2(Path(sim) / INI, original)
    else:
        absent.write_text("")


def apply(sim, proton, backup):
    sim = Path(sim)
    if not (sim / ADDON_FILE).is_file():
        print(f"{sim / ADDON_FILE} does not exist; run: flightsim-on-linux addon install glass-cockpit-dlss-fix", file=sys.stderr)
        return 1
    backup_before_first_change(sim, backup)
    text = read_ini(sim) or ""
    text = with_value(text, *DARK_SCREEN)
    print("dark_screen_fix=1 is set under [experimental]")
    vendor, detail = simulator_gpu_vendor()
    if vendor == NVIDIA:
        text = with_value(text, *TRACKING)
        print(f"descriptor_tracking=1 is set under [debug], because the GPU is NVIDIA ({detail})")
    elif vendor:
        print(f"descriptor_tracking is left alone: {detail} is not NVIDIA, and AMD and Intel drivers are tracked without it")
    else:
        print(f"descriptor_tracking is left alone: {detail}. On an NVIDIA GPU set it to 1 under [debug] yourself")
    (sim / INI).write_bytes(text.encode("utf-8", errors="surrogateescape"))
    return 0


def ini_line(good, passed, failed):
    return good, f"ok {passed}" if good else f"FAIL {failed}"


def tracking_line(text):
    section, key, wanted = TRACKING
    vendor, detail = simulator_gpu_vendor()
    if vendor == NVIDIA:
        return ini_line(read_value(text, section, key) == wanted, f"{key}={wanted} is set under [{section}], as the NVIDIA GPU ({detail}) needs", f"{key}={wanted} is not set under [{section}], and the NVIDIA GPU ({detail}) needs it; run: {APPLY}")
    if vendor:
        return True, f"ok {key} is not needed: {detail} is not NVIDIA"
    return False, f"FAIL cannot tell whether {key} is needed: {detail}"


def check_ini(sim):
    text = read_ini(sim)
    if text is None:
        return [(False, f"FAIL {Path(sim) / INI} does not exist; run: {APPLY}")]
    section, key, wanted = DARK_SCREEN
    dark_screen = ini_line(read_value(text, section, key) == wanted, f"{key}={wanted} is set under [{section}]", f"{key}={wanted} is not set under [{section}]; run: {APPLY}")
    return [dark_screen, tracking_line(text)]


def check_vkd3d(proton):
    if not proton:
        return [(False, "FAIL no Proton found; run: flightsim-on-linux machine")]
    results = [proton_build.listed_line(Path(proton), patch, f"Proton FS2024 was built with {patch}", "vkd3d-patch") for patch in VKD3D_PATCHES]
    results.append(proton_build.module_line(Path(proton), VKD3D_MODULE, "d3d12core.dll is the one the build recorded"))
    return [(good, f"ok {text}" if good else f"FAIL {text}") for good, text in results]


def check_files(sim):
    sim = Path(sim)
    lines = []
    reshade = (sim / RESHADE_DLL).is_file()
    lines.append((reshade, "ok ReShade's dxgi.dll is in the simulator folder" if reshade else "FAIL ReShade's dxgi.dll is not in the simulator folder; run: flightsim-on-linux addon install reshade <dxgi.dll>"))
    present = (sim / ADDON_FILE).is_file()
    lines.append((present, f"ok {ADDON_FILE} is in the simulator folder" if present else f"FAIL {ADDON_FILE} is not in the simulator folder; run: flightsim-on-linux addon install glass-cockpit-dlss-fix"))
    return lines


def check(sim, proton):
    lines = check_files(sim) + check_ini(sim) + check_vkd3d(proton)
    for _, line in lines:
        print(line)
    return 0 if all(good for good, _ in lines) else 1


def undo(sim, backup):
    backup = Path(backup)
    original, absent = backup / f"{INI}.orig", backup / f"{INI}.absent"
    target = Path(sim) / INI
    if original.exists():
        shutil.copy2(original, target)
        original.unlink()
        print(f"{INI} is as it was before apply")
    elif absent.exists():
        target.unlink(missing_ok=True)
        absent.unlink()
        print(f"{INI} did not exist before apply, so it is gone")
    else:
        print("nothing to undo: apply did not run")
    print("the add-on itself stays in the simulator's folder; delete glass-cockpit-dlss-fix.addon64 to remove it")
    return 0


def main(argv):
    if len(argv) != 4 or argv[0] not in ("apply", "check", "undo"):
        print(USAGE, file=sys.stderr)
        return 2
    action, sim, proton, backup = argv
    if not sim:
        print("FAIL no simulator install folder found; run: flightsim-on-linux machine")
        return 1
    if action == "apply":
        return apply(sim, proton, backup)
    if action == "check":
        return check(sim, proton)
    return undo(sim, backup)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
