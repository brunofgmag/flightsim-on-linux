import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PACK = ROOT / "tools" / "pack-shims.sh"
EXACT_TAG = re.compile(r"@v\d+\.\d+\.\d+$")


def job(name):
    text = WORKFLOW.read_text()
    start = text.index(f"\n  {name}:\n")
    following = re.search(r"\n  [a-z-]+:\n", text[start + 1:])
    return text[start:start + 1 + following.start()] if following else text[start:]


class WorkflowTest(unittest.TestCase):
    def test_the_tests_job_installs_what_the_skipped_tests_need_and_sets_up_dotnet(self):
        tests = job("tests")
        for package in ("gcc-mingw-w64-x86-64", "desktop-file-utils", "attr", "python3-pyqt6", "golang"):
            self.assertRegex(tests, rf"apt-get install[^\n]*\b{re.escape(package)}\b")
        self.assertRegex(tests, r"uses: actions/setup-dotnet@v\d+\.\d+\.\d+")

    def test_the_windows_programs_job_sets_up_go_and_dotnet_at_exact_versions(self):
        programs = job("windows-programs")
        for action in ("actions/setup-go", "actions/setup-dotnet"):
            uses = re.findall(rf"uses: {action}(@\S+)", programs)
            self.assertEqual(len(uses), 1, action)
            self.assertRegex(uses[0], r"^@v\d+\.\d+\.\d+$")

    def test_the_new_actions_are_pinned_to_exact_tags(self):
        for action in re.findall(r"uses: (actions/setup-[a-z]+@\S+)", WORKFLOW.read_text()):
            self.assertRegex(action, EXACT_TAG)

    def test_shellcheck_runs_at_its_default_severity(self):
        shellcheck = job("shellcheck")
        self.assertIn("tools/shell-scripts.sh | xargs -r -d '\\n' shellcheck", shellcheck)
        self.assertNotRegex(shellcheck, r"shellcheck\s+(-S|--severity)")

    def test_the_release_uploads_files_only(self):
        release = job("release")
        self.assertIn('test -z "$(find out/shims -mindepth 1 -type d)"', release)


class PackShimsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.shims = Path(self.temp.name) / "shims"
        (self.shims / "windowsbase-timer").mkdir(parents=True)
        (self.shims / "windowsbase-timer" / "windowsbase-timer.dll").write_bytes(b"MZ timer")
        (self.shims / "windowsbase-timer" / "Mono.Cecil.dll").write_bytes(b"MZ cecil")
        (self.shims / "procgov.exe").write_bytes(b"MZ procgov")

    def pack(self):
        return subprocess.run([str(PACK), str(self.shims)], capture_output=True, text=True)

    def test_a_folder_becomes_one_archive_and_only_files_are_left(self):
        result = self.pack()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(path.name for path in self.shims.iterdir()), ["SHA256SUMS", "procgov.exe", "windowsbase-timer.tar.gz"])
        self.assertTrue(all(path.is_file() for path in self.shims.iterdir()))
        with tarfile.open(self.shims / "windowsbase-timer.tar.gz") as archive:
            self.assertEqual(sorted(archive.getnames()), ["windowsbase-timer", "windowsbase-timer/Mono.Cecil.dll", "windowsbase-timer/windowsbase-timer.dll"])

    def test_the_archive_is_listed_in_sha256sums_and_the_sums_check_out(self):
        self.pack()
        listed = [line.split("  ", 1)[1] for line in (self.shims / "SHA256SUMS").read_text().splitlines()]
        self.assertEqual(listed, ["procgov.exe", "windowsbase-timer.tar.gz"])
        checked = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=self.shims, capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_packing_twice_gives_the_same_sums(self):
        self.pack()
        first = (self.shims / "SHA256SUMS").read_text()
        (self.shims / "windowsbase-timer").mkdir()
        (self.shims / "windowsbase-timer" / "windowsbase-timer.dll").write_bytes(b"MZ timer")
        (self.shims / "windowsbase-timer" / "Mono.Cecil.dll").write_bytes(b"MZ cecil")
        self.pack()
        self.assertEqual((self.shims / "SHA256SUMS").read_text(), first)

    def test_a_folder_of_files_only_still_gets_its_sums(self):
        shutil.rmtree(self.shims / "windowsbase-timer")
        self.pack()
        self.assertEqual((self.shims / "SHA256SUMS").read_text().split()[1], "procgov.exe")

    def test_an_empty_folder_gets_no_sums_and_a_missing_one_is_refused(self):
        shutil.rmtree(self.shims / "windowsbase-timer")
        (self.shims / "procgov.exe").unlink()
        self.assertEqual(self.pack().returncode, 0)
        self.assertEqual(list(self.shims.iterdir()), [])
        missing = subprocess.run([str(PACK), str(self.shims / "nowhere")], capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)

    def test_the_windows_build_packs_its_shims_with_it(self):
        self.assertIn('"$ROOT/tools/pack-shims.sh" "$OUT/shims"', (ROOT / "tools" / "build-windows.sh").read_text())


@unittest.skipUnless(shutil.which("shellcheck"), "needs shellcheck")
class ShellcheckTest(unittest.TestCase):
    def test_every_shell_script_passes_shellcheck_at_its_default_severity(self):
        listed = subprocess.run([str(ROOT / "tools" / "shell-scripts.sh")], capture_output=True, text=True, check=True).stdout.splitlines()
        result = subprocess.run(["shellcheck", "-f", "gcc", *listed], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
