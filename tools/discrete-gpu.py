#!/usr/bin/env python3
import re
import shutil
import subprocess
import sys
from typing import NamedTuple, Optional

DISCRETE = "PHYSICAL_DEVICE_TYPE_DISCRETE_GPU"
CPU = "PHYSICAL_DEVICE_TYPE_CPU"


class Ambiguous(Exception):
    pass


class Gpu(NamedTuple):
    name: str
    kind: str
    vendor: str
    device: str


def devices(summary):
    found = []
    for block in re.split(r"^GPU\d+:\s*$", summary, flags=re.MULTILINE)[1:]:
        fields = dict(re.findall(r"^\s*(\w+)\s*=\s*(.*?)\s*$", block, flags=re.MULTILINE))
        if "deviceName" in fields:
            found.append(Gpu(fields["deviceName"], fields.get("deviceType", ""), fields.get("vendorID", ""), fields.get("deviceID", "")))
    return found


def discrete_choice(summary) -> Optional[Gpu]:
    gpus = [gpu for gpu in devices(summary) if gpu.kind != CPU]
    if len(gpus) < 2:
        return None
    discrete = {gpu.name: gpu for gpu in gpus if gpu.kind == DISCRETE}
    if len(discrete) != 1:
        raise Ambiguous(f"{len(gpus)} GPUs and {len(discrete)} distinct discrete ones: " + "; ".join(gpu.name for gpu in gpus))
    return next(iter(discrete.values()))


def filter_name(summary):
    gpu = discrete_choice(summary)
    return gpu.name if gpu else None


def pci_id(value):
    match = re.fullmatch(r"(?:0[xX])?([0-9a-fA-F]{1,4})", value)
    return f"{int(match.group(1), 16):04x}" if match else None


def vk_device(summary):
    gpu = discrete_choice(summary)
    if gpu is None:
        return None
    vendor, device = pci_id(gpu.vendor), pci_id(gpu.device)
    if not vendor or not device:
        raise Ambiguous(f"vulkaninfo gives no vendorID and deviceID for {gpu.name}")
    return f"{vendor}:{device}"


def main(argv):
    if argv not in ([], ["--vk-device"]):
        print("usage: discrete-gpu.py [--vk-device]", file=sys.stderr)
        return 64
    if not shutil.which("vulkaninfo"):
        print("vulkaninfo is missing; install your distribution's vulkan-tools package", file=sys.stderr)
        return 2
    summary = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True).stdout
    pick = vk_device if argv else filter_name
    try:
        answer = pick(summary)
    except Ambiguous as error:
        print(f"cannot pick one GPU: {error}", file=sys.stderr)
        return 3
    if answer:
        print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
