#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
INSTALLER="${1:-$HOME/Downloads/TriTriSim Hub_1.2.2_x64-setup.exe}"
export FSOL_PROTON="${2:-${FSOL_PROTON:-$HOME/.local/share/Steam/compatibilitytools.d/Proton-FS2024}}"

[ -f "$INSTALLER" ] || { echo "installer not found: $INSTALLER" >&2; exit 2; }
[ -x "$FSOL_PROTON/files/bin/wine" ] || { echo "no Proton at $FSOL_PROTON" >&2; exit 2; }
command -v x86_64-w64-mingw32-gcc > /dev/null || { echo "x86_64-w64-mingw32-gcc is missing" >&2; exit 2; }

work="$(mktemp -d /tmp/fsol-XXXXXX)"
case "$work" in /tmp/fsol-*) ;; *) echo "unexpected work folder $work" >&2; exit 2 ;; esac
export FSOL_PREFIX="$work/pfx" FSOL_CACHE="$work/cache" FSOL_ADDON=manual-test FSOL_SIM_RUNNING=0 FSOL_CLI=/nonexistent
mkdir -p "$FSOL_PREFIX" "$FSOL_CACHE"
# shellcheck source=tools/addon-launch.sh
. "$ROOT/tools/addon-launch.sh"

trap 'addon_prefix_run "$FSOL_PREFIX" wineserver -k > /dev/null 2>&1 || true; rm -rf "$work"' EXIT

status=0
report() {
  if [ "$1" = ok ]; then echo "ok $2"; else echo "FAIL $2"; status=1; fi
}

echo "disposable prefix: $FSOL_PREFIX"
addon_prefix_run "$FSOL_PREFIX" wineboot -u > /dev/null 2>&1 || true

code=0
addon_alone "$INSTALLER" /S || code=$?
installed="$(find "$FSOL_PREFIX/drive_c" -iname '*.exe' -path '*TriTriSim*' ! -iname '*uninst*' -print -quit 2> /dev/null || true)"
echo "installer exit code: $code"
if [ -n "$installed" ]; then report ok "the installed program appeared: ${installed#"$FSOL_PREFIX"/}"; else report FAIL "no TriTriSim program appeared under drive_c"; fi
held="$(python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from fsol import machine; print(machine.prefix_wineserver(Path(sys.argv[2]), Path("/proc")) is not None)' "$ROOT" "$FSOL_PREFIX")"
if [ "$held" = True ]; then report FAIL "a wineserver still holds the prefix after the installer"; else report ok "no wineserver holds the prefix after the installer"; fi

x86_64-w64-mingw32-gcc -mwindows -o "$work/closetest.exe" "$HERE/close-window.c"
marker="$work/closed"
addon_in_prefix "$work/closetest.exe" "$(windows_path "$marker")" > /dev/null 2>&1 &
for ((second = 0; second < 30; second++)); do
  pgrep -f 'closetest[.]exe' > /dev/null && break
  sleep 1
done
if pgrep -f 'closetest[.]exe' > /dev/null; then report ok "a program with a hidden window runs in the prefix"; else report FAIL "the test program did not start"; fi
sleep 2
addon_in_prefix taskkill /im closetest.exe > /dev/null 2>&1 || true
for ((second = 0; second < 15; second++)); do
  [ -e "$marker" ] && break
  sleep 1
done
if [ -e "$marker" ]; then report ok "taskkill without /f delivered WM_CLOSE to its window"; else report FAIL "taskkill without /f did not reach the window"; fi
for ((second = 0; second < 15; second++)); do
  pgrep -f 'closetest[.]exe' > /dev/null || break
  sleep 1
done
if pgrep -f 'closetest[.]exe' > /dev/null; then report FAIL "the program is still running after its window closed"; else report ok "the program ended after its window closed"; fi
exit "$status"
