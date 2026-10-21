import os
import subprocess
import tempfile
import unittest
from pathlib import Path

GUI = Path(__file__).resolve().parent.parent / "bin" / "flightsim-on-linux-gui"
FAKE_KDIALOG = '#!/bin/sh\ncase "$*" in *--progressbar*) echo "org.kde.kdialog-1 /ProgressDialog" ;; esac\n'
FAKE_DBUS_SEND = '#!/bin/sh\necho "$*" >> "$DBUS_LOG"\n'


class KdialogProgressTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.bin = root / "bin"
        self.bin.mkdir()
        for name, body in (("kdialog", FAKE_KDIALOG), ("dbus-send", FAKE_DBUS_SEND)):
            (self.bin / name).write_text(body)
            (self.bin / name).chmod(0o755)
        self.calls = root / "dbus.log"

    def tearDown(self):
        self.temp.cleanup()

    def run_progress(self, command):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", FSOL_DIALOG="kdialog", DBUS_LOG=str(self.calls))
        script = f'source "{GUI}"; run_with_progress "Testing" {command}'
        status = subprocess.run(["bash", "-c", script], env=env).returncode
        return status, self.calls.read_text().splitlines()

    def test_the_dialog_is_closed_with_a_method_call(self):
        _, calls = self.run_progress("true")
        self.assertIn("--session --type=method_call --dest=org.kde.kdialog-1 /ProgressDialog org.kde.kdialog.ProgressDialog.close", calls)

    def test_the_cancel_button_is_hidden_with_a_method_call(self):
        _, calls = self.run_progress("true")
        self.assertIn("--session --type=method_call --dest=org.kde.kdialog-1 /ProgressDialog org.kde.kdialog.ProgressDialog.showCancelButton boolean:false", calls)

    def test_the_command_status_comes_back(self):
        status, _ = self.run_progress("false")
        self.assertEqual(status, 1)


if __name__ == "__main__":
    unittest.main()
