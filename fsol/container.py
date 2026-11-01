import os
import re
from pathlib import Path

from fsol import machine as machines

DROPPED = ("WINESERVERSOCKET", "LD_PRELOAD")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
VOICE_FILES = Path("steamapps/common/Proton Voice Files")
XAUTHORITY_COPY = "xauthority-desktop"


class ContainerError(Exception):
    pass


def split_assignments(args):
    rest = list(args)
    assignments = {}
    while rest and ASSIGNMENT.match(rest[0]):
        key, value = rest.pop(0).split("=", 1)
        assignments[key] = value
    return assignments, rest


def visible_in(root, path):
    return (root / str(path).lstrip("/")).exists()


def desktop_xauthority(cookie, sim_root, compatdata):
    if not cookie or visible_in(sim_root, cookie):
        return cookie
    copy = compatdata / XAUTHORITY_COPY
    descriptor = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as target, open(cookie, "rb") as source:
        target.write(source.read())
    os.chmod(copy, 0o600)
    if not visible_in(sim_root, copy):
        raise ContainerError(f"the simulator's container sees neither {cookie} nor {copy}")
    return str(copy)


def join_display(env, caller_env, sim_root, compatdata):
    display = caller_env.get("DISPLAY")
    if not display or env.get("DISPLAY") == display:
        return
    env["DISPLAY"] = display
    cookie = desktop_xauthority(caller_env.get("XAUTHORITY"), sim_root, compatdata)
    if cookie:
        env["XAUTHORITY"] = cookie
    else:
        env.pop("XAUTHORITY", None)


def voice_files(libraries):
    return next((str(library / VOICE_FILES) for library in libraries if (library / VOICE_FILES).is_dir()), None)


def environment(sim_env, caller_env, found, sim_root):
    env = {key: value for key, value in sim_env.items() if key not in DROPPED}
    env["WINEDEBUG"] = caller_env.get("WINEDEBUG", "-all")
    join_display(env, caller_env, sim_root, found.compatdata)
    if "PROTON_VOICE_FILES" not in env and voice_files(found.libraries):
        env["PROTON_VOICE_FILES"] = voice_files(found.libraries)
    if caller_env.get("EXTRA_WINEDLLOVERRIDES"):
        env["WINEDLLOVERRIDES"] = ";".join(filter(None, [env.get("WINEDLLOVERRIDES"), caller_env["EXTRA_WINEDLLOVERRIDES"]]))
    return env


def require_simulator(found):
    if not found.sim_pid:
        raise ContainerError("the simulator is not running; start it from Steam first")


def simulator_environ(found, proc):
    require_simulator(found)
    pid_dir = proc / str(found.sim_pid)
    sim_env = machines.process_environ(pid_dir)
    if not sim_env:
        raise ContainerError(f"cannot read the environment of the simulator (pid {found.sim_pid})")
    return pid_dir, sim_env


def simulator_values(found, names, proc=Path("/proc")):
    _, sim_env = simulator_environ(found, proc)
    return {name: sim_env[name] for name in names if name in sim_env}


def plan(found, args, caller_env, proc=Path("/proc")):
    require_simulator(found)
    if not found.proton:
        raise ContainerError("the Proton the simulator runs with was not found in its config_info")
    assignments, program = split_assignments(args)
    if not program:
        raise ContainerError("no program given")
    pid_dir, sim_env = simulator_environ(found, proc)
    env = environment(sim_env, caller_env, found, pid_dir / "root")
    env.update(assignments)
    wine = found.proton / "files" / "bin" / "wine"
    return ["nsenter", "-t", str(found.sim_pid), "-U", "-m", "--preserve-credentials", str(wine), *program], env
