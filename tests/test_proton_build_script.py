import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "proton" / "build-proton-fs2024.sh"
RELEASE_COMMIT = "44cf7c2042168f3b8ee37249f0a53ac091e48fd9"
PINNED_COMMIT = "8293130ebe370b1deb761898ae0d397d680837de"
CHOSEN_COMMIT = "0bb64c56c8f0dcc11ecc0f6c56e00e4f7bd8d506"
FENIX_PATCH = ROOT / "patches" / "wine" / "fenix-a320-linux-patch-cachyos-11.patch"
FENIX_UPSTREAM_SHA256 = "0992d4c491f37f041490743612b81dd5b2f79ddd618c5c57f91dc03e518373e2"


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


class CarriedFenixPatchTest(unittest.TestCase):
    def setUp(self):
        self.script = SCRIPT.read_text()

    def patches_block(self):
        start = self.script.index("PATCHES=(\n")
        return self.script[start:self.script.index("\n)\n", start)].splitlines()[1:]

    def header_and_body(self):
        header, separator, body = FENIX_PATCH.read_bytes().partition(b"\n\n")
        self.assertEqual(separator, b"\n\n")
        return header.decode(), body

    def test_the_first_patch_the_build_applies_is_the_carried_file(self):
        self.assertEqual(self.patches_block()[0].strip(), '"$ROOT/patches/wine/fenix-a320-linux-patch-cachyos-11.patch"')

    def test_the_build_no_longer_downloads_the_overlay_wine_patch(self):
        self.assertNotIn("download_overlay_patch", self.script)
        self.assertNotIn("raw.githubusercontent.com/marselnenaj", self.script)
        self.assertNotIn("OVERLAY_PATCH_URL", self.script)

    def test_the_build_checks_that_every_patch_exists_the_carried_one_included(self):
        preconditions = self.script[self.script.index("check_preconditions() {"):]
        self.assertIn('for patch in "${PATCHES[@]}" "${VKD3D_PATCHES[@]}"', preconditions[:preconditions.index("\n}\n")])

    def test_the_carried_file_exists_and_starts_with_its_licence_and_origin(self):
        header, _ = self.header_and_body()
        lines = header.splitlines()
        self.assertEqual(lines[0], "SPDX-License-Identifier: LGPL-2.1-or-later")
        self.assertIn("https://github.com/marselnenaj/fenix-a320-linux-patch", header)
        self.assertIn("v0.1.0-preview.7", header)
        self.assertIn("patches/proton/cachyos-11.patch", header)
        self.assertIn(FENIX_UPSTREAM_SHA256, header)
        self.assertIn("Copyright (c) 2026 Fenix A320 Linux Patch contributors", header)
        self.assertIn("GloriousEggroll", header)
        self.assertIn("patch -p1 --forward --fuzz=0 -i <this file>", lines[-1])

    def test_no_header_line_looks_like_a_patch_line(self):
        header, _ = self.header_and_body()
        for line in header.splitlines():
            self.assertIsNone(re.match(r"^(--- |\+\+\+ |\*\*\* |diff |Index:|Prereq:|@@ )", line), line)

    @unittest.skipUnless(shutil.which("patch"), "patch is not installed")
    def test_patch_itself_skips_the_header_and_applies_what_follows(self):
        header, _ = self.header_and_body()
        diff = "--- a/x.txt\n+++ b/x.txt\n@@ -1 +1 @@\n-old\n+new\n"
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "x.txt").write_text("old\n")
            carried = Path(temp) / "carried.patch"
            carried.write_text(f"{header}\n\n{diff}")
            result = subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", "-p1", "--dry-run", "-i", str(carried)], cwd=temp, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_body_of_the_carried_file_is_the_upstream_file_byte_for_byte(self):
        _, body = self.header_and_body()
        self.assertEqual(hashlib.sha256(body).hexdigest(), FENIX_UPSTREAM_SHA256)

    def test_the_manifest_still_records_the_overlay_line_that_addons_read(self):
        self.assertIn('OVERLAY_TAG="v0.1.0-preview.7"', self.script)
        self.assertIn('OVERLAY_VARIANT="cachyos-11"', self.script)
        self.assertIn('echo "overlay fenix-a320-linux-patch $OVERLAY_TAG $OVERLAY_VARIANT"', self.script)

    def run_guard(self, release):
        guard = next(line.strip() for line in self.script.splitlines() if line.strip().startswith('[[ "$RELEASE" == cachyos-11.*'))
        text = "\n".join(["set -euo pipefail", functions("die"), f'RELEASE="{release}"', guard])
        return subprocess.run(["bash", "-c", text], capture_output=True, text=True)

    def test_a_release_other_than_cachyos_11_stops_and_names_the_carried_patch(self):
        result = self.run_guard("cachyos-10.0-x")
        self.assertEqual(result.returncode, 1)
        self.assertIn("patches/wine/", result.stderr)
        self.assertIn("cachyos-10.0-x", result.stderr)

    def test_a_cachyos_11_release_passes_the_guard(self):
        result = self.run_guard("cachyos-11.0-20261015-slr")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
