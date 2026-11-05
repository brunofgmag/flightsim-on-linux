import hashlib
import importlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Callable, NamedTuple

PARTS = Path(__file__).resolve().parent
ROOT = PARTS.parents[2]
sys.path.insert(0, str(ROOT))
machine = importlib.import_module("fsol.machine")

PROXY = "version.dll"
ARCHIVE_MEMBER = "MFGUnlocker.dll"
CONFIG = "RTXMFG-Universal.json"
BACKUP_NAME = f"{CONFIG}.orig"
WANTED = {"followGame": False, "mode": "fixed"}
NVIDIA = "10de"
ADA = re.compile(r"\bRTX\s*40\d0\b|\bAda\b", re.IGNORECASE)
LOG_GLOB = "RTXMFG-*.log"
LOG_PROOFS = (
    ("capability=8.9 verified=1", "the mod found the RTX 40 GPU through CUDA", "the mod did not find the RTX 40 GPU through CUDA; check that PROTON_NVIDIA_NVCUDA=1 is in the launch options and start the simulator again"),
    ("MFG_PROXY_UI state=active", "the mod's menu installed", "the mod's menu did not install; the patched version.dll has to be the one that loaded, and the simulator has to have started since it was installed"),
)
GENERATED = ("RTXMFG-*", "RTX40MFG-UI*.ini")
APPLY = "flightsim-on-linux addon apply mfg-unlocker"
INSTALL = "flightsim-on-linux addon install mfg-unlocker"
USAGE = "usage: mfg.py install <simulator folder> <zip> <zip sha256> | apply <simulator folder> <Steam folder> <backup folder> | check <simulator folder> <Steam folder> | undo <simulator folder> <backup folder>"


def discrete_module():
    spec = importlib.util.spec_from_file_location("discrete_gpu", ROOT / "tools" / "discrete-gpu.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


discrete = discrete_module()


class PatchError(Exception):
    pass


class LaunchPart(NamedTuple):
    name: str
    text: str
    reason: str
    holds: Callable[[dict], bool]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load_table():
    return json.loads((PARTS / "patches.json").read_text())


def table_runs(table):
    return [(int(run["offset"], 16), bytes.fromhex(run["original"]), bytes.fromhex(run["patched"])) for patch in table["patches"] for run in patch["runs"]]


def patch_image(data, table):
    digest = sha256(data)
    if digest != table["input_sha256"]:
        raise PatchError(f"the DLL has sha256 {digest}, not the {table['input_sha256']} this patch table was made for")
    runs = table_runs(table)
    for offset, original, _ in runs:
        if data[offset:offset + len(original)] != original:
            raise PatchError(f"the bytes at offset {offset:#x} are not the ones this patch table expects")
    image = bytearray(data)
    for offset, _, patched in runs:
        image[offset:offset + len(patched)] = patched
    result = sha256(image)
    if result != table["output_sha256"]:
        raise PatchError(f"the patched DLL has sha256 {result}, not the {table['output_sha256']} the patch table promises")
    return bytes(image)


def proxy_state(sim, table=None):
    path = Path(sim) / PROXY
    if not path.is_file():
        return "absent", None
    digest = sha256(path.read_bytes())
    return ("ours" if digest == (table or load_table())["output_sha256"] else "foreign"), digest


def proxy_problem(sim, table=None):
    state, digest = proxy_state(sim, table)
    if state == "absent":
        return f"{PROXY} is not in the simulator folder; run: {INSTALL}"
    if state == "foreign":
        return f"{Path(sim) / PROXY} is not the patched MFG Unlocker (sha256 {digest}); another mod may use that name, and {INSTALL} does not overwrite it"
    return None


def archive_problem(archive, archive_sha):
    try:
        archive_bytes = Path(archive).read_bytes()
    except OSError as error:
        return f"cannot read {archive}: {error}"
    if sha256(archive_bytes) != archive_sha:
        return f"{archive} is not the release this add-on was tested with (sha256 {archive_sha}); nothing was installed"
    return None


def patched_image(archive, table):
    try:
        with zipfile.ZipFile(archive) as bundle:
            return patch_image(bundle.read(ARCHIVE_MEMBER), table)
    except (zipfile.BadZipFile, KeyError) as error:
        raise PatchError(f"{ARCHIVE_MEMBER} cannot be read from the zip ({error!r})") from error


def write_proxy(sim, image):
    part = Path(sim) / f"{PROXY}.part"
    part.write_bytes(image)
    os.replace(part, Path(sim) / PROXY)


def install(archive, archive_sha, sim):
    sim = Path(sim)
    problem = archive_problem(archive, archive_sha)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    table = load_table()
    state, digest = proxy_state(sim, table)
    if state == "foreign":
        print(f"{sim / PROXY} is not the patched MFG Unlocker (sha256 {digest}); nothing was installed. Move it away first, if you want this one in its place", file=sys.stderr)
        return 1
    if state == "ours":
        print(f"the patched MFG Unlocker {table['release']} is already in {sim}; next: {APPLY}")
        return 0
    try:
        image = patched_image(archive, table)
    except PatchError as error:
        print(f"{archive} cannot be patched: {error}; nothing was installed", file=sys.stderr)
        return 1
    write_proxy(sim, image)
    if not (sim / "dxgi.dll").is_file():
        print("note: ReShade's dxgi.dll is not in the simulator folder, and this build was only tested with ReShade present")
    print(f"the patched MFG Unlocker {table['release']} is in {sim} as {PROXY}; next: {APPLY}")
    return 0


def simulator_gpu():
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
    return chosen, ""


def passed(text):
    return True, f"ok {text}"


def failed(text):
    return False, f"FAIL {text}"


def verdict(good, passed_text, failed_text):
    return passed(passed_text) if good else failed(failed_text)


def gpu_line():
    gpu, problem = simulator_gpu()
    if gpu is None:
        return failed(f"cannot tell which GPU the simulator uses: {problem}")
    if discrete.pci_id(gpu.vendor) != NVIDIA:
        return failed(f"the GPU is {gpu.name}, which is not NVIDIA; the mod needs an RTX 40 series card")
    if not ADA.search(gpu.name):
        return failed(f"the GPU is {gpu.name}, not an RTX 40 series card; RTX 50 cards have multi frame generation of their own and older cards have no frame generation")
    return passed(f"the GPU is {gpu.name}, an RTX 40 series card")


def proxy_line(sim):
    table = load_table()
    problem = proxy_problem(sim, table)
    if problem:
        return failed(problem)
    return passed(f"{PROXY} in the simulator folder is the patched MFG Unlocker {table['release']}")


def environment_of(options):
    environment = {}
    try:
        words = shlex.split(options or "")
    except ValueError:
        return environment
    for word in words:
        if word == "%command%":
            break
        name, equals, value = word.partition("=")
        if equals and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            environment[name] = value
    return environment


def overrides_hold_version(value):
    for entry in value.split(";"):
        names, _, modes = entry.partition("=")
        if "version" in names.split(",") and modes.split(",")[0] == "n":
            return True
    return False


LAUNCH_PARTS = (
    LaunchPart("WINEDLLOVERRIDES", 'WINEDLLOVERRIDES="version=n,b"', "without it Wine loads its builtin version.dll and never the mod", lambda env: overrides_hold_version(env.get("WINEDLLOVERRIDES", ""))),
    LaunchPart("PROTON_NVIDIA_NVCUDA", "PROTON_NVIDIA_NVCUDA=1", "without it the mod finds no RTX 40 GPU, because it takes that route only when CUDA answers with compute capability 8.9", lambda env: env.get("PROTON_NVIDIA_NVCUDA") == "1"),
    LaunchPart("PROTON_DLSS_UPGRADE", "PROTON_DLSS_UPGRADE=1", "without it Proton does not put the newest DLSS frame generation in the prefix, and that is the copy the mod patches", lambda env: env.get("PROTON_DLSS_UPGRADE") == "1"),
    LaunchPart("ngx_dlss_fg_override", "ngx_dlss_fg_override=on in DXVK_NVAPI_DRS_SETTINGS", "DLSS frame generation needs it, and that variable takes a comma-separated list, so add it to the one you have", lambda env: "ngx_dlss_fg_override=on" in env.get("DXVK_NVAPI_DRS_SETTINGS", "").split(",")),
)


def launch_environments(steam):
    if not steam:
        return None
    return [environment_of(options) for options in machine.launch_options(Path(steam)).values()]


def missing_parts(steam):
    environments = launch_environments(steam)
    if environments is None:
        return None
    return [part for part in LAUNCH_PARTS if not any(part.holds(env) for env in environments)]


def launch_lines(steam):
    missing = missing_parts(steam)
    if missing is None:
        return [failed("no Steam folder found; run: flightsim-on-linux machine")]
    return [
        verdict(
            part not in missing,
            f"the launch options of the simulator in Steam hold {part.name}",
            f"the launch options of the simulator in Steam do not hold {part.text}: {part.reason}; run {APPLY} to see what to add",
        )
        for part in LAUNCH_PARTS
    ]


def read_config(sim):
    path = Path(sim) / CONFIG
    return path.read_bytes().decode("utf-8") if path.is_file() else None


def parse_config(text):
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


def replace_value(text, key, literal):
    pattern = re.compile(rf'("{re.escape(key)}"\s*:\s*)("(?:[^"\\]|\\.)*"|[^,}}\s]+)')
    return pattern.sub(lambda found: found.group(1) + literal, text, count=1)


def insert_value(text, key, literal):
    separator = "," if parse_config(text) else ""
    return text.replace("{", f'{{"{key}":{literal}{separator}', 1)


def with_keys(text, wanted):
    data = parse_config(text)
    edited = text
    for key, value in wanted.items():
        change = replace_value if key in data else insert_value
        edited = change(edited, key, json.dumps(value))
    if parse_config(edited) != {**data, **wanted}:
        raise ValueError("the edit would change more than the wanted keys")
    return edited


def multiplier_suffix(data):
    multiplier = data.get("multiplier")
    if isinstance(multiplier, int) and not isinstance(multiplier, bool):
        return f", at {multiplier}x"
    return ""


def config_lines(sim):
    try:
        text = read_config(sim)
        data = None if text is None else parse_config(text)
    except ValueError:
        return [failed(f"{CONFIG} is not valid JSON; delete it, start the simulator once and run: {APPLY}")]
    if data is None:
        return [failed(f"{CONFIG} is not in the simulator folder; start the simulator once with the mod installed, close it and run: {APPLY}")]
    return [
        verdict(
            data.get("followGame") is False,
            f'"followGame" is false in {CONFIG}',
            f'"followGame" is {json.dumps(data.get("followGame"))} in {CONFIG}, so the mod passes on the dynamic mode the simulator asks for and generates no frames; run: {APPLY}',
        ),
        verdict(
            data.get("mode") == "fixed",
            f'"mode" is "fixed" in {CONFIG}{multiplier_suffix(data)}',
            f'"mode" is {json.dumps(data.get("mode"))} in {CONFIG}, and the mod generates frames in the simulator only in fixed mode; run: {APPLY}',
        ),
    ]


def mod_log(sim):
    logs = [path for path in Path(sim).glob(LOG_GLOB) if ".previous" not in path.name]
    return max(logs, key=lambda path: path.stat().st_mtime, default=None)


def log_lines(sim):
    log = mod_log(sim)
    if log is None:
        return []
    text = log.read_bytes().decode("utf-8", errors="replace")
    return [verdict(needle in text, f"{log.name} has {needle}: {good}", f"{log.name} has no {needle}: {bad}") for needle, good, bad in LOG_PROOFS]


def backup_before_first_change(sim, backup):
    backup = Path(backup)
    if (backup / BACKUP_NAME).exists():
        return
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(sim) / CONFIG, backup / BACKUP_NAME)


def apply_config(sim, backup):
    path = Path(sim) / CONFIG
    try:
        text = read_config(sim)
        edited = None if text is None else with_keys(text, WANTED)
    except ValueError as error:
        print(f"{path} cannot be edited: {error}; delete it, start the simulator once and run {APPLY} again", file=sys.stderr)
        return 1
    if text is None:
        print(f"{path} does not exist yet: start the simulator once with the mod installed, close it and run {APPLY} again", file=sys.stderr)
        return 1
    backup_before_first_change(sim, backup)
    if edited != text:
        path.write_bytes(edited.encode("utf-8"))
    print(f'"followGame" is false and "mode" is "fixed" in {CONFIG}; the multiplier is yours, in the mod\'s menu (Backspace) or in the "multiplier" key')
    return 0


def report_launch_options(steam):
    missing = missing_parts(steam)
    if missing is None:
        print("no Steam folder found, so the launch options were not read; run: flightsim-on-linux machine")
    elif not missing:
        print("the launch options of the simulator in Steam hold everything the mod needs")
    else:
        print("Steam keeps the launch options, so nothing here edits them. Add these before %command% in the simulator's properties in Steam:")
        for part in missing:
            print(f"  {part.text}  ({part.reason})")


def apply(sim, steam, backup):
    problem = proxy_problem(sim)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    code = apply_config(sim, backup)
    report_launch_options(steam)
    return code


def check(sim, steam):
    lines = [gpu_line(), proxy_line(sim)] + launch_lines(steam) + config_lines(sim) + log_lines(sim)
    for _, line in lines:
        print(line)
    return 0 if all(good for good, _ in lines) else 1


def generated_files(sim):
    return sorted({path.name for pattern in GENERATED for path in Path(sim).glob(pattern)})


def undo_proxy(sim):
    state, digest = proxy_state(sim)
    if state == "ours":
        (Path(sim) / PROXY).unlink()
        print(f"{PROXY}, the patched MFG Unlocker, is removed from the simulator folder")
    elif state == "foreign":
        print(f"{Path(sim) / PROXY} is not the patched MFG Unlocker (sha256 {digest}), so it stays")
    else:
        print(f"{PROXY} is not in the simulator folder")


def undo_config(sim, backup):
    original = Path(backup) / BACKUP_NAME
    if not original.exists():
        print(f"nothing to restore: apply did not change {CONFIG}")
        return
    shutil.copy2(original, Path(sim) / CONFIG)
    original.unlink()
    print(f"{CONFIG} is as it was before apply")


def undo(sim, backup):
    undo_proxy(sim)
    undo_config(sim, backup)
    left = generated_files(sim)
    if left:
        print("the mod's own files stay in the simulator folder; delete them if you want them gone: " + ", ".join(left))
    print("Steam's launch options are not touched; take version out of WINEDLLOVERRIDES and PROTON_NVIDIA_NVCUDA=1 out yourself if nothing else needs them")
    return 0


def main(argv):
    sizes = {"install": 4, "apply": 4, "check": 3, "undo": 3}
    if not argv or argv[0] not in sizes or len(argv) != sizes[argv[0]]:
        print(USAGE, file=sys.stderr)
        return 2
    action, sim, rest = argv[0], argv[1], argv[2:]
    if not sim:
        print("FAIL no simulator install folder found; run: flightsim-on-linux machine")
        return 1
    if action == "install":
        return install(rest[0], rest[1], sim)
    if action == "apply":
        return apply(sim, rest[0], rest[1])
    if action == "check":
        return check(sim, rest[0])
    return undo(sim, rest[0])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
