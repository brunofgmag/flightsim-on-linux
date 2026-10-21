import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

GUI = Path(__file__).resolve().parent.parent / "bin" / "flightsim-on-linux-gui"
FAKE_DBUS_SEND = '#!/bin/sh\necho "$*" >> "$DBUS_LOG"\n'
FAKE_TERMINAL = '#!/bin/sh\necho "$(basename "$0") $1" > "$TERMINAL_LOG.who"\ncase "$1" in -e|-x|--) shift ;; esac\n"$@" < /dev/null > "$TERMINAL_LOG" 2>&1\n'
TASK = "sh -c 'echo first line; sleep 1; echo last line'"
TOOLS = ("bash", "sh", "mktemp", "dirname", "readlink", "rm", "tail", "sleep", "kill", "pkill", "cat", "basename", "printf")


def fake_kdialog(yesno):
    return f'#!/bin/sh\ncase "$*" in\n  *--progressbar*) echo "org.kde.kdialog-1 /ProgressDialog" ;;\n  *--yesno*) echo $$ > "$KDIALOG_PID"; {yesno} ;;\nesac\n'


FAKE_ZENITY = '#!/bin/sh\ncase "$*" in\n  *--progress*) echo "$*" > "$ZENITY_LOG"; echo "Show output"; cat > /dev/null ;;\nesac\n'


class GuiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for tool in TOOLS:
            (self.bin / tool).symlink_to(shutil.which(tool))
        self.fake("dbus-send", FAKE_DBUS_SEND)
        self.calls = self.root / "dbus.log"
        self.terminal_log = self.root / "terminal.log"

    def tearDown(self):
        self.temp.cleanup()

    def fake(self, name, body):
        (self.bin / name).write_text(body)
        (self.bin / name).chmod(0o755)

    def run_gui(self, script, dialog="kdialog", desktop="KDE", **extra):
        env = dict(PATH=str(self.bin), HOME=str(self.root), FSOL_DIALOG=dialog, XDG_CURRENT_DESKTOP=desktop,
                   DBUS_LOG=str(self.calls), TERMINAL_LOG=str(self.terminal_log),
                   KDIALOG_PID=str(self.root / "kdialog.pid"), ZENITY_LOG=str(self.root / "zenity.log"), **extra)
        result = subprocess.run([str(self.bin / "bash"), "-c", f'source "{GUI}"; {script}'], env=env, capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout

    def terminal_output(self):
        for _ in range(50):
            if self.terminal_log.exists() and "Finished" in self.terminal_log.read_text():
                return self.terminal_log.read_text()
            time.sleep(0.1)
        return self.terminal_log.read_text() if self.terminal_log.exists() else ""


class KdialogProgressTest(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.fake("kdialog", fake_kdialog("exit 1"))

    def dbus_calls(self):
        return self.calls.read_text().splitlines()

    def test_the_dialog_is_closed_with_a_method_call(self):
        self.run_gui('run_with_progress "Testing" true')
        self.assertIn("--session --type=method_call --dest=org.kde.kdialog-1 /ProgressDialog org.kde.kdialog.ProgressDialog.close", self.dbus_calls())

    def test_the_cancel_button_is_hidden_with_a_method_call(self):
        self.run_gui('run_with_progress "Testing" true')
        self.assertIn("--session --type=method_call --dest=org.kde.kdialog-1 /ProgressDialog org.kde.kdialog.ProgressDialog.showCancelButton boolean:false", self.dbus_calls())

    def test_the_command_status_comes_back(self):
        status, _ = self.run_gui('run_with_progress "Testing" false')
        self.assertEqual(status, 1)


class ShowOutputTest(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.fake("konsole", FAKE_TERMINAL)

    def test_kdialog_offers_the_output_and_the_terminal_follows_the_task_to_its_end(self):
        self.fake("kdialog", fake_kdialog("exit 0"))
        self.run_gui(f'run_with_progress "Testing" {TASK}')
        output = self.terminal_output()
        self.assertIn("first line", output)
        self.assertIn("last line", output)
        self.assertIn("Finished", output)

    def test_an_offer_nobody_answers_closes_when_the_task_ends(self):
        self.fake("kdialog", fake_kdialog("exec sleep 60"))
        started = time.monotonic()
        self.run_gui(f'run_with_progress "Testing" {TASK}')
        self.assertLess(time.monotonic() - started, 10)
        pid = int((self.root / "kdialog.pid").read_text())
        time.sleep(0.2)
        self.assertFalse(Path(f"/proc/{pid}").exists())
        self.assertFalse(self.terminal_log.exists())

    def test_zenity_shows_the_button_in_the_progress_and_opens_the_terminal(self):
        self.fake("zenity", FAKE_ZENITY)
        self.run_gui(f'run_with_progress "Testing" {TASK}', dialog="zenity")
        self.assertIn("--extra-button=Show output", (self.root / "zenity.log").read_text())
        self.assertIn("last line", self.terminal_output())


class TerminalChoiceTest(GuiTestCase):
    def chosen(self, *terminals, desktop="KDE", **extra):
        for name in terminals:
            self.fake(name, "#!/bin/sh\n")
        status, out = self.run_gui("terminal_command", desktop=desktop, **extra)
        return out.strip() if status == 0 else None

    def test_kde_prefers_konsole(self):
        self.assertEqual(self.chosen("xdg-terminal-exec", "konsole"), "konsole -e")

    def test_elsewhere_the_desktop_default_comes_first(self):
        self.assertEqual(self.chosen("xdg-terminal-exec", "konsole", desktop="GNOME"), "xdg-terminal-exec")

    def test_gnome_terminal_runs_a_command_after_two_dashes(self):
        self.assertEqual(self.chosen("gnome-terminal", desktop="GNOME"), "gnome-terminal --")

    def test_xfce4_terminal_runs_a_command_after_x(self):
        self.assertEqual(self.chosen("xfce4-terminal", desktop="XFCE"), "xfce4-terminal -x")

    def test_the_terminal_variable_wins(self):
        self.assertEqual(self.chosen("konsole", "xterm", TERMINAL="xterm"), "xterm -e")

    def test_no_terminal_means_none(self):
        self.assertIsNone(self.chosen())


if __name__ == "__main__":
    unittest.main()
