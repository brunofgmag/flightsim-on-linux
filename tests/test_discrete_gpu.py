import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "discrete-gpu.py"
spec = importlib.util.spec_from_file_location("discrete_gpu", SCRIPT)
discrete_gpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discrete_gpu)


def summary(*gpus):
    blocks = "".join(
        f"GPU{number}:\n\tvendorID           = {(ids or ('0x0000', '0x0000'))[0]}\n\tdeviceID           = {(ids or ('0x0000', '0x0000'))[1]}\n\tdeviceType         = PHYSICAL_DEVICE_TYPE_{kind}\n\tdeviceName         = {name}\n\tdriverName         = x\n"
        for number, (kind, name, *ids) in enumerate(gpus)
    )
    return f"Devices:\n========\n{blocks}"


RTX = ("DISCRETE_GPU", "NVIDIA GeForce RTX 4080", "0x10de", "0x2704")
RADV = ("INTEGRATED_GPU", "AMD Ryzen 9 9950X3D 16-Core Processor (RADV RAPHAEL_MENDOCINO)", "0x1002", "0x13c0")
LLVMPIPE = ("CPU", "llvmpipe (LLVM 23.1.1, 256 bits)")


class DiscreteGpuTest(unittest.TestCase):
    def test_one_gpu_needs_no_filter(self):
        self.assertIsNone(discrete_gpu.filter_name(summary(RTX)))
        self.assertIsNone(discrete_gpu.filter_name(summary(RADV)))

    def test_a_software_device_does_not_count_as_a_second_gpu(self):
        self.assertIsNone(discrete_gpu.filter_name(summary(RTX, LLVMPIPE)))

    def test_with_two_gpus_the_discrete_one_is_named(self):
        self.assertEqual(discrete_gpu.filter_name(summary(RADV, RTX, LLVMPIPE)), "NVIDIA GeForce RTX 4080")

    def test_two_gpus_without_one_discrete_choice_are_ambiguous(self):
        with self.assertRaises(discrete_gpu.Ambiguous):
            discrete_gpu.filter_name(summary(RADV, ("INTEGRATED_GPU", "Intel(R) Graphics")))
        with self.assertRaises(discrete_gpu.Ambiguous):
            discrete_gpu.filter_name(summary(RTX, ("DISCRETE_GPU", "AMD Radeon RX 7900 XTX")))

    def test_two_identical_discrete_gpus_share_one_filter(self):
        self.assertEqual(discrete_gpu.filter_name(summary(RTX, RTX)), "NVIDIA GeForce RTX 4080")

    def test_vk_device_is_the_discrete_gpus_vendor_and_device_in_gamescope_form(self):
        self.assertEqual(discrete_gpu.vk_device(summary(RADV, RTX, LLVMPIPE)), "10de:2704")

    def test_vk_device_pads_short_ids_and_lowercases(self):
        discrete = ("DISCRETE_GPU", "Some Card", "0x10DE", "0xA1")
        self.assertEqual(discrete_gpu.vk_device(summary(RADV, discrete)), "10de:00a1")

    def test_vk_device_is_empty_with_one_gpu_and_ambiguous_with_two_integrated(self):
        self.assertIsNone(discrete_gpu.vk_device(summary(RTX, LLVMPIPE)))
        with self.assertRaises(discrete_gpu.Ambiguous):
            discrete_gpu.vk_device(summary(RADV, ("INTEGRATED_GPU", "Intel(R) Graphics")))

    def test_vk_device_without_ids_is_ambiguous(self):
        blocks = "GPU0:\n\tdeviceType = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU\n\tdeviceName = A\nGPU1:\n\tdeviceType = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU\n\tdeviceName = B\n"
        with self.assertRaises(discrete_gpu.Ambiguous):
            discrete_gpu.vk_device(blocks)

    def run_script(self, path, *options):
        env = {**os.environ, "PATH": path}
        return subprocess.run([str(SCRIPT), *options], capture_output=True, text=True, env=env)

    def test_two_integrated_gpus_from_vulkaninfo_exit_3_for_both_options(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / "vulkaninfo"
            text = summary(RADV, ("INTEGRATED_GPU", "Intel(R) Graphics"))
            fake.write_text(f"#!/bin/sh\n[ \"$1\" = --summary ] || exit 64\ncat <<'EOF'\n{text}EOF\n")
            fake.chmod(0o755)
            for options in ([], ["--vk-device"]):
                result = self.run_script(f"{temp}:{os.environ['PATH']}", *options)
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertIn("cannot pick one GPU", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_a_discrete_gpu_from_vulkaninfo_is_printed_in_each_form(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / "vulkaninfo"
            text = summary(RADV, RTX)
            fake.write_text(f"#!/bin/sh\n[ \"$1\" = --summary ] || exit 64\ncat <<'EOF'\n{text}EOF\n")
            fake.chmod(0o755)
            path = f"{temp}:{os.environ['PATH']}"
            self.assertEqual(self.run_script(path).stdout, "NVIDIA GeForce RTX 4080\n")
            self.assertEqual(self.run_script(path, "--vk-device").stdout, "10de:2704\n")

    def test_without_vulkaninfo_on_the_path_it_exits_2_and_names_the_package(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "python3").symlink_to(shutil.which("python3"))
            result = self.run_script(temp)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("vulkaninfo is missing", result.stderr)

    def test_an_unknown_option_is_refused(self):
        result = subprocess.run([str(SCRIPT), "--nope"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 64)


if __name__ == "__main__":
    unittest.main()
