import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fsol import addons, machine, watcher
from tests.support import start_stand_in
from tests.test_machine import FakeMachine, write

SIM_COMMAND = "Q:\\steamapps\\common\\MSFS2024\\FlightSimulator2024.exe"
APP_COMMAND = "C:\\Program Files\\Demo\\Demo.exe"
APP = r"^C:.*\\Demo\.exe"
SPEC = {
    "name": "Demo Add-on",
    "tested_version": "1.2.3",
    "tested_on": "2026-10-17",
    "needs": [],
    "touches_addon_files": False,
    "installed": {"community": ["demo-addon-*"]},
}


def config(**options):
    return watcher.config_of(dict({"app": APP}, **options))


def sees(now, sim=None, **fields):
    return watcher.Seen(now=now, sim=sim, **fields)


def drive(cfg, timeline):
    memory = watcher.Memory()
    history = []
    for seen in timeline:
        memory, actions = watcher.step(cfg, memory, seen)
        history.append(actions)
    return history


def runs(history):
    return [action for actions in history for action in actions if action[0] == "run"]


def stops(history):
    return [action for actions in history for action in actions if action[0] == "stop"]


def logs(history):
    return [action[1] for actions in history for action in actions if action[0] == "log"]


def ticks(start, stop, every=8, **fields):
    return [sees(now, **fields) for now in range(start, stop, every)]


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        cfg = watcher.config_of({"app": APP})
        self.assertEqual((cfg.script, cfg.args, cfg.grace, cfg.poll, cfg.trigger, cfg.stop, cfg.wait_for, cfg.health), ("launch", (), 20, 8, "simulator", "never", None, None))

    def test_an_aircraft_trigger_keeps_its_regex(self):
        cfg = config(trigger={"aircraft": "maddog"})
        self.assertEqual((cfg.trigger, cfg.aircraft), ("aircraft", "maddog"))

    def test_the_port_comes_from_the_health_option(self):
        self.assertEqual(config(health={"listening": 8651}).port, 8651)
        self.assertEqual(config(health={"connected_to": 5111, "lost_for": 60, "restart": "restart"}).port, 5111)
        self.assertIsNone(config().port)


class StartWithTheSimulatorTest(unittest.TestCase):
    def test_autofps_waits_out_the_grace_then_starts_once_per_simulator(self):
        cfg = config(grace=20, stop="with_simulator")
        history = drive(cfg, ticks(0, 80, sim=100))
        self.assertEqual(runs(history), [("run", "launch", ())])
        first = next(number for number, actions in enumerate(history) if any(action[0] == "run" for action in actions))
        self.assertEqual(first * 8, 24)

    def test_vraas_passes_its_arguments_on(self):
        history = drive(config(grace=20, args=["-autostart"], stop="with_simulator"), ticks(0, 40, sim=100))
        self.assertEqual(runs(history), [("run", "launch", ("-autostart",))])

    def test_volanta_with_no_grace_starts_on_the_first_look_and_never_stops(self):
        history = drive(config(grace=0, args=["--minimized"]), ticks(0, 40, sim=100) + ticks(40, 80))
        self.assertEqual(runs(history), [("run", "launch", ("--minimized",))])
        self.assertEqual(stops(history), [])

    def test_a_program_that_is_already_running_is_left_alone_for_that_simulator(self):
        history = drive(config(grace=0), ticks(0, 40, sim=100, app=True))
        self.assertEqual(runs(history), [])

    def test_a_program_closed_by_hand_is_not_started_again_for_the_same_simulator(self):
        history = drive(config(grace=0), ticks(0, 24, sim=100) + ticks(24, 80, sim=100, app=True) + ticks(80, 160, sim=100))
        self.assertEqual(len(runs(history)), 1)

    def test_a_program_that_appears_during_the_grace_is_not_started_over(self):
        timeline = ticks(0, 16, sim=100) + ticks(16, 80, sim=100, app=True)
        self.assertEqual(runs(drive(config(grace=20), timeline)), [])

    def test_a_new_simulator_gets_its_own_start(self):
        timeline = ticks(0, 40, sim=100) + ticks(40, 80) + ticks(80, 120, sim=200)
        self.assertEqual(len(runs(drive(config(grace=0), timeline))), 2)

    def test_a_simulator_that_dies_during_the_grace_never_starts_the_program(self):
        history = drive(config(grace=20, stop="with_simulator_if_started"), ticks(0, 16, sim=100) + ticks(16, 120))
        self.assertEqual(runs(history), [])
        self.assertEqual(stops(history), [])

    def test_a_simulator_that_dies_while_waiting_for_a_process_never_starts_the_program(self):
        cfg = config(grace=0, wait_for={"process": "couatl", "timeout": 300})
        history = drive(cfg, ticks(0, 24, sim=100) + ticks(24, 400))
        self.assertEqual(runs(history), [])

    def test_the_trigger_none_never_starts_anything(self):
        self.assertEqual(runs(drive(config(grace=0, trigger="none"), ticks(0, 80, sim=100))), [])


class WaitForTest(unittest.TestCase):
    CFG = {"grace": 0, "wait_for": {"process": "couatl64", "timeout": 300}, "stop": "with_simulator_if_started"}

    def test_the_program_starts_once_the_process_it_waits_for_is_there(self):
        timeline = ticks(0, 32, sim=100) + ticks(32, 80, sim=100, waited=True)
        history = drive(config(**self.CFG), timeline)
        self.assertEqual(len(runs(history)), 1)
        first = next(number for number, actions in enumerate(history) if any(action[0] == "run" for action in actions))
        self.assertEqual(first * 8, 32)

    def test_after_the_timeout_it_starts_anyway_and_says_so(self):
        history = drive(config(**self.CFG), ticks(0, 330, sim=100))
        self.assertEqual(len(runs(history)), 1)
        self.assertEqual(len([line for line in logs(history) if "did not appear in 300 s" in line]), 1)
        first = next(number for number, actions in enumerate(history) if any(action[0] == "run" for action in actions))
        self.assertEqual(first * 8, 304)

    def test_the_wait_is_said_once(self):
        history = drive(config(**self.CFG), ticks(0, 200, sim=100))
        self.assertEqual(len([line for line in logs(history) if line.startswith("waiting for")]), 1)

    def test_the_wait_starts_after_the_grace(self):
        cfg = config(grace=20, wait_for={"process": "couatl64", "timeout": 40})
        history = drive(cfg, ticks(0, 100, sim=100))
        first = next(number for number, actions in enumerate(history) if any(action[0] == "run" for action in actions))
        self.assertEqual(first * 8, 64)


class StopTest(unittest.TestCase):
    def test_with_simulator_stops_what_runs_when_the_simulator_closes(self):
        history = drive(config(grace=0, stop="with_simulator"), ticks(0, 24, sim=100) + ticks(24, 48, app=True))
        self.assertEqual(len(stops(history)), 1)

    def test_with_simulator_does_nothing_when_the_program_is_already_gone(self):
        history = drive(config(grace=0, stop="with_simulator"), ticks(0, 24, sim=100, app=True) + ticks(24, 200))
        self.assertEqual(stops(history), [])

    def test_with_simulator_stops_once_not_on_every_look(self):
        history = drive(config(grace=0, stop="with_simulator"), ticks(0, 24, sim=100, app=True) + ticks(24, 400, app=True))
        self.assertEqual(len(stops(history)), 1)

    def test_with_simulator_if_started_stops_only_what_it_started(self):
        mine = drive(config(grace=0, stop="with_simulator_if_started"), ticks(0, 24, sim=100) + ticks(24, 48, app=True))
        yours = drive(config(grace=0, stop="with_simulator_if_started"), ticks(0, 24, sim=100, app=True) + ticks(24, 48, app=True))
        self.assertEqual((len(stops(mine)), len(stops(yours))), (1, 0))

    def test_never_leaves_it_running(self):
        history = drive(config(grace=0, stop="never"), ticks(0, 24, sim=100) + ticks(24, 200, app=True))
        self.assertEqual(stops(history), [])

    def test_a_simulator_replaced_between_two_looks_stops_the_old_program_and_starts_one_for_the_new(self):
        cfg = config(grace=20, stop="with_simulator")
        timeline = ticks(0, 32, sim=100) + [sees(32, sim=200, app=True)] + ticks(40, 100, sim=200)
        history = drive(cfg, timeline)
        self.assertEqual(len(stops(history)), 1)
        self.assertEqual(len(runs(history)), 2)
        replaced = next(number for number, actions in enumerate(history) if any(action[0] == "stop" for action in actions))
        self.assertEqual(replaced, 4)

    def test_a_simulator_that_dies_during_the_grace_with_a_program_somebody_started_still_stops_it(self):
        history = drive(config(grace=20, stop="with_simulator"), ticks(0, 8, sim=100, app=True) + ticks(8, 40, app=True))
        self.assertEqual(len(stops(history)), 1)


class AircraftTest(unittest.TestCase):
    FSL = {"trigger": {"aircraft": r"airplanes\\fslabs a3"}, "stop": "with_simulator"}
    MADDOG = {"trigger": {"aircraft": r"airplanes\\maddog"}, "stop": "on_other_aircraft"}

    def test_fsl_starts_on_a_matching_aircraft_without_regard_to_case_and_not_on_another(self):
        cfg = config(**self.FSL)
        other = drive(cfg, [sees(0, sim=100), sees(8, sim=100, aircraft=("Airplanes\\Asobo_TBM930",))])
        match = drive(cfg, [sees(0, sim=100), sees(8, sim=100, aircraft=("Airplanes\\FSLabs A321",))])
        self.assertEqual((runs(other), len(runs(match))), ([], 1))

    def test_fsl_does_not_start_it_twice_while_it_runs(self):
        history = drive(config(**self.FSL), [sees(0, sim=100), sees(8, sim=100, aircraft=("Airplanes\\FSLabs A321",)), sees(16, sim=100, app=True, aircraft=("Airplanes\\FSLabs A321",))])
        self.assertEqual(len(runs(history)), 1)

    def test_fsl_keeps_the_program_on_another_aircraft_and_stops_it_with_the_simulator(self):
        timeline = [sees(0, sim=100), sees(8, sim=100, app=True, aircraft=("Airplanes\\FSLabs A321",)), sees(16, sim=100, app=True, aircraft=("Airplanes\\Asobo_TBM930",)), sees(24, app=True)]
        history = drive(config(**self.FSL), timeline)
        self.assertEqual(len(stops(history)), 1)
        self.assertEqual(sum(1 for actions in history[:3] for action in actions if action[0] == "stop"), 0)

    def test_the_aircraft_trigger_ignores_the_grace(self):
        history = drive(config(grace=999, **self.FSL), [sees(0, sim=100, aircraft=("Airplanes\\FSLabs A320",))])
        self.assertEqual(len(runs(history)), 1)

    def test_mdclient_starts_on_the_maddog_and_stops_on_another_aircraft(self):
        timeline = [
            sees(0, sim=100),
            sees(8, sim=100, aircraft=("Airplanes\\Maddog X",)),
            sees(16, sim=100, app=True),
            sees(24, sim=100, app=True, aircraft=("Airplanes\\Asobo_TBM930",)),
            sees(32, sim=100),
            sees(40, sim=100, aircraft=("Airplanes\\Maddog X",)),
        ]
        history = drive(config(**self.MADDOG), timeline)
        self.assertEqual(len(runs(history)), 2)
        self.assertEqual(len(stops(history)), 1)
        self.assertEqual(sum(1 for action in history[3] if action[0] == "stop"), 1)

    def test_mdclient_events_in_one_look_are_taken_in_order(self):
        timeline = [sees(0, sim=100), sees(8, sim=100, aircraft=("Airplanes\\Maddog X", "Airplanes\\Asobo_TBM930", "Airplanes\\Maddog X"))]
        history = drive(config(**self.MADDOG), timeline)
        self.assertEqual([action[0] for actions in history for action in actions if action[0] in ("run", "stop")], ["run", "stop", "run"])

    def test_another_aircraft_with_nothing_running_does_nothing(self):
        history = drive(config(**self.MADDOG), [sees(0, sim=100), sees(8, sim=100, aircraft=("Airplanes\\Asobo_TBM930",))])
        self.assertEqual(stops(history), [])

    def test_mdclient_stops_with_the_simulator_only_what_it_started(self):
        timeline = [sees(0, sim=100), sees(8, sim=100, aircraft=("Airplanes\\Maddog X",)), sees(16, sim=100, app=True), sees(24, app=True)]
        self.assertEqual(len(stops(drive(config(**self.MADDOG), timeline))), 1)
        yours = [sees(0, sim=100, app=True), sees(8, app=True)]
        self.assertEqual(stops(drive(config(**self.MADDOG), yours)), [])


class ListeningTest(unittest.TestCase):
    CFG = {"grace": 20, "health": {"listening": 8651}, "stop": "with_simulator"}

    def test_the_bridge_starts_only_when_its_port_is_not_listening(self):
        down = drive(config(**self.CFG), ticks(0, 60, sim=100))
        up = drive(config(**self.CFG), ticks(0, 60, sim=100, listening=True))
        self.assertEqual((len(runs(down)), runs(up)), (1, []))

    def test_a_program_that_is_there_but_not_listening_yet_is_not_started_over(self):
        self.assertEqual(runs(drive(config(**self.CFG), ticks(0, 60, sim=100, app=True))), [])

    def test_the_bridge_that_dies_is_not_started_again_for_the_same_simulator(self):
        timeline = ticks(0, 40, sim=100) + ticks(40, 80, sim=100, listening=True, app=True) + ticks(80, 200, sim=100)
        self.assertEqual(len(runs(drive(config(**self.CFG), timeline))), 1)


class ConnectionTest(unittest.TestCase):
    CFG = {"trigger": "none", "health": {"connected_to": 5111, "lost_for": 60, "restart": "restart"}}

    def stuck(self, start, stop, every=10, **fields):
        return [sees(now, sim=100, app=True, listening=True, connected=False, **fields) for now in range(start, stop, every)]

    def test_a_program_that_stays_unconnected_is_restarted_after_the_wait(self):
        history = drive(config(**self.CFG), self.stuck(0, 100))
        self.assertEqual(runs(history), [("run", "restart", ())])
        first = next(number for number, actions in enumerate(history) if any(action[0] == "run" for action in actions))
        self.assertEqual(first * 10, 60)

    def test_a_connection_that_comes_back_resets_the_wait(self):
        timeline = self.stuck(0, 50) + [sees(50, sim=100, app=True, listening=True, connected=True)] + self.stuck(60, 110)
        self.assertEqual(runs(drive(config(**self.CFG), timeline)), [])

    def test_a_simulator_that_is_not_listening_yet_is_not_a_lost_connection(self):
        timeline = [sees(now, sim=100, app=True, listening=False) for now in range(0, 300, 10)]
        self.assertEqual(runs(drive(config(**self.CFG), timeline)), [])

    def test_a_program_that_is_not_running_is_not_restarted(self):
        timeline = [sees(now, sim=100, app=False, listening=True) for now in range(0, 300, 10)]
        self.assertEqual(runs(drive(config(**self.CFG), timeline)), [])

    def test_restarts_stop_at_three_per_simulator_with_the_wait_between_them(self):
        history = drive(config(**self.CFG), self.stuck(0, 1000))
        times = [number * 10 for number, actions in enumerate(history) if any(action[0] == "run" for action in actions)]
        self.assertEqual(len(times), 3)
        self.assertTrue(all(later - earlier >= 60 for earlier, later in zip(times, times[1:])), times)
        self.assertEqual(len([line for line in logs(history) if "giving up" in line]), 1)

    def test_a_new_simulator_gets_three_restarts_again(self):
        timeline = self.stuck(0, 600) + [sees(600)] + [watcher.Seen(now=now, sim=200, app=True, listening=True) for now in range(610, 1200, 10)]
        self.assertEqual(len(runs(drive(config(**self.CFG), timeline))), 6)

    def test_the_connection_loss_is_said_once_per_loss(self):
        history = drive(config(**self.CFG), self.stuck(0, 55))
        self.assertEqual(len([line for line in logs(history) if "not connected to port 5111" in line]), 1)


class QuietTest(unittest.TestCase):
    def test_a_steady_state_says_nothing_after_the_first_look(self):
        for fields in ({}, {"sim": 100, "app": True}, {"sim": 100}):
            history = drive(config(grace=0), [sees(now, **fields) for now in range(0, 800, 8)])
            self.assertLessEqual(len(logs(history)), 3, fields)

    def test_no_simulator_means_no_lines_at_all(self):
        self.assertEqual(logs(drive(config(), ticks(0, 800))), [])

    def test_every_action_is_a_known_kind(self):
        history = drive(config(grace=0, stop="with_simulator", wait_for={"process": "x", "timeout": 10}), ticks(0, 60, sim=100) + ticks(60, 100, app=True))
        kinds = {action[0] for actions in history for action in actions}
        self.assertLessEqual(kinds, {"log", "run", "stop"})


class FakeProc:
    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def process(self, pid, command, comm=None, environ=None, sockets=()):
        folder = self.root / str(pid)
        write(folder / "cmdline", command.replace(" ", "\0") + "\0" if "\\" not in command else command + "\0")
        if comm:
            write(folder / "comm", comm + "\n")
        if environ is not None:
            write(folder / "environ", "".join(f"{key}={value}\0" for key, value in environ.items()))
        (folder / "fd").mkdir(exist_ok=True)
        for number, inode in enumerate(sockets):
            os.symlink(f"socket:[{inode}]", folder / "fd" / str(number + 3))

    def tcp(self, *rows):
        header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
        lines = [f"{number:4}: 0100007F:{local:04X} 0100007F:{remote:04X} {state} 00000000:00000000 00:00000000 00000000  1000        0 {inode} 1 0000000000000000 100 0 0 10 0" for number, (local, remote, state, inode) in enumerate(rows)]
        write(self.root / "net" / "tcp", header + "\n".join(lines) + "\n")


class ObserveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.proc = FakeProc(Path(self.temp.name) / "proc")

    def tearDown(self):
        self.temp.cleanup()

    def observe(self, cfg, **kwargs):
        return watcher.observe(cfg, self.proc.root, 5.0, **kwargs)

    def test_the_simulator_and_the_program_are_found_in_the_command_lines(self):
        self.proc.process(10, SIM_COMMAND)
        self.proc.process(11, APP_COMMAND)
        seen = self.observe(config())
        self.assertEqual((seen.sim, seen.app, seen.now), (10, True, 5.0))

    def test_nothing_running_sees_nothing(self):
        self.proc.process(11, "/usr/bin/python3 other.py")
        seen = self.observe(config())
        self.assertEqual((seen.sim, seen.app, seen.waited, seen.listening, seen.connected), (None, False, False, False, False))

    def test_the_watcher_does_not_see_itself(self):
        self.proc.process(os.getpid(), APP_COMMAND)
        self.assertFalse(self.observe(config()).app)

    def test_the_process_waited_for_is_matched_apart_from_the_program(self):
        self.proc.process(12, "C:\\Windows\\couatl64_MSFS2024.exe")
        seen = self.observe(config(wait_for={"process": r"couatl64_MSFS2024\.exe", "timeout": 5}))
        self.assertEqual((seen.waited, seen.app), (True, False))

    def test_the_aircraft_lines_are_passed_through(self):
        self.assertEqual(self.observe(config(), aircraft=["a", "b"]).aircraft, ("a", "b"))

    def test_a_listening_socket_on_the_port_is_seen(self):
        self.proc.tcp((8651, 0, "0A", 900))
        self.assertTrue(self.observe(config(health={"listening": 8651})).listening)
        self.assertFalse(self.observe(config(health={"listening": 8652})).listening)

    def test_without_the_net_table_nothing_listens(self):
        self.assertFalse(self.observe(config(health={"listening": 8651})).listening)

    def test_a_connection_owned_by_the_program_counts(self):
        self.proc.process(11, APP_COMMAND, sockets=(901,))
        self.proc.tcp((5111, 0, "0A", 900), (50000, 5111, "01", 901))
        seen = self.observe(config(health={"connected_to": 5111, "lost_for": 60, "restart": "restart"}))
        self.assertEqual((seen.listening, seen.connected), (True, True))

    def test_a_connection_owned_by_the_wineserver_of_the_programs_prefix_counts(self):
        prefix = Path(self.temp.name) / "pfx"
        prefix.mkdir()
        self.proc.process(11, APP_COMMAND, environ={"WINEPREFIX": str(prefix)})
        self.proc.process(12, "wineserver", comm="wineserver", environ={"WINEPREFIX": str(prefix)}, sockets=(901,))
        self.proc.tcp((5111, 0, "0A", 900), (50000, 5111, "01", 901))
        self.assertTrue(self.observe(config(health={"connected_to": 5111, "lost_for": 60, "restart": "restart"})).connected)

    def test_a_connection_owned_by_a_wineserver_of_another_prefix_does_not_count(self):
        prefix = Path(self.temp.name) / "pfx"
        other = Path(self.temp.name) / "other"
        prefix.mkdir()
        other.mkdir()
        self.proc.process(11, APP_COMMAND, environ={"WINEPREFIX": str(prefix)})
        self.proc.process(12, "wineserver", comm="wineserver", environ={"WINEPREFIX": str(other)}, sockets=(901,))
        self.proc.tcp((5111, 0, "0A", 900), (50000, 5111, "01", 901))
        self.assertFalse(self.observe(config(health={"connected_to": 5111, "lost_for": 60, "restart": "restart"})).connected)

    def test_a_connection_of_another_program_does_not_count(self):
        self.proc.process(11, APP_COMMAND)
        self.proc.process(13, "/usr/bin/node reader.js", sockets=(901,))
        self.proc.tcp((5111, 0, "0A", 900), (50000, 5111, "01", 901))
        self.assertFalse(self.observe(config(health={"connected_to": 5111, "lost_for": 60, "restart": "restart"})).connected)


class ReaderTest(unittest.TestCase):
    def setUp(self):
        self.said = []
        self.now = 0.0
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def clock(self):
        return self.now

    def reader(self, command):
        return watcher.AircraftReader(command, self.said.append, self.clock, restart=8, retry=60)

    def wait_for_exit(self, reader):
        deadline = time.monotonic() + 5
        while reader.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.05)

    def python(self, code):
        return lambda: [sys.executable, "-c", code]

    def cycle(self, reader, seconds, every=1):
        for second in range(0, seconds, every):
            self.now = float(second)
            reader.poll(True)
            if reader.process:
                self.wait_for_exit(reader)

    def said_with(self, text):
        return len([line for line in self.said if text in line])

    def test_lines_the_child_prints_come_back_as_events(self):
        reader = self.reader(self.python("print('#open'); print('Airplanes\\\\Maddog X'); print('Airplanes\\\\Other')"))
        reader.poll(True)
        self.wait_for_exit(reader)
        self.assertEqual(reader.poll(True), ("Airplanes\\Maddog X", "Airplanes\\Other"))
        self.assertEqual(reader.poll(True), ())

    def test_the_open_line_is_said_and_never_passed_on_as_an_aircraft(self):
        reader = self.reader(self.python("print('#open')"))
        reader.poll(True)
        self.wait_for_exit(reader)
        self.assertEqual(reader.poll(True), ())
        self.assertEqual(self.said_with("connected to SimConnect"), 1)

    def test_a_reader_nobody_wants_is_stopped(self):
        reader = self.reader(self.python("import time; time.sleep(60)"))
        reader.poll(True)
        process = reader.process
        reader.poll(False)
        self.assertIsNotNone(process.wait(5))
        self.assertIsNone(reader.process)

    def test_a_reader_that_cannot_connect_is_retried_after_eight_seconds_and_the_failure_is_said_once(self):
        starts = []

        def command():
            starts.append(self.now)
            return [sys.executable, "-c", "raise SystemExit(1)"]

        reader = self.reader(command)
        self.cycle(reader, 60)
        self.assertEqual(self.said_with("SimConnect is not answering"), 1)
        self.assertEqual([later - earlier for earlier, later in zip(starts, starts[1:])][:3], [9.0, 9.0, 9.0])

    def test_a_missing_module_is_said_once_and_retried_every_minute(self):
        starts = []

        def command():
            starts.append(self.now)
            return [sys.executable, "-c", f"raise SystemExit({watcher.MODULE_MISSING})"]

        reader = self.reader(command)
        self.cycle(reader, 200, every=5)
        self.assertEqual(self.said_with("node-simconnect is missing"), 1)
        self.assertEqual([later - earlier for earlier, later in zip(starts, starts[1:])][:2], [65.0, 65.0])

    def test_a_command_that_is_unavailable_is_said_once_and_retried_every_minute(self):
        tries = []

        def command():
            tries.append(self.now)
            raise watcher.ReaderUnavailable("node is not installed")

        reader = self.reader(command)
        for second in range(0, 200, 10):
            self.now = float(second)
            reader.poll(True)
        self.assertEqual(self.said_with("node is not installed"), 1)
        self.assertEqual(tries, [0.0, 60.0, 120.0, 180.0])

    def test_a_connection_that_opens_is_said_once_and_a_failure_before_it_stays_said_once(self):
        flag = Path(self.folder.name) / "flag"
        code = f"import pathlib, sys, time; sys.exit(1) if not pathlib.Path({str(flag)!r}).exists() else (print('#open', flush=True), time.sleep(60))"
        reader = self.reader(self.python(code))
        self.cycle(reader, 12)
        flag.write_text("")
        for second in range(12, 40):
            self.now = float(second)
            reader.poll(True)
            time.sleep(0.02)
        reader.poll(False)
        self.assertEqual(self.said_with("is not answering"), 1)
        self.assertEqual(self.said_with("connected to SimConnect"), 1)

    def test_a_connection_the_simulator_closes_is_said(self):
        reader = self.reader(self.python("print('#open')"))
        self.cycle(reader, 4)
        self.assertEqual(self.said_with("connected to SimConnect"), 1)
        self.assertEqual(self.said_with("SimConnect closed the connection"), 1)


class ReaderCommandTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fake = FakeMachine(Path(self.temp.name)).build()

    def tearDown(self):
        self.temp.cleanup()

    def xml(self, port):
        write(self.fake.discover().base / "SimConnect.xml", f"<SimConnect.Comm><Protocol>IPv4</Protocol><Port>{port}</Port></SimConnect.Comm>")

    def test_without_node_the_problem_is_named(self):
        with mock.patch.object(watcher.shutil, "which", return_value=None), self.assertRaisesRegex(watcher.ReaderUnavailable, "node is not installed"):
            watcher.reader_command(self.fake.discover(), {})

    def test_without_a_port_the_apply_command_is_named(self):
        with mock.patch.object(watcher.shutil, "which", return_value="/usr/bin/node"), self.assertRaisesRegex(watcher.ReaderUnavailable, "addon apply simconnect-tcp"):
            watcher.reader_command(self.fake.discover(), {})

    def test_a_port_below_1024_is_refused(self):
        self.xml(500)
        with mock.patch.object(watcher.shutil, "which", return_value="/usr/bin/node"), self.assertRaisesRegex(watcher.ReaderUnavailable, "500"):
            watcher.reader_command(self.fake.discover(), {})

    def test_the_command_takes_the_port_from_simconnect_xml_and_the_modules_from_the_cache(self):
        self.xml(5111)
        with mock.patch.object(watcher.shutil, "which", return_value="/usr/bin/node"):
            command = watcher.reader_command(self.fake.discover(), {"FSOL_CACHE": "/some/cache"})
        self.assertEqual(command, ["node", str(watcher.READER_SCRIPT), "5111", "/some/cache/simconnect"])


class UnitTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def addon(self, **options):
        folder = self.root / "addons" / "demo"
        write(folder / "addon.json", json.dumps(dict(SPEC, watcher=dict({"app": APP}, **options))))
        for name in ("apply", "check", "undo", "launch"):
            write(folder / name, "#!/bin/sh\n")
            (folder / name).chmod(0o755)
        return addons.load(folder)

    def looping(self):
        folder = self.root / "addons" / "demo"
        write(folder / "addon.json", json.dumps(dict(SPEC, watcher={"loop": "keep-links"})))
        for name in ("apply", "check", "undo", "keep-links"):
            write(folder / name, "#!/bin/sh\n")
            (folder / name).chmod(0o755)
        return addons.load(folder)

    def systemctl(self, *args):
        self.calls.append(args)
        return 0

    def test_a_loop_unit_launches_the_script_and_restarts_on_failure(self):
        lines = watcher.unit_text(self.looping(), Path("/opt/fsol/bin/flightsim-on-linux")).splitlines()
        for expected in ('ExecStart="/opt/fsol/bin/flightsim-on-linux" addon launch demo keep-links', "Restart=on-failure", "WantedBy=default.target"):
            self.assertIn(expected, lines)
        self.assertNotIn("addon watch", "\n".join(lines))

    def test_stopping_a_loop_unit_reaches_the_loop_because_the_script_is_the_service(self):
        self.assertNotIn("KillMode=process", watcher.unit_text(self.looping(), Path("/x/cli")))

    def test_a_loop_unit_quotes_the_exec_path_too(self):
        text = watcher.unit_text(self.looping(), Path('/opt/odd dir/100%/cli'))
        self.assertIn('ExecStart="/opt/odd dir/100%%/cli" addon launch demo keep-links', text)

    def test_the_unit_runs_the_watch_loop_and_restarts_on_failure(self):
        text = watcher.unit_text(self.addon(stop="with_simulator"), Path("/opt/fsol/bin/flightsim-on-linux"))
        lines = text.splitlines()
        for expected in ('ExecStart="/opt/fsol/bin/flightsim-on-linux" addon watch demo', "Restart=on-failure", "WantedBy=default.target"):
            self.assertIn(expected, lines)

    def test_stopping_the_service_never_signals_the_program_whatever_the_stop_rule(self):
        for stop in addons.STOPS[:3]:
            self.assertIn("KillMode=process", watcher.unit_text(self.addon(stop=stop), Path("/x/cli")).splitlines())

    def test_the_exec_path_is_quoted_for_systemd(self):
        text = watcher.unit_text(self.addon(), Path('/opt/odd dir/100%/$x/"q"/cli'))
        self.assertIn('ExecStart="/opt/odd dir/100%%/$$x/\\"q\\"/cli" addon watch demo', text)

    def test_the_unit_lives_under_the_user_config_folder(self):
        addon = self.addon()
        self.assertEqual(watcher.unit_path(addon, self.root / "config"), self.root / "config" / "systemd" / "user" / "flightsim-on-linux-demo.service")

    def test_installing_writes_the_file_then_enables_and_starts_it(self):
        addon = self.addon()
        code = watcher.install_unit(addon, Path("/x/cli"), self.root / "config", self.systemctl)
        self.assertEqual(code, 0)
        self.assertTrue(watcher.has_unit(addon, self.root / "config"))
        self.assertEqual(self.calls, [("enable", "--now", "flightsim-on-linux-demo.service")])

    def test_removing_disables_the_unit_then_deletes_the_file(self):
        addon = self.addon()
        watcher.install_unit(addon, Path("/x/cli"), self.root / "config", self.systemctl)
        self.calls.clear()
        watcher.remove_unit(addon, self.root / "config", self.systemctl)
        self.assertFalse(watcher.has_unit(addon, self.root / "config"))
        self.assertEqual(self.calls, [("disable", "--now", "flightsim-on-linux-demo.service"), ("daemon-reload",)])

    def test_removing_what_was_never_written_does_not_ask_systemd_to_disable_it(self):
        watcher.remove_unit(self.addon(), self.root / "config", self.systemctl)
        self.assertEqual(self.calls, [("daemon-reload",)])

    def test_a_failing_systemctl_gives_its_exit_code(self):
        self.assertEqual(watcher.install_unit(self.addon(), Path("/x/cli"), self.root / "config", lambda *args: 5), 5)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "needs systemd-analyze")
    def test_systemd_accepts_the_generated_unit(self):
        cli = Path(__file__).resolve().parent.parent / "bin" / "flightsim-on-linux"
        for name, addon in (("stop", self.addon(stop="with_simulator")), ("never", self.addon()), ("loop", self.looping())):
            path = self.root / f"{name}.service"
            path.write_text(watcher.unit_text(addon, cli))
            result = subprocess.run(["systemd-analyze", "--user", "verify", str(path)], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""), name)


@unittest.skipUnless(shutil.which("sleep"), "needs sleep")
class RealLoopTest(unittest.TestCase):
    APP_PATTERN = r"^Q:.*\\FsolDemo\.exe"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.token = str(self.root)
        self.found = FakeMachine(self.root).build().discover()
        self.sleep = self.root / "bin" / "sleep"
        self.sleep.parent.mkdir()
        shutil.copy(shutil.which("sleep"), self.sleep)
        self.marker = self.root / "marker"
        self.simulator = None
        folder = self.root / "addons" / "demo"
        spec = dict(SPEC, watcher={"app": self.APP_PATTERN, "grace": 0.3, "poll": 0.1, "args": ["--minimized"], "stop": "with_simulator"})
        write(folder / "addon.json", json.dumps(spec))
        for name in ("apply", "check", "undo"):
            write(folder / name, "#!/bin/sh\n")
            (folder / name).chmod(0o755)
        self.launch(f'#!/bin/bash\necho "$FSOL_SIM_RUNNING $*" >> {self.marker}\n( exec -a \'Q:\\Program Files\\FsolDemo.exe\' {self.sleep} 60 ) > /dev/null 2>&1 &\n', folder)
        self.addon = addons.load(folder)
        env = mock.patch.dict(os.environ, {"HOME": str(self.root / "home"), "NoDefaultCurrentDirectoryInExePath": "1"})
        env.start()
        self.addCleanup(env.stop)
        own = mock.patch.object(machine, "sim_pid", self.own_sim_pid)
        own.start()
        self.addCleanup(own.stop)

    def own_sim_pid(self, proc=Path("/proc")):
        return self.simulator.pid if self.simulator is not None and self.simulator.poll() is None else None

    def launch(self, text, folder=None):
        script = (folder or self.addon.folder) / "launch"
        write(script, text)
        script.chmod(0o755)

    def tearDown(self):
        if self.simulator and self.simulator.poll() is None:
            self.simulator.kill()
        if self.simulator:
            self.simulator.wait()
        for pid_dir in watcher.matching(self.APP_PATTERN, watcher.scan(Path("/proc"))):
            os.kill(int(pid_dir.name), signal.SIGKILL)
        self.temp.cleanup()

    def start_simulator(self):
        self.simulator = start_stand_in(self.root, self.token, SIM_COMMAND, 60)

    def program_running(self):
        return bool(watcher.matching(self.APP_PATTERN, watcher.scan(Path("/proc"))))

    def loop(self, should_stop, seconds=10):
        deadline = time.monotonic() + seconds
        said = []
        watcher.run(self.addon, self.found, Path("/x/cli"), say=said.append, should_stop=lambda: should_stop() or time.monotonic() > deadline)
        return said

    def test_the_real_loop_starts_the_program_once_and_closes_it_when_the_simulator_goes(self):
        self.start_simulator()
        stages = {}

        def should_stop():
            now = time.monotonic()
            if "started" not in stages and self.marker.exists() and self.program_running():
                stages["started"] = now
            if "started" in stages and "closed" not in stages and now - stages["started"] > 1.5:
                self.simulator.terminate()
                self.simulator.wait()
                stages["closed"] = now
            return "closed" in stages and not self.program_running()

        said = self.loop(should_stop)
        self.assertIn("closed", stages)
        self.assertEqual(self.marker.read_text().splitlines(), ["1 --minimized"])
        self.assertFalse(self.program_running())
        self.assertEqual(len([line for line in said if "starting the program" in line]), 1)
        self.assertEqual(len([line for line in said if "closed" in line]), 1)

    def test_a_launch_script_gets_a_clean_environment(self):
        self.launch(f'#!/bin/sh\necho "[${{NoDefaultCurrentDirectoryInExePath-gone}}]" > {self.marker}\n')
        self.start_simulator()
        self.loop(self.marker.exists)
        self.assertEqual(self.marker.read_text().strip(), "[gone]")

    def test_a_simulator_that_never_runs_starts_nothing(self):
        self.loop(lambda: False, seconds=1)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
