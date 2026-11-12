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

RELEASE = "1.7.1-xmx.1"
PROXY = "version.dll"
PROXY_SHA256 = "6a606ef333ad5764b5b379b1486ef10ecf81654d1139a5267812a2ae3af1d001"
ARCHIVE_MEMBER = "MFGUnlocker.dll"
XESS_FOLDER = "RTXMFG-XeSS"
XESS_SDK = "XeSS SDK 3.0.2"
XESS_SDK_FOLDER = "bin"
XESS_FILES = {
    "libxess_fg.dll": "ec5e0c65e075570c6ede72618bb666d0be0c2e10b2ea9762c0fe8cb8e375ab27",
    "libxell.dll": "d2030dcd694fda8f2ec7e044b13e6db8f0b56d4ba9113a5efad334e3f3ded8c7",
}
CONFIG = "RTXMFG-Universal.json"
BACKUP_NAME = f"{CONFIG}.orig"
WANTED = {"followGame": False, "mode": "fixed", "xmx": 1, "xmxXell": 1}
NVIDIA = "10de"
ADA = re.compile(r"\bRTX\s*40\d0\b|\bAda\b", re.IGNORECASE)
LOG_GLOB = "RTXMFG-*.log"
LOG_PROOFS = (
    ("capability=8.9 verified=1", "the mod found the RTX 40 GPU through CUDA", "the mod did not find the RTX 40 GPU through CUDA; check that PROTON_NVIDIA_NVCUDA=1 is in the launch options and start the simulator again"),
    ("MFG_PROXY_UI state=active", "the mod's menu installed", "the mod's menu did not install; the fork's version.dll has to be the one that loaded, and the simulator has to have started since it was installed"),
    ("XMX runtime ", f"the mod loaded Intel's two libraries from {XESS_FOLDER}", f"the mod did not load Intel's two libraries from {XESS_FOLDER}; check that both files are there and start the simulator again"),
)
GENERATED = ("RTXMFG-*", "RTX40MFG-UI*.ini")
APPLY = "flightsim-on-linux addon apply mfg-unlocker"
INSTALL = "flightsim-on-linux addon install mfg-unlocker"
USAGE = "usage: mfg.py install <simulator folder> <zip> <zip sha256> <XeSS SDK zip> <SDK sha256> | apply <simulator folder> <Steam folder> <backup folder> | check <simulator folder> <Steam folder> | undo <simulator folder> <backup folder>"


def discrete_module():
    spec = importlib.util.spec_from_file_location("discrete_gpu", ROOT / "tools" / "discrete-gpu.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


discrete = discrete_module()


class InstallError(Exception):
    pass


class LaunchPart(NamedTuple):
    name: str
    text: str
    reason: str
    holds: Callable[[dict], bool]


class Artifact(NamedTuple):
    name: str
    path: Path
    sha256: str
    label: str


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def artifacts(sim):
    proxy = Artifact(PROXY, Path(sim) / PROXY, PROXY_SHA256, f"the MFG Unlocker fork {RELEASE}")
    runtime = [Artifact(f"{XESS_FOLDER}/{name}", Path(sim) / XESS_FOLDER / name, digest, f"Intel's {name} from the {XESS_SDK}") for name, digest in XESS_FILES.items()]
    return proxy, runtime


def artifact_state(artifact):
    if not artifact.path.is_file():
        return "absent", None
    digest = sha256(artifact.path.read_bytes())
    return ("ours" if digest == artifact.sha256 else "foreign"), digest


def artifact_problem(artifact):
    state, digest = artifact_state(artifact)
    if state == "absent":
        return f"{artifact.path} is not there; run: {INSTALL}"
    if state == "foreign":
        return f"{artifact.path} is not {artifact.label} (sha256 {digest}); another mod may use that name, and {INSTALL} does not overwrite it"
    return None


def installation_problem(sim):
    proxy, runtime = artifacts(sim)
    return next(filter(None, map(artifact_problem, [proxy, *runtime])), None)


def archive_problem(archive, archive_sha, source=""):
    try:
        archive_bytes = Path(archive).read_bytes()
    except OSError as error:
        return f"cannot read {archive}: {error}"
    if sha256(archive_bytes) != archive_sha:
        return f"{archive} is not the release this add-on was tested with ({source}sha256 {archive_sha}); nothing was installed"
    return None


def archive_member(archive, member, wanted_sha):
    try:
        with zipfile.ZipFile(archive) as bundle:
            data = bundle.read(member)
    except (zipfile.BadZipFile, KeyError, OSError) as error:
        raise InstallError(f"{member} cannot be read from {archive} ({error!r})") from error
    if sha256(data) != wanted_sha:
        raise InstallError(f"{member} in {archive} has sha256 {sha256(data)}, not the {wanted_sha} this add-on was tested with")
    return data


def write_file(path, data):
    part = path.with_name(f"{path.name}.part")
    part.write_bytes(data)
    os.replace(part, path)


def foreign_files(targets):
    states = [(target, *artifact_state(target)) for target in targets]
    return [(target, digest) for target, state, digest in states if state == "foreign"]


def images_to_write(archive, sdk, proxy, runtime):
    wanted = [(proxy, archive, ARCHIVE_MEMBER)] + [(target, sdk, f"{XESS_SDK_FOLDER}/{target.path.name}") for target in runtime]
    return [(target, archive_member(source, member, target.sha256)) for target, source, member in wanted if artifact_state(target)[0] == "absent"]


def install(archive, archive_sha, sdk, sdk_sha, sim):
    problem = archive_problem(archive, archive_sha) or archive_problem(sdk, sdk_sha, f"the {XESS_SDK}, ")
    if problem:
        print(problem, file=sys.stderr)
        return 1
    proxy, runtime = artifacts(sim)
    for target, digest in foreign_files([proxy, *runtime]):
        print(f"{target.path} is not {target.label} (sha256 {digest}); nothing was installed. Move it away first, if you want ours in its place", file=sys.stderr)
        return 1
    try:
        pending = images_to_write(archive, sdk, proxy, runtime)
    except InstallError as error:
        print(f"{error}; the files cannot be installed and nothing was installed", file=sys.stderr)
        return 1
    if not pending:
        print(f"the MFG Unlocker fork {RELEASE} and Intel's two XeSS files are already in {sim}; next: {APPLY}")
        return 0
    (Path(sim) / XESS_FOLDER).mkdir(exist_ok=True)
    proxy_last = sorted(pending, key=lambda item: item[0] is proxy)
    for target, image in proxy_last:
        write_file(target.path, image)
    if not (Path(sim) / "dxgi.dll").is_file():
        print("note: ReShade's dxgi.dll is not in the simulator folder, and this build was only tested with ReShade present")
    print(f"the MFG Unlocker fork {RELEASE} is in {sim} as {PROXY}, with Intel's libxess_fg.dll and libxell.dll in {XESS_FOLDER}; next: {APPLY}")
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


def artifact_line(artifact):
    problem = artifact_problem(artifact)
    if problem:
        return failed(problem)
    return passed(f"{artifact.name} in the simulator folder is {artifact.label}")


def installation_lines(sim):
    proxy, runtime = artifacts(sim)
    return [artifact_line(artifact) for artifact in [proxy, *runtime]]


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
    LaunchPart("PROTON_DLSS_UPGRADE", "PROTON_DLSS_UPGRADE=1", "without it Proton does not put the newest DLSS frame generation in the prefix, and the mod was tested with that copy", lambda env: env.get("PROTON_DLSS_UPGRADE") == "1"),
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


def is_number(value, wanted=None):
    return isinstance(value, int) and not isinstance(value, bool) and (wanted is None or value == wanted)


def multiplier_suffix(data):
    multiplier = data.get("multiplier")
    if is_number(multiplier):
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
        verdict(
            is_number(data.get("xmx"), 1),
            f'"xmx" is 1 in {CONFIG}: Intel\'s XeFG draws the generated frames',
            f'"xmx" is {json.dumps(data.get("xmx"))} in {CONFIG}, so NVIDIA\'s generator makes the frames and Intel\'s XeFG is not used; run: {APPLY}',
        ),
        verdict(
            is_number(data.get("xmxXell"), 1),
            f'"xmxXell" is 1 in {CONFIG}: the game\'s Reflex sleep goes to XeLL',
            f'"xmxXell" is {json.dumps(data.get("xmxXell"))} in {CONFIG}, so the game\'s Reflex sleep does not go to XeLL, and with VK_NV_low_latency2 on it took a 30 fps base down to about 23 fps on the test machine; run: {APPLY}',
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
    print(f'"followGame" is false, "mode" is "fixed", "xmx" is 1 and "xmxXell" is 1 in {CONFIG}; the multiplier is yours, in the mod\'s menu (Backspace) or in the "multiplier" key. "xmx" takes effect at the next start of the simulator')
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
    problem = installation_problem(sim)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    code = apply_config(sim, backup)
    report_launch_options(steam)
    return code


def check(sim, steam):
    lines = [gpu_line()] + installation_lines(sim) + launch_lines(steam) + config_lines(sim) + log_lines(sim)
    for _, line in lines:
        print(line)
    return 0 if all(good for good, _ in lines) else 1


def generated_files(sim):
    return sorted({path.name for pattern in GENERATED for path in Path(sim).glob(pattern) if path.is_file()})


def undo_artifact(artifact):
    state, digest = artifact_state(artifact)
    if state == "ours":
        artifact.path.unlink()
        print(f"{artifact.name}, {artifact.label}, is removed from the simulator folder")
    elif state == "foreign":
        print(f"{artifact.path} is not {artifact.label} (sha256 {digest}), so it stays")
    else:
        print(f"{artifact.name} is not in the simulator folder")


def undo_installation(sim):
    proxy, runtime = artifacts(sim)
    for artifact in [proxy, *runtime]:
        undo_artifact(artifact)
    folder = Path(sim) / XESS_FOLDER
    if folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()


def undo_config(sim, backup):
    original = Path(backup) / BACKUP_NAME
    if not original.exists():
        print(f"nothing to restore: apply did not change {CONFIG}")
        return
    shutil.copy2(original, Path(sim) / CONFIG)
    original.unlink()
    print(f"{CONFIG} is as it was before apply")


def undo(sim, backup):
    undo_installation(sim)
    undo_config(sim, backup)
    left = generated_files(sim)
    if left:
        print("the mod's own files stay in the simulator folder; delete them if you want them gone: " + ", ".join(left))
    print("Steam's launch options are not touched; take version out of WINEDLLOVERRIDES and PROTON_NVIDIA_NVCUDA=1 out yourself if nothing else needs them")
    return 0


def main(argv):
    sizes = {"install": 6, "apply": 4, "check": 3, "undo": 3}
    if not argv or argv[0] not in sizes or len(argv) != sizes[argv[0]]:
        print(USAGE, file=sys.stderr)
        return 2
    action, sim, rest = argv[0], argv[1], argv[2:]
    if not sim:
        print("FAIL no simulator install folder found; run: flightsim-on-linux machine")
        return 1
    if action == "install":
        return install(rest[0], rest[1], rest[2], rest[3], sim)
    if action == "apply":
        return apply(sim, rest[0], rest[1])
    if action == "check":
        return check(sim, rest[0])
    return undo(sim, rest[0])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
