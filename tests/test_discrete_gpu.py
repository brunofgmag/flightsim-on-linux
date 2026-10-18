import importlib.util
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "discrete-gpu.py"
spec = importlib.util.spec_from_file_location("discrete_gpu", SCRIPT)
discrete_gpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discrete_gpu)


def summary(*gpus):
    blocks = "".join(f"GPU{number}:\n\tdeviceType         = PHYSICAL_DEVICE_TYPE_{kind}\n\tdeviceName         = {name}\n\tdriverName         = x\n" for number, (kind, name) in enumerate(gpus))
    return f"Devices:\n========\n{blocks}"


RTX = ("DISCRETE_GPU", "NVIDIA GeForce RTX 4080")
RADV = ("INTEGRATED_GPU", "AMD Ryzen 9 9950X3D 16-Core Processor (RADV RAPHAEL_MENDOCINO)")
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

    def test_the_real_vulkaninfo_gives_an_answer(self):
        if not shutil.which("vulkaninfo"):
            self.skipTest("no vulkaninfo here")
        result = subprocess.run([str(SCRIPT)], capture_output=True, text=True)
        self.assertIn(result.returncode, (0, 3), result.stderr)


if __name__ == "__main__":
    unittest.main()
