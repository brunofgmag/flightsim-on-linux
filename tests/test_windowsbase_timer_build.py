import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.support import stub

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "shims" / "windowsbase-timer" / "build.sh"
DOTNET = shutil.which("dotnet")


@unittest.skipUnless(DOTNET, "needs dotnet")
class BuildTest(unittest.TestCase):
    def test_an_sdk_of_another_major_than_the_project_builds_for_its_own_framework(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            stub(temp / "bin" / "dotnet", f'#!/bin/sh\n[ "$1" = --version ] && {{ echo 8.0.100; exit 0; }}\nexec "{DOTNET}" "$@"\n')
            env = dict(os.environ, PATH=f"{temp / 'bin'}:{os.environ['PATH']}", TMPDIR=str(temp))
            result = subprocess.run([str(BUILD), str(temp / "out")], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            built = temp / "out" / "windowsbase-timer"
            self.assertTrue((built / "windowsbase-timer.dll").is_file())
            config = json.loads((built / "windowsbase-timer.runtimeconfig.json").read_text())
            self.assertEqual(config["runtimeOptions"]["tfm"], "net8.0")


if __name__ == "__main__":
    unittest.main()
