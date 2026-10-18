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
x86_64-w64-mingw32-gcc -O2 -o "$OUT/d2d1-geometry-leak-test.exe" "$HERE/src/d2d1-geometry-leak-test.c" -ld2d1 -luuid
x86_64-w64-mingw32-gcc -O2 -o "$OUT/d2d1-non-finite-close-test.exe" "$HERE/src/d2d1-non-finite-close-test.c" -ld2d1 -luuid
x86_64-w64-mingw32-gcc -O2 -o "$OUT/d2d1-bezier-ceiling-test.exe" "$HERE/src/d2d1-bezier-ceiling-test.c" -ld2d1 -luuid
x86_64-w64-mingw32-gcc -O2 -o "$OUT/d3d12-cross-process-fence-test.exe" "$HERE/src/d3d12-cross-process-fence-test.c" -ld3d12 -ld3d11 -ldxguid -luuid
x86_64-w64-mingw32-gcc -O2 -o "$OUT/dxgi-shared-resource-luid-test.exe" "$HERE/src/dxgi-shared-resource-luid-test.c" -ld3d12 -ld3d11 -ldxgi -luuid
x86_64-w64-mingw32-gcc -O2 -o "$OUT/d3d11-keyed-mutex-test.exe" "$HERE/src/d3d11-keyed-mutex-test.c" -ld3d11 -ldxgi -luuid
