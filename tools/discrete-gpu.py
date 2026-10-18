#!/usr/bin/env python3
import re
import shutil
import subprocess
import sys

DISCRETE = "PHYSICAL_DEVICE_TYPE_DISCRETE_GPU"
CPU = "PHYSICAL_DEVICE_TYPE_CPU"


class Ambiguous(Exception):
    pass


def devices(summary):
    found = []
    for block in re.split(r"^GPU\d+:\s*$", summary, flags=re.MULTILINE)[1:]:
        fields = dict(re.findall(r"^\s*(\w+)\s*=\s*(.*?)\s*$", block, flags=re.MULTILINE))
        if "deviceName" in fields:
            found.append((fields["deviceName"], fields.get("deviceType", "")))
    return found


def filter_name(summary):
    gpus = [(name, kind) for name, kind in devices(summary) if kind != CPU]
    if len(gpus) < 2:
        return None
    discrete = {name for name, kind in gpus if kind == DISCRETE}
    if len(discrete) != 1:
        raise Ambiguous(f"{len(gpus)} GPUs and {len(discrete)} distinct discrete ones: " + "; ".join(name for name, _ in gpus))
    return discrete.pop()


def main():
    if not shutil.which("vulkaninfo"):
        print("vulkaninfo is missing; install your distribution's vulkan-tools package", file=sys.stderr)
        return 2
    summary = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True).stdout
    try:
        name = filter_name(summary)
    except Ambiguous as error:
        print(f"cannot pick one GPU: {error}", file=sys.stderr)
        return 3
    if name:
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
