import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from fsol import addons, machine, simconnect

ROOT = Path(__file__).resolve().parent.parent
READER_SCRIPT = ROOT / "tools" / "aircraft-loaded.mjs"
DEFAULT_GRACE = 20
DEFAULT_POLL = 8
RESTART_CAP = 3
READER_RESTART = 8
READER_RETRY = 60
MODULE_MISSING = 3
OPEN_LINE = "#open"
SCRIPT_TIMEOUT = 120
LISTEN = "0A"
ESTABLISHED = "01"
KILL_ON_STOP = signal.SIGTERM


@dataclass(frozen=True)
class Config:
    app: str
    script: str = "launch"
    args: tuple = ()
    grace: float = DEFAULT_GRACE
    poll: float = DEFAULT_POLL
    wait_for: dict | None = None
    trigger: str = "simulator"
    aircraft: str | None = None
    stop: str = "never"
    health: dict | None = None

    @property
    def port(self):
        health = self.health or {}
        return health.get("listening") or health.get("connected_to")


def config_of(data):
    trigger = data.get("trigger", "simulator")
    aircraft = trigger["aircraft"] if isinstance(trigger, dict) else None
    return Config(
        app=data["app"],
        script=data.get("script", "launch"),
        args=tuple(data.get("args", ())),
        grace=data.get("grace", DEFAULT_GRACE),
        poll=data.get("poll", DEFAULT_POLL),
        wait_for=data.get("wait_for"),
        trigger="aircraft" if aircraft else trigger,
        aircraft=aircraft,
        stop=data.get("stop", "never"),
        health=data.get("health"),
    )


@dataclass(frozen=True)
class Seen:
    now: float
    sim: int | None = None
    app: bool = False
    waited: bool = False
    aircraft: tuple = ()
    listening: bool = False
    connected: bool = False


@dataclass(frozen=True)
class Memory:
    sim: int | None = None
    since: float = 0.0
    handled: bool = False
    started: bool = False
    waiting_since: float | None = None
    lost_since: float | None = None
    restarts: int = 0
    gave_up: bool = False


def stops_with_simulator(config, memory):
    if config.stop == "with_simulator":
        return True
    return config.stop in ("with_simulator_if_started", "on_other_aircraft") and memory.started


def simulator_changed(config, memory, seen):
    actions = []
    stopped = False
    if memory.sim is not None:
        actions.append(("log", f"simulator {memory.sim} closed"))
        if seen.app and stops_with_simulator(config, memory):
            actions += [("log", "closing the program the simulator no longer needs"), ("stop",)]
            stopped = True
    if seen.sim is not None:
        actions.append(("log", f"simulator {seen.sim} running"))
    return Memory(sim=seen.sim, since=seen.now), actions, replace(seen, app=seen.app and not stopped)


def already_up(config, seen):
    return seen.app or bool(config.health and "listening" in config.health and seen.listening)


def wait_actions(config, memory, seen):
    waiting = seen.now if memory.waiting_since is None else memory.waiting_since
    process = config.wait_for["process"]
    if seen.waited:
        return replace(memory, waiting_since=waiting), True, []
    if seen.now - waiting >= config.wait_for["timeout"]:
        return replace(memory, waiting_since=waiting), True, [("log", f"{process} did not appear in {config.wait_for['timeout']:g} s; starting anyway")]
    first = [] if memory.waiting_since is not None else [("log", f"waiting for {process}")]
    return replace(memory, waiting_since=waiting), False, first


def start_with_simulator(config, memory, seen):
    if memory.handled:
        return memory, []
    if already_up(config, seen):
        return replace(memory, handled=True), [("log", "the program is already running; leaving it alone")]
    if seen.now - memory.since < config.grace:
        return memory, []
    ready = True
    notes = []
    if config.wait_for:
        memory, ready, notes = wait_actions(config, memory, seen)
    if not ready:
        return memory, notes
    return replace(memory, handled=True, started=True), notes + [("log", "starting the program"), ("run", config.script, config.args)]


def start_with_aircraft(config, memory, seen):
    actions = []
    up = seen.app
    started = memory.started
    for aircraft in seen.aircraft:
        if re.search(config.aircraft, aircraft, re.IGNORECASE):
            if not up:
                actions += [("log", f"{aircraft}: starting the program"), ("run", config.script, config.args)]
                up = started = True
        elif config.stop == "on_other_aircraft" and up:
            actions += [("log", f"{aircraft}: closing the program"), ("stop",)]
            up = False
    return replace(memory, started=started), actions


def start_actions(config, memory, seen):
    if config.trigger == "aircraft":
        return start_with_aircraft(config, memory, seen)
    if config.trigger == "simulator":
        return start_with_simulator(config, memory, seen)
    return memory, []


def lost_actions(config, memory, seen):
    since = seen.now if memory.lost_since is None else memory.lost_since
    first = [] if memory.lost_since is not None else [("log", f"the program is not connected to port {config.health['connected_to']}")]
    memory = replace(memory, lost_since=since)
    if seen.now - since < config.health["lost_for"]:
        return memory, first
    if memory.restarts >= RESTART_CAP:
        return replace(memory, gave_up=True), [] if memory.gave_up else [("log", f"restarted {RESTART_CAP} times without a connection; giving up until the simulator restarts")]
    restart = config.health["restart"]
    return replace(memory, lost_since=None, restarts=memory.restarts + 1), [("log", f"not connected for {config.health['lost_for']:g} s; running {restart}"), ("run", restart, ())]


def health_actions(config, memory, seen):
    if not config.health or "connected_to" not in config.health:
        return memory, []
    if not (seen.app and seen.listening and not seen.connected):
        return replace(memory, lost_since=None), []
    return lost_actions(config, memory, seen)


def step(config, memory, seen):
    actions = []
    if seen.sim != memory.sim:
        memory, actions, seen = simulator_changed(config, memory, seen)
    if seen.sim is None:
        return memory, actions
    memory, started = start_actions(config, memory, seen)
    memory, health = health_actions(config, memory, seen)
    return memory, actions + started + health


def scan(proc, ignore=None):
    ignore = os.getpid() if ignore is None else ignore
    found = []
    for pid_dir in proc.glob("[0-9]*"):
        try:
            raw = (pid_dir / "cmdline").read_bytes()
        except OSError:
            continue
        if int(pid_dir.name) != ignore:
            found.append((pid_dir, raw.replace(b"\0", b" ").decode(errors="replace").strip()))
    return found


def matching(pattern, processes):
    return [pid_dir for pid_dir, command in processes if re.search(pattern, command)]


def tcp_rows(proc):
    rows = []
    for name in ("tcp", "tcp6"):
        try:
            lines = (proc / "net" / name).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) > 9:
                rows.append((int(parts[1].rsplit(":", 1)[1], 16), int(parts[2].rsplit(":", 1)[1], 16), parts[3], parts[9]))
    return rows


def socket_owners(inodes, proc):
    wanted = {f"socket:[{inode}]" for inode in inodes}
    owners = set()
    for pid_dir in proc.glob("[0-9]*"):
        try:
            targets = {os.readlink(fd) for fd in (pid_dir / "fd").glob("*")}
        except OSError:
            continue
        if targets & wanted:
            owners.add(pid_dir)
    return owners


def wineservers_of(apps, proc):
    prefixes = {os.path.realpath(winepfx) for winepfx in (machine.process_environ(pid_dir).get("WINEPREFIX") for pid_dir in apps) if winepfx}
    servers = set()
    for comm in proc.glob("[0-9]*/comm"):
        try:
            named = comm.read_text().strip() == "wineserver"
        except OSError:
            continue
        if named and any(machine.in_prefix(comm.parent, prefix) for prefix in prefixes):
            servers.add(comm.parent)
    return servers


def is_connected(port, apps, proc, rows):
    inodes = {inode for _, remote, state, inode in rows if remote == port and state == ESTABLISHED}
    if not inodes or not apps:
        return False
    return bool(socket_owners(inodes, proc) & (set(apps) | wineservers_of(apps, proc)))


def observe(config, proc, now, aircraft=()):
    processes = scan(proc)
    apps = matching(config.app, processes)
    rows = tcp_rows(proc) if config.port else []
    listening = any(local == config.port and state == LISTEN for local, _, state, _ in rows)
    connected = bool(config.health and "connected_to" in config.health and apps and is_connected(config.port, apps, proc, rows))
    return Seen(
        now=now,
        sim=machine.sim_pid(proc),
        app=bool(apps),
        waited=bool(config.wait_for and matching(config.wait_for["process"], processes)),
        aircraft=tuple(aircraft),
        listening=listening,
        connected=connected,
    )


class ReaderUnavailable(Exception):
    pass


def reader_command(found, environ=os.environ):
    if not shutil.which("node"):
        raise ReaderUnavailable("node is not installed; the aircraft trigger needs Node.js")
    port = simconnect.port(found)
    if port is None:
        raise ReaderUnavailable("no static SimConnect port found; run: flightsim-on-linux addon apply simconnect-tcp")
    if port < simconnect.PRIVILEGED_BELOW:
        raise ReaderUnavailable(f"SimConnect.xml has port {port}, which Linux will not open for the simulator; run: flightsim-on-linux addon apply simconnect-tcp")
    return ["node", str(READER_SCRIPT), str(port), str(addons.cache_folder(environ) / "simconnect")]


class AircraftReader:
    def __init__(self, command, say, clock=time.monotonic, restart=READER_RESTART, retry=READER_RETRY):
        self.command = command
        self.say = say
        self.clock = clock
        self.restart = restart
        self.retry = retry
        self.process = None
        self.lines = queue.Queue()
        self.state = None
        self.next_try = 0.0

    def note(self, state, message):
        if state != self.state:
            self.say(message)
            self.state = state

    def poll(self, wanted):
        if not wanted:
            self.stop()
        elif self.process is None and self.clock() >= self.next_try:
            self.start()
        elif self.process is not None and self.process.poll() is not None:
            self.exited(self.process.returncode)
        return self.drain()

    def drain(self):
        found = []
        while not self.lines.empty():
            line = self.lines.get()
            if line == OPEN_LINE:
                self.note("open", "connected to SimConnect, reading the loaded aircraft")
            else:
                found.append(line)
        return tuple(found)

    def pump(self, process):
        with process.stdout:
            for line in process.stdout:
                self.lines.put(line.rstrip("\r\n"))

    def start(self):
        try:
            argv = self.command()
        except ReaderUnavailable as problem:
            self.next_try = self.clock() + self.retry
            return self.note(f"unavailable: {problem}", f"aircraft trigger unavailable: {problem}; trying again every {self.retry:g} s")
        self.process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, errors="replace")
        threading.Thread(target=self.pump, args=(self.process,), daemon=True).start()

    def exited(self, code):
        self.process = None
        if code == MODULE_MISSING:
            self.next_try = self.clock() + self.retry
            return self.note("module", f"node-simconnect is missing; run: flightsim-on-linux addon apply simconnect-tcp; trying again every {self.retry:g} s")
        self.next_try = self.clock() + self.restart
        what = "closed the connection" if code == 0 else "is not answering"
        self.note(what, f"SimConnect {what} (reader exit {code}); trying again every {self.restart:g} s")

    def stop(self):
        process, self.process = self.process, None
        self.state = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()
        self.drain()


def say_line(text):
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


class Watcher:
    def __init__(self, addon, found, cli, discover, proc, say):
        self.addon = addon
        self.found = found
        self.cli = cli
        self.discover = discover
        self.proc = proc
        self.say = say
        self.sim = None

    def machine_now(self):
        base = self.discover() if self.discover else self.found
        return replace(base, sim_pid=self.sim, sim_running=self.sim is not None)

    def run_script(self, name, args):
        try:
            code = addons.call_script(self.addon, name, self.machine_now(), self.cli, args, drop=addons.WINE_UNFRIENDLY, stdin=subprocess.DEVNULL, timeout=SCRIPT_TIMEOUT)
        except subprocess.TimeoutExpired:
            return self.say(f"{name} was still running after {SCRIPT_TIMEOUT} s; moving on")
        if code:
            self.say(f"{name} exited with code {code}")

    def stop_program(self):
        if self.addon.has_script("close"):
            return self.run_script("close", ())
        for pid_dir in matching(self.addon.watcher["app"], scan(self.proc)):
            try:
                os.kill(int(pid_dir.name), KILL_ON_STOP)
            except ProcessLookupError:
                continue

    def carry_out(self, actions):
        for action in actions:
            if action[0] == "log":
                self.say(action[1])
            elif action[0] == "run":
                self.run_script(action[1], action[2])
            else:
                self.stop_program()


def run(addon, found, cli, discover=None, proc=Path("/proc"), clock=time.monotonic, sleep=time.sleep, reader=None, say=say_line, should_stop=lambda: False):
    config = config_of(addon.watcher)
    reader = reader or AircraftReader(lambda: reader_command(found), say, clock)
    watcher = Watcher(addon, found, cli, discover, proc, say)
    memory = Memory()
    say(f"watching for {addon.name}")
    try:
        while not should_stop():
            sim = machine.sim_pid(proc)
            lines = reader.poll(config.trigger == "aircraft" and sim is not None)
            seen = observe(config, proc, clock(), lines)
            watcher.sim = seen.sim
            memory, actions = step(config, memory, seen)
            watcher.carry_out(actions)
            sleep(config.poll)
    finally:
        reader.stop()
    return 0


def unit_name(addon):
    return f"flightsim-on-linux-{addon.id}.service"


def unit_path(addon, config_home):
    return config_home / "systemd" / "user" / unit_name(addon)


def has_unit(addon, config_home):
    return unit_path(addon, config_home).is_file()


def systemd_quote(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def has_loop(addon):
    return "loop" in (addon.watcher or {})


def unit_command(addon, cli):
    if has_loop(addon):
        return f"{systemd_quote(cli)} addon launch {addon.id} {addon.watcher['loop']}"
    return f"{systemd_quote(cli)} addon watch {addon.id}"


def unit_service(addon, cli):
    service = [f"ExecStart={unit_command(addon, cli)}", "Restart=on-failure", "RestartSec=5"]
    return service if has_loop(addon) else [*service, "KillMode=process"]


def unit_text(addon, cli):
    service = unit_service(addon, cli)
    lines = ["[Unit]", f"Description=Flightsim on Linux watcher for {addon.name}", "", "[Service]", *service, "", "[Install]", "WantedBy=default.target"]
    return "\n".join(lines) + "\n"


def systemctl_user(*args):
    return subprocess.run(["systemctl", "--user", *args], stdin=subprocess.DEVNULL).returncode


def install_unit(addon, cli, config_home, systemctl=None):
    path = unit_path(addon, config_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit_text(addon, cli))
    return (systemctl or systemctl_user)("enable", "--now", unit_name(addon))


def remove_unit(addon, config_home, systemctl=None):
    systemctl = systemctl or systemctl_user
    path = unit_path(addon, config_home)
    code = systemctl("disable", "--now", unit_name(addon)) if path.is_file() else 0
    path.unlink(missing_ok=True)
    systemctl("daemon-reload")
    return code
