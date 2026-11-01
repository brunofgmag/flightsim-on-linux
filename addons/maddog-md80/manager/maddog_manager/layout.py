import json
import os
from collections import deque
from pathlib import Path

FILETIME_EPOCH = 116444736000000000
EXCLUDED = {"layout.json", "manifest.json"}


def filetime(stat):
    return stat.st_mtime_ns // 100 + FILETIME_EPOCH


def package_files(package):
    package = Path(package)
    pending = deque([package])
    while pending:
        folder = pending.popleft()
        entries = sorted(os.scandir(folder), key=lambda entry: entry.name.upper())
        for entry in entries:
            if entry.is_file():
                yield Path(entry.path).relative_to(package).as_posix(), entry.stat()
        pending.extend(Path(entry.path) for entry in entries if entry.is_dir())


def build_layout(package):
    content = []
    total = 0
    for path, stat in package_files(package):
        if path == "manifest.json":
            total += stat.st_size
        if path in EXCLUDED or path.upper().startswith("_CVT_"):
            continue
        content.append({"path": path, "size": stat.st_size, "date": filetime(stat)})
        total += stat.st_size
    return content, total


def write_layout(package):
    package = Path(package)
    content, total = build_layout(package)
    layout = package / "layout.json"
    layout.write_text(json.dumps({"content": content}, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    total += layout.stat().st_size
    manifest = package / "manifest.json"
    if not manifest.is_file():
        return
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except ValueError:
        return
    if not isinstance(data, dict) or "total_package_size" not in data:
        return
    data["total_package_size"] = str(total).zfill(20)
    text = json.dumps(data, indent=2, ensure_ascii=False).replace("\n", "\r\n")
    manifest.write_bytes(text.encode("utf-8"))
