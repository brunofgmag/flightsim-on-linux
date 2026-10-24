import contextlib
import importlib.util
import io
import json
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addons" / "fslabs-a321-textures"


def load_module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


textures = load_module("fslabs_textures_parts", ADDON / "parts" / "textures.py")


def level_size(width, height, block):
    return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * block


def dds(width, height, mips, fourcc=b"DXT1", dxgi=None):
    block = 8 if fourcc == b"DXT1" or dxgi == 71 else 16
    header = bytearray(128)
    header[0:4] = b"DDS "
    struct.pack_into("<IIIIIII", header, 4, 124, 0x000A1007, height, width, level_size(width, height, block), 0, mips)
    struct.pack_into("<I", header, 76, 32)
    struct.pack_into("<I", header, 80, 4)
    header[84:88] = fourcc
    struct.pack_into("<I", header, 108, 0x401008)
    if fourcc == b"DX10":
        header += struct.pack("<IIIII", dxgi, 3, 0, 1, 0)
    data = bytearray()
    for level in range(mips):
        size = level_size(max(1, width >> level), max(1, height >> level), block)
        data += bytes([level + 1]) * size
    return bytes(header + data)


def header_of(data):
    height, width = struct.unpack_from("<II", data, 12)
    linear = struct.unpack_from("<I", data, 20)[0]
    mips = struct.unpack_from("<I", data, 28)[0]
    return width, height, mips, linear


class ShrinkCase(unittest.TestCase):
    def test_dropping_one_level_keeps_the_rest_of_the_chain_byte_for_byte(self):
        original = dds(16, 16, 3)
        shrunk = textures.shrink(original, 8)
        self.assertEqual(header_of(shrunk), (8, 8, 2, level_size(8, 8, 8)))
        self.assertEqual(shrunk[128:], original[128 + level_size(16, 16, 8):])

    def test_dropping_two_levels_reaches_the_cap(self):
        shrunk = textures.shrink(dds(32, 16, 6), 8)
        self.assertEqual(header_of(shrunk)[:3], (8, 4, 4))

    def test_a_dx10_bc7_texture_keeps_its_extended_header(self):
        original = dds(16, 16, 5, b"DX10", 98)
        shrunk = textures.shrink(original, 8)
        self.assertEqual(header_of(shrunk), (8, 8, 4, level_size(8, 8, 16)))
        self.assertEqual(shrunk[128:148], original[128:148])
        self.assertEqual(shrunk[148:], original[148 + level_size(16, 16, 16):])

    def test_a_texture_already_within_the_cap_is_left_alone(self):
        self.assertIsNone(textures.shrink(dds(8, 8, 4), 8))

    def test_a_texture_without_enough_mip_levels_is_left_alone(self):
        self.assertIsNone(textures.shrink(dds(16, 16, 1), 8))

    def test_a_file_whose_size_does_not_match_its_header_is_left_alone(self):
        self.assertIsNone(textures.shrink(dds(16, 16, 3) + b"\0", 8))

    def test_an_unknown_format_is_left_alone(self):
        self.assertIsNone(textures.shrink(dds(16, 16, 3, b"DX10", 2), 8))


class PackageCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.package = Path(self.temp.name) / "fsl-a32x"
        self.backup = Path(self.temp.name) / "backup"
        airplane = self.package / "SimObjects" / "Airplanes" / "A321"
        (airplane / "Model").mkdir(parents=True)
        (airplane / "Texture").mkdir()
        self.write_gltf(airplane / "Model" / "FSLA32X_INTERIOR_LOD00.gltf", ["PANEL.PNG.DDS"])
        self.write_gltf(airplane / "Model" / "FSLA32X_LOD00.gltf", ["Fuse.png.dds", "PANEL.PNG.DDS"])
        self.files = {
            "panel": airplane / "Texture" / "PANEL.PNG.DDS",
            "fuse": airplane / "Texture" / "FUSE.PNG.DDS",
            "other": airplane / "Texture" / "UNUSED.PNG.DDS",
        }
        self.originals = {"panel": dds(32, 32, 6), "fuse": dds(32, 32, 6, b"DX10", 98), "other": dds(32, 32, 6)}
        for key, data in self.originals.items():
            self.files[key].write_bytes(data)
        self.write_layout()

    def write_gltf(self, file, uris):
        file.write_text(json.dumps({"images": [{"uri": uri} for uri in uris]}))

    def write_layout(self):
        content = []
        for file in sorted(self.package.rglob("*")):
            if file.is_file() and file.name != "layout.json":
                stat = file.stat()
                content.append({"path": file.relative_to(self.package).as_posix(), "size": stat.st_size, "date": textures.filetime(stat.st_mtime_ns)})
        (self.package / "layout.json").write_text(json.dumps({"content": content}))

    def layout_sizes(self):
        return {entry["path"]: entry["size"] for entry in json.loads((self.package / "layout.json").read_text())["content"]}

    def run_action(self, action, *extra):
        parsed = textures.parser().parse_args([action, str(self.package), str(self.backup), *extra])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = parsed.run(parsed)
        return code, output.getvalue().splitlines()

    def width(self, key):
        return header_of(self.files[key].read_bytes())[0]

    def test_check_fails_before_apply(self):
        code, lines = self.run_action("check", "--exterior", "8", "--cockpit", "16")
        self.assertNotEqual(code, 0)
        self.assertTrue(any(line.startswith("FAIL") for line in lines))

    def test_apply_caps_cockpit_and_exterior_textures_and_check_passes(self):
        code, _ = self.run_action("apply", "--exterior", "8", "--cockpit", "16")
        self.assertEqual(code, 0)
        self.assertEqual((self.width("panel"), self.width("fuse"), self.width("other")), (16, 8, 32))
        code, lines = self.run_action("check")
        self.assertEqual(code, 0, lines)
        self.assertTrue(all(line.startswith("ok") for line in lines))

    def test_apply_writes_the_new_sizes_into_layout_json(self):
        self.run_action("apply", "--exterior", "8", "--cockpit", "16")
        sizes = self.layout_sizes()
        for file in self.files.values():
            self.assertEqual(sizes[file.relative_to(self.package).as_posix()], file.stat().st_size)

    def test_check_fails_when_layout_json_lists_an_old_size(self):
        self.run_action("apply", "--exterior", "8", "--cockpit", "16")
        layout = json.loads((self.package / "layout.json").read_text())
        layout["content"][0]["size"] += 1
        (self.package / "layout.json").write_text(json.dumps(layout))
        code, lines = self.run_action("check")
        self.assertNotEqual(code, 0)
        self.assertTrue(any(line.startswith("FAIL") and "layout.json" in line for line in lines))

    def test_undo_restores_every_original_byte_and_check_fails_again(self):
        layout = (self.package / "layout.json").read_bytes()
        self.run_action("apply", "--exterior", "8", "--cockpit", "16")
        code, _ = self.run_action("undo")
        self.assertEqual(code, 0)
        for key, data in self.originals.items():
            self.assertEqual(self.files[key].read_bytes(), data)
        self.assertEqual(json.loads((self.package / "layout.json").read_bytes()), json.loads(layout))
        code, _ = self.run_action("check", "--exterior", "8", "--cockpit", "16")
        self.assertNotEqual(code, 0)

    def test_a_second_apply_keeps_the_first_backup(self):
        self.run_action("apply", "--exterior", "8", "--cockpit", "16")
        self.run_action("apply", "--exterior", "8", "--cockpit", "16")
        self.run_action("undo")
        self.assertEqual(self.files["fuse"].read_bytes(), self.originals["fuse"])

    def test_a_lower_cap_shrinks_further_and_undo_still_has_the_original(self):
        self.run_action("apply", "--exterior", "16", "--cockpit", "16")
        self.run_action("apply", "--exterior", "8", "--cockpit", "16")
        self.assertEqual(self.width("fuse"), 8)
        self.run_action("undo")
        self.assertEqual(self.files["fuse"].read_bytes(), self.originals["fuse"])

    def test_a_higher_cap_brings_back_the_larger_levels(self):
        self.run_action("apply", "--exterior", "8", "--cockpit", "16")
        self.run_action("apply", "--exterior", "32", "--cockpit", "16")
        self.assertEqual(self.files["fuse"].read_bytes(), self.originals["fuse"])
        self.assertEqual(self.layout_sizes()[self.files["fuse"].relative_to(self.package).as_posix()], len(self.originals["fuse"]))

    def test_a_texture_the_updater_replaced_gets_a_new_backup(self):
        self.run_action("apply", "--exterior", "8", "--cockpit", "16")
        updated = dds(64, 64, 7, b"DX10", 98)
        self.files["fuse"].write_bytes(updated)
        self.write_layout()
        code, lines = self.run_action("check")
        self.assertNotEqual(code, 0)
        self.run_action("apply")
        self.assertEqual(self.width("fuse"), 8)
        self.run_action("undo")
        self.assertEqual(self.files["fuse"].read_bytes(), updated)

    def test_undo_leaves_a_texture_the_updater_replaced_after_apply(self):
        self.run_action("apply", "--exterior", "8", "--cockpit", "16")
        updated = dds(64, 64, 7)
        self.files["panel"].write_bytes(updated)
        self.run_action("undo")
        self.assertEqual(self.files["panel"].read_bytes(), updated)

    def test_a_package_without_layout_json_fails_without_a_traceback(self):
        (self.package / "layout.json").unlink()
        code, lines = self.run_action("check")
        self.assertNotEqual(code, 0)
        self.assertTrue(lines and lines[-1].startswith("FAIL"))


class ScriptCase(unittest.TestCase):
    def test_the_installed_glob_points_at_the_fslabs_package(self):
        data = json.loads((ADDON / "addon.json").read_text())
        self.assertEqual(data["installed"], {"community": ["fsl-a32x/layout.json"]})
        self.assertTrue(data["touches_addon_files"])

    def test_check_without_a_community_folder_fails(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith("FSOL_")}
        result = subprocess.run([str(ADDON / "check")], cwd=ADDON, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
