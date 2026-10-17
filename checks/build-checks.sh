#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
[ $# -eq 1 ] || { echo "usage: $(basename "$0") <output folder>" >&2; exit 2; }
OUT="$1"
mkdir -p "$OUT"

x86_64-w64-mingw32-gcc -O2 -o "$OUT/notifyicon-guid-test.exe" "$HERE/src/notifyicon-guid-test.c" -lshell32 -luser32
x86_64-w64-mingw32-gcc -O2 -o "$OUT/pathgradient-test.exe" "$HERE/src/pathgradient-test.c" -lgdiplus
x86_64-w64-mingw32-gcc -O2 -municode -o "$OUT/centered-point-test.exe" "$HERE/src/centered-point-test.c" -lgdiplus -lgdi32
x86_64-w64-mingw32-gcc -O2 -o "$OUT/cross-process-child-test.exe" "$HERE/src/cross-process-child-test.c" -lgdi32 -luser32
