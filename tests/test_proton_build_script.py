import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "proton" / "build-proton-fs2024.sh"
RELEASE_COMMIT = "44cf7c2042168f3b8ee37249f0a53ac091e48fd9"
PINNED_COMMIT = "8293130ebe370b1deb761898ae0d397d680837de"
CHOSEN_COMMIT = "0bb64c56c8f0dcc11ecc0f6c56e00e4f7bd8d506"


def functions(*names):
    lines = SCRIPT.read_text().splitlines()
    picked = [line for line in lines if line.startswith("VKD3D_PINNED_COMMIT=")]
    for name in names:
        start = lines.index(f"{name}() {{")
        end = lines.index("}", start)
        picked.extend(lines[start:end + 1])
    return "\n".join(picked)


class FindVkd3dCommitTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stage = Path(self.temp.name)
        version = self.stage / "files" / "lib" / "wine" / "vkd3d-proton" / "version"
        version.parent.mkdir(parents=True)
        version.write_text(f"{RELEASE_COMMIT} vkd3d-proton (v3.0b-12-g44cf7c20)\n")

    def run_bash(self, call, **env):
        text = "\n".join([
            "set -euo pipefail",
            functions("die", "step", "check_vkd3d_choice", "find_vkd3d_commit"),
            f'STAGE="{self.stage}"',
            call,
        ])
        merged = {key: value for key, value in os.environ.items() if key != "VKD3D_COMMIT"}
        merged.update(env)
        return subprocess.run(["bash", "-c", text], env=merged, capture_output=True, text=True)

    def find(self, **env):
        return self.run_bash('find_vkd3d_commit\necho "chosen $VKD3D_COMMIT"', **env)

    def test_without_a_choice_the_build_uses_vkd3d_proton_3_1_and_names_the_release_one(self):
        result = self.find()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"vkd3d-proton {PINNED_COMMIT}, pinned by this project; the release ships {RELEASE_COMMIT}", result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], f"chosen {PINNED_COMMIT}")

    def test_choosing_the_release_commit_builds_the_release_vkd3d_proton(self):
        result = self.find(VKD3D_COMMIT=RELEASE_COMMIT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"vkd3d-proton {RELEASE_COMMIT}, from VKD3D_COMMIT, the one the release ships", result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], f"chosen {RELEASE_COMMIT}")

    def test_a_commit_in_the_environment_replaces_the_release_one_and_says_so(self):
        result = self.find(VKD3D_COMMIT=CHOSEN_COMMIT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"vkd3d-proton {CHOSEN_COMMIT}, from VKD3D_COMMIT; the release ships {RELEASE_COMMIT}", result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], f"chosen {CHOSEN_COMMIT}")

    def test_a_chosen_commit_that_is_not_a_full_hash_stops_the_build(self):
        result = self.run_bash("check_vkd3d_choice", VKD3D_COMMIT="v3.1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("VKD3D_COMMIT must be a full 40-character commit, not v3.1", result.stderr)

    def test_a_full_chosen_commit_passes_the_early_check(self):
        result = self.run_bash("check_vkd3d_choice", VKD3D_COMMIT=CHOSEN_COMMIT)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_early_check_runs_before_any_source_is_fetched(self):
        text = SCRIPT.read_text()
        preconditions = text[text.index("check_preconditions() {"):]
        self.assertIn("  check_vkd3d_choice\n", preconditions[:preconditions.index("\n}\n")])


if __name__ == "__main__":
    unittest.main()
