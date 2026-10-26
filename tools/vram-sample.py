#!/usr/bin/env python3
import argparse
import bisect
import csv
import ctypes
import re
import signal
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

MIB = 1024 * 1024
NOT_AVAILABLE = 0xFFFFFFFFFFFFFFFF
INSUFFICIENT_SIZE = 7
PCIE_TX, PCIE_RX = 0, 1
DEFAULT_FOLDER = Path.home() / ".cache" / "flightsim-on-linux" / "vram"
MARKS = "marks.csv"
FIELDS = ["time", "gpu_used_mib", "gpu_total_mib", "gpu_util", "pcie_rx_kbs", "pcie_tx_kbs", "pid", "process", "used_mib"]


class Memory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class Utilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [("pid", ctypes.c_uint), ("usedGpuMemory", ctypes.c_ulonglong), ("gpuInstanceId", ctypes.c_uint), ("computeInstanceId", ctypes.c_uint)]


class NvmlError(Exception):
    pass


class Gpu:
    def __init__(self, index):
        try:
            self.nvml = ctypes.CDLL("libnvidia-ml.so.1")
        except OSError as error:
            raise NvmlError(f"cannot load libnvidia-ml.so.1: {error}")
        self.call("nvmlInit_v2")
        self.handle = ctypes.c_void_p()
        self.call("nvmlDeviceGetHandleByIndex_v2", ctypes.c_uint(index), ctypes.byref(self.handle))
        name = ctypes.create_string_buffer(96)
        self.call("nvmlDeviceGetName", self.handle, name, ctypes.c_uint(len(name)))
        self.name = name.value.decode()

    def call(self, function, *args):
        status = getattr(self.nvml, function)(*args)
        if status:
            raise NvmlError(f"{function} returned {status}")

    def memory(self):
        memory = Memory()
        self.call("nvmlDeviceGetMemoryInfo", self.handle, ctypes.byref(memory))
        return memory.used / MIB, memory.total / MIB

    def utilization(self):
        rates = Utilization()
        self.call("nvmlDeviceGetUtilizationRates", self.handle, ctypes.byref(rates))
        return rates.gpu

    def pcie(self, counter):
        value = ctypes.c_uint()
        self.call("nvmlDeviceGetPcieThroughput", self.handle, ctypes.c_int(counter), ctypes.byref(value))
        return value.value

    def processes(self):
        usage = {}
        for function in ("nvmlDeviceGetGraphicsRunningProcesses_v3", "nvmlDeviceGetComputeRunningProcesses_v3"):
            size = 64
            while True:
                count = ctypes.c_uint(size)
                infos = (ProcessInfo * size)()
                status = getattr(self.nvml, function)(self.handle, ctypes.byref(count), infos)
                if status == INSUFFICIENT_SIZE:
                    size = count.value + 16
                    continue
                if status:
                    raise NvmlError(f"{function} returned {status}")
                break
            for info in infos[:count.value]:
                if info.usedGpuMemory != NOT_AVAILABLE:
                    usage[info.pid] = max(usage.get(info.pid, 0), info.usedGpuMemory / MIB)
        return usage


def process_name(pid, names):
    if pid not in names:
        try:
            argv0 = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[0].decode(errors="replace")
        except OSError:
            argv0 = ""
        names[pid] = argv0.replace("\\", "/").rsplit("/", 1)[-1] or f"pid {pid}"
    return names[pid]


def record(arguments):
    gpu = Gpu(arguments.gpu)
    arguments.folder.mkdir(parents=True, exist_ok=True)
    path = arguments.folder / time.strftime("vram-%Y-%m-%d_%H-%M-%S.csv")
    stop = []
    signal.signal(signal.SIGINT, lambda *_: stop.append(True))
    signal.signal(signal.SIGTERM, lambda *_: stop.append(True))
    names = {}
    samples = 0
    print(f"recording {gpu.name} every {arguments.interval} s into {path}; Ctrl+C stops", file=sys.stderr)
    with path.open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(FIELDS)
        while not stop:
            started = time.monotonic()
            now = time.time()
            used, total = gpu.memory()
            totals = [f"{now:.3f}", f"{used:.0f}", f"{total:.0f}", gpu.utilization(), gpu.pcie(PCIE_RX), gpu.pcie(PCIE_TX)]
            for pid, mib in sorted(gpu.processes().items()):
                writer.writerow(totals + [pid, process_name(pid, names), f"{mib:.0f}"])
            writer.writerow(totals + ["", "", ""])
            output.flush()
            samples += 1
            if samples % max(1, round(10 / arguments.interval)) == 0:
                print(f"\r{time.strftime('%H:%M:%S')}  {used:6.0f} / {total:.0f} MiB  {samples} samples", end="", file=sys.stderr)
            time.sleep(max(0.0, arguments.interval - (time.monotonic() - started)))
    print(f"\nwrote {samples} samples to {path}", file=sys.stderr)
    return 0


class Sample:
    def __init__(self, row):
        self.time = float(row["time"])
        self.used = float(row["gpu_used_mib"])
        self.total = float(row["gpu_total_mib"])
        self.pcie_rx = int(row["pcie_rx_kbs"])
        self.pcie_tx = int(row["pcie_tx_kbs"])
        self.processes = {}


def load_samples(path):
    samples = []
    with path.open(newline="") as source:
        for row in csv.DictReader(source):
            if not samples or float(row["time"]) != samples[-1].time:
                samples.append(Sample(row))
            if row["pid"]:
                samples[-1].processes[f"{row['process']} ({row['pid']})"] = float(row["used_mib"])
    return samples


def log_start(path, last_elapsed):
    match = re.search(r"_(\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d)\.csv$", path.name)
    if not match:
        return path.stat().st_mtime - last_elapsed
    named = time.mktime(time.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S"))
    from_mtime = path.stat().st_mtime - last_elapsed
    return from_mtime if named <= from_mtime < named + 1 else named + 0.5


def load_frames(path):
    lines = path.read_text().splitlines()
    header = next((index for index, line in enumerate(lines) if line.startswith("fps,frametime")), None)
    rows = list(csv.DictReader(lines[header:])) if header is not None else []
    if not rows:
        return []
    elapsed = [int(row["elapsed"]) for row in rows]
    start = log_start(path, elapsed[-1] / 1e9)
    gaps = [float(rows[0]["frametime"])] + [(later - earlier) / 1e6 for earlier, later in zip(elapsed, elapsed[1:])]
    return [(start + moment / 1e9, gap) for moment, gap in zip(elapsed, gaps)]


def load_marks(path):
    if not path.exists():
        return []
    with path.open(newline="") as source:
        return sorted((float(row[0]), row[1]) for row in csv.reader(source) if len(row) == 2)


def mark(arguments):
    arguments.folder.mkdir(parents=True, exist_ok=True)
    with (arguments.folder / MARKS).open("a", newline="") as output:
        csv.writer(output).writerow([f"{time.time():.3f}", arguments.label])
    print(f"{time.strftime('%H:%M:%S')} {arguments.label}", file=sys.stderr)
    return 0


def sample_at(samples, times, moment):
    index = bisect.bisect_right(times, moment) - 1
    return samples[max(0, index)]


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def clock(moment):
    return f"{time.strftime('%H:%M:%S', time.localtime(moment))}.{int(moment % 1 * 1000):03d}"


def describe_memory(window, margin):
    peak = max(window, key=lambda sample: sample.used)
    near_full = sum(1 for sample in window if sample.total - sample.used < margin)
    print(f"  VRAM peak {peak.used:.0f} of {peak.total:.0f} MiB at {clock(peak.time)}; "
          f"{100 * near_full / len(window):.0f}% of samples with less than {margin} MiB free")
    print(f"  PCIe to GPU: median {statistics.median(s.pcie_rx for s in window) / 1024:.0f} MB/s, "
          f"peak {max(s.pcie_rx for s in window) / 1024:.0f} MB/s")
    peaks = defaultdict(float)
    for sample in window:
        for process, mib in sample.processes.items():
            peaks[process] = max(peaks[process], mib)
    for process, mib in sorted(peaks.items(), key=lambda item: -item[1])[:8]:
        print(f"    {mib:7.0f} MiB  {process}")


def describe_frames(frames, samples, times, margin):
    frametimes = [frametime for _, frametime in frames]
    median = statistics.median(frametimes)
    threshold = max(3 * median, 25.0)
    spikes = [(moment, frametime) for moment, frametime in frames if frametime > threshold]
    print(f"  {len(frames)} frames, median {median:.1f} ms, p99 {percentile(frametimes, 0.99):.1f} ms, "
          f"{len(spikes)} frames over {threshold:.1f} ms")
    if not spikes:
        return
    at_spike = [sample_at(samples, times, moment) for moment, _ in spikes]
    tight = sum(1 for sample in at_spike if sample.total - sample.used < margin)
    print(f"  {tight} of {len(spikes)} spikes with less than {margin} MiB free")
    for (moment, frametime), sample in sorted(zip(spikes, at_spike), key=lambda pair: -pair[0][1])[:12]:
        print(f"    {clock(moment)}  {frametime:7.1f} ms  "
              f"{sample.used:6.0f} MiB used, {sample.total - sample.used:5.0f} free, PCIe to GPU {sample.pcie_rx / 1024:5.0f} MB/s")


def segments(begin, end, marks):
    inside = [(moment, label) for moment, label in marks if begin < moment < end]
    edges = [(begin, "before the first mark" if inside else "whole log")] + inside
    return [(start, edges[index + 1][0] if index + 1 < len(edges) else end, label) for index, (start, label) in enumerate(edges)]


def newest(folder, pattern):
    found = sorted(folder.glob(pattern), key=lambda path: path.stat().st_mtime)
    return found[-1] if found else None


def report(arguments):
    recording = arguments.samples or newest(arguments.folder, "vram-*.csv")
    if recording is None:
        print(f"no recording in {arguments.folder}", file=sys.stderr)
        return 1
    samples = load_samples(recording)
    if not samples:
        print(f"{recording} holds no samples", file=sys.stderr)
        return 1
    times = [sample.time for sample in samples]
    marks = load_marks(recording.parent / MARKS)
    logs = arguments.frames or sorted(path for path in recording.parent.glob("*.csv")
                                      if not path.name.startswith("vram-") and path.name != MARKS and not path.name.endswith("_summary.csv"))
    print(f"{recording.name}: {clock(samples[0].time)} to {clock(samples[-1].time)}")
    timelines = [(path.name, load_frames(path)) for path in logs]
    timelines = [(name, frames) for name, frames in timelines if frames and frames[0][0] <= samples[-1].time and frames[-1][0] >= samples[0].time]
    if not timelines:
        timelines = [(None, [])]
    for name, frames in timelines:
        begin, end = (frames[0][0], frames[-1][0]) if frames else (samples[0].time, samples[-1].time)
        for start, stop, label in segments(begin, end, marks):
            window = [sample for sample in samples if start <= sample.time <= stop]
            print(f"\n{name or 'no frame log'}, {label}: {clock(start)} to {clock(stop)}")
            if not window:
                print("  no VRAM samples in this stretch")
                continue
            describe_memory(window, arguments.margin)
            stretch = [frame for frame in frames if start <= frame[0] < stop]
            if stretch:
                describe_frames(stretch, samples, times, arguments.margin)
    return 0


def main(argv):
    parser = argparse.ArgumentParser(description="Sample NVIDIA VRAM per process and match it to MangoHud frame logs.")
    parser.add_argument("--folder", type=Path, default=DEFAULT_FOLDER)
    commands = parser.add_subparsers(dest="command", required=True)
    recorder = commands.add_parser("record")
    recorder.add_argument("--interval", type=float, default=0.25)
    recorder.add_argument("--gpu", type=int, default=0)
    marker = commands.add_parser("mark")
    marker.add_argument("label")
    reporter = commands.add_parser("report")
    reporter.add_argument("samples", type=Path, nargs="?")
    reporter.add_argument("frames", type=Path, nargs="*")
    reporter.add_argument("--margin", type=int, default=512)
    arguments = parser.parse_args(argv)
    actions = {"record": record, "mark": mark, "report": report}
    try:
        return actions[arguments.command](arguments)
    except NvmlError as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
