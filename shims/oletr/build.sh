#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
[ $# -eq 1 ] || { echo "usage: $(basename "$0") <output folder>" >&2; exit 2; }
command -v x86_64-w64-mingw32-gcc > /dev/null || { echo "x86_64-w64-mingw32-gcc is missing: install mingw-w64-gcc (Arch), gcc-mingw-w64-x86-64 (Debian, Ubuntu) or mingw64-gcc (Fedora)" >&2; exit 1; }
mkdir -p "$1"
x86_64-w64-mingw32-gcc -O2 -Wno-attributes -shared -s -static-libgcc -o "$1/oletr.dll" "$HERE/ole32_shim.c" "$HERE/oletr.def"
