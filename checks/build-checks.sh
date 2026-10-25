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
x86_64-w64-mingw32-gcc -O2 -o "$OUT/d3d12-identical-root-signature-test.exe" "$HERE/src/d3d12-identical-root-signature-test.c" -ld3d12 -luuid
x86_64-w64-mingw32-gcc -O2 -o "$OUT/d3d12-descriptor-heap-addresses-test.exe" "$HERE/src/d3d12-descriptor-heap-addresses-test.c" -ld3d12 -luuid
x86_64-w64-mingw32-gcc -O2 -o "$OUT/d3d11-keyed-mutex-test.exe" "$HERE/src/d3d11-keyed-mutex-test.c" -ld3d11 -ldxgi -luuid
x86_64-w64-mingw32-gcc -O2 -o "$OUT/process-memory-after-eperm-test.exe" "$HERE/src/process-memory-after-eperm-test.c" -Wl,--disable-dynamicbase
x86_64-w64-mingw32-gcc -O2 -o "$OUT/hidden-cursor-clip-test.exe" "$HERE/src/hidden-cursor-clip-test.c" -lgdi32 -luser32
x86_64-w64-mingw32-gcc -O2 -o "$OUT/pointer-constraint-commit-test.exe" "$HERE/src/pointer-constraint-commit-test.c" -lgdi32 -luser32
x86_64-w64-mingw32-gcc -O2 -o "$OUT/one-pixel-clip-test.exe" "$HERE/src/one-pixel-clip-test.c" -lgdi32 -luser32
x86_64-w64-mingw32-gcc -O2 -o "$OUT/hidden-gl-swap-pacing-test.exe" "$HERE/src/hidden-gl-swap-pacing-test.c" -lopengl32 -lgdi32 -luser32
x86_64-w64-mingw32-gcc -O2 -o "$OUT/applicationdata-no-package-test.exe" "$HERE/src/applicationdata-no-package-test.c" -lruntimeobject
x86_64-w64-mingw32-gcc -O2 -o "$OUT/wintrust-create-indirect-data-test.exe" "$HERE/src/wintrust-create-indirect-data-test.c" -lwintrust -lcrypt32 -lbcrypt
x86_64-w64-mingw32-gcc -O2 -o "$OUT/globalroot-device-path-test.exe" "$HERE/src/globalroot-device-path-test.c"
x86_64-w64-mingw32-gcc -O2 -o "$OUT/device-path-current-directory-test.exe" "$HERE/src/device-path-current-directory-test.c"
x86_64-w64-mingw32-gcc -O2 -o "$OUT/heap-read-slack-test.exe" "$HERE/src/heap-read-slack-test.c"
x86_64-w64-mingw32-gcc -O2 -o "$OUT/bcrypt-pbkdf2-alg-handle-test.exe" "$HERE/src/bcrypt-pbkdf2-alg-handle-test.c" -lbcrypt
x86_64-w64-mingw32-gcc -O2 -o "$OUT/ncrypt-ecdh-p256-test.exe" "$HERE/src/ncrypt-ecdh-p256-test.c" -lncrypt -lbcrypt
if pkg-config --exists libei-1.0 libsystemd; then
  read -r -a EIS_FLAGS < <(pkg-config --cflags --libs libei-1.0 libsystemd)
  gcc -O2 -Wall -o "$OUT/eis-pointer" "$HERE/src/eis-pointer.c" "${EIS_FLAGS[@]}"
fi
