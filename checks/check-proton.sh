#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
# shellcheck source=/dev/null
. "$HERE/../tools/proton-runner.sh"
CACHE="${FSOL_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/flightsim-on-linux}"
OVERLAY_TAG="v0.1.0-preview.7"
PROBE_URL="https://raw.githubusercontent.com/marselnenaj/fenix-a320-linux-patch/$OVERLAY_TAG/tests/loader-case-probe.c"
PROBE_SHA256="ee85e7d218b950111e2ea05e93fae5a46ad4934181adb5ec97e5a821c0887c38"

die() {
  echo "check-proton: $*" >&2
  exit 2
}

[ $# -eq 1 ] || die "usage: $(basename "$0") <Proton folder>"
TOOL="$(readlink -f "$1")"
[ -x "$TOOL/files/bin/wine" ] || die "$TOOL is not a Proton folder (no files/bin/wine)"
command -v x86_64-w64-mingw32-gcc > /dev/null || die "missing program: x86_64-w64-mingw32-gcc (the MinGW cross compiler)"

fetch_probe() {
  local target="$CACHE/downloads/loader-case-probe-$OVERLAY_TAG.c"
  mkdir -p "$CACHE/downloads"
  if [ ! -f "$target" ]; then
    curl -fsSL --retry 3 -o "$target.part" "$PROBE_URL"
    mv "$target.part" "$target"
  fi
  [ "$(sha256sum "$target" | cut -d' ' -f1)" = "$PROBE_SHA256" ] || die "$target does not match the pinned sha256; delete it and run again"
  echo "$target"
}

run_wine_wayland() {
  local inner="LD_LIBRARY_PATH=\"\$1\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}\"; shift; export LD_LIBRARY_PATH; exec \"\$@\""
  timeout 120 env -u DISPLAY "${RUNNER[@]}" sh -c "$inner" sh \
    "$TOOL/files/lib/x86_64-linux-gnu:$TOOL/files/lib/i386-linux-gnu" "$TOOL/files/bin/wine" "$@" 2> /dev/null | tr -d '\r'
}

wayland_socket() {
  local socket="${WAYLAND_DISPLAY:-}"
  [ -n "$socket" ] || return 1
  [[ "$socket" == /* ]] || socket="${XDG_RUNTIME_DIR:-/nonexistent}/$socket"
  [ -S "$socket" ]
}

install_gpu_libraries() {
  local system32="$WINEPREFIX/drive_c/windows/system32" dll
  for dll in d3d10core d3d11 dxgi; do
    cp "$TOOL/files/lib/wine/dxvk/x86_64-windows/$dll.dll" "$system32/$dll.dll"
  done
  for dll in d3d12 d3d12core; do
    cp "$TOOL/files/lib/wine/vkd3d-proton/x86_64-windows/$dll.dll" "$system32/$dll.dll"
  done
}

run_wine_gpu() {
  local gpu="$1"
  shift
  local -x WINEDLLOVERRIDES="$WINEDLLOVERRIDES;d3d11,d3d10core,dxgi,d3d12,d3d12core=n"
  local -x DXVK_LOG_PATH=none
  if [ -n "$gpu" ]; then
    local -x DXVK_FILTER_DEVICE_NAME="$gpu" VKD3D_FILTER_DEVICE_NAME="$gpu"
  fi
  run_wine "$@"
}

core_dumps_visible() {
  command -v coredumpctl > /dev/null \
    && grep -q systemd-coredump /proc/sys/kernel/core_pattern 2> /dev/null \
    && [ "$(ulimit -H -c)" != 0 ]
}

count_core_dumps_since() {
  coredumpctl list --since "$1" --no-legend --no-pager 2> /dev/null | grep -c -F "$TOOL/" || true
}

cleanup() {
  stop_prefix
  rm -rf "$WORK"
}

WORK="$(mktemp -d "${TMPDIR:-/tmp}/flightsim-on-linux-check.XXXXXX")"
set_runner
trap cleanup EXIT

export WINEPREFIX="$WORK/pfx"
export WINEDEBUG=-all
export WINEDLLOVERRIDES="winemenubuilder.exe=d;mscoree,mshtml="
export WINEESYNC=0
export WINEFSYNC=0
export WINEDLLPATH="$TOOL/files/lib/vkd3d:$TOOL/files/lib/wine"
export_settings

"$HERE/build-checks.sh" "$WORK"
x86_64-w64-mingw32-gcc -O2 -o "$WORK/Fenix.exe" "$(fetch_probe)"

echo "Proton: $TOOL"
echo "Runtime: ${RUNTIME:-none, running on the host}"
echo "Throwaway prefix: $WINEPREFIX"
run_wine wineboot -i > /dev/null || true

failures=0
skipped=0
total=0
report() {
  local name="$1" status="$2" detail="$3" line
  total=$((total + 1))
  echo
  echo "== $name: $status"
  while IFS= read -r line; do
    echo "   $line"
  done <<< "$detail"
  case "$status" in
    ok) ;;
    skipped) skipped=$((skipped + 1)) ;;
    *) failures=$((failures + 1)) ;;
  esac
}

output="$(cd "$WORK" && run_wine Fenix.exe)" || true
if grep -q "KERNEL32.DLL" <<< "$output" && grep -q "KERNELBASE.dll" <<< "$output"; then
  report "fenix-a320-linux-patch loader (Fenix runner)" ok "$output"
else
  report "fenix-a320-linux-patch loader (Fenix runner)" FAIL "${output:-no output}
Without this the Fenix runner spins without opening a window."
fi

status=0
output="$(run_wine "$(windows_path "$WORK/notifyicon-guid-test.exe")")" || status=$?
case "$status" in
  0) report "shell32 tray icon by GUID (AutoFPS, SimAtlas)" ok "$output" ;;
  3) report "shell32 tray icon by GUID (AutoFPS, SimAtlas)" skipped "$output
Wine needs an X11 display for tray icons, and DISPLAY is '${DISPLAY:-unset}'. Run the check from your desktop session." ;;
  *) report "shell32 tray icon by GUID (AutoFPS, SimAtlas)" FAIL "$output" ;;
esac

status=0
output="$(run_wine "$(windows_path "$WORK/pathgradient-test.exe")")" || status=$?
[ "$status" -eq 0 ] && verdict=ok || verdict=FAIL
report "gdiplus path gradient (FSLabs display glow)" "$verdict" "$output"

status=0
output="$(run_wine "$(windows_path "$WORK/centered-point-test.exe")" "$(windows_path "$TOOL/files/share/fonts/arial.ttf")")" || status=$?
[ "$status" -eq 0 ] && verdict=ok || verdict=FAIL
report "gdiplus text centred on a point (FSLabs MCDU letters)" "$verdict" "$output"

status=0
output="$(run_wine "$(windows_path "$WORK/d2d1-geometry-leak-test.exe")")" || status=$?
[ "$status" -eq 0 ] && verdict=ok || verdict=FAIL
report "d2d1 path geometry gives its memory back (Fenix displays slowing down and freezing)" "$verdict" "${output:-no output}"

status=0
output="$(run_wine "$(windows_path "$WORK/d2d1-non-finite-close-test.exe")")" || status=$?
[ "$status" -eq 0 ] && verdict=ok || verdict=FAIL
report "d2d1 geometry with NaN coordinates closes (Fenix displays freezing)" "$verdict" "${output:-no output}"

status=0
output="$(run_wine "$(windows_path "$WORK/d2d1-bezier-ceiling-test.exe")")" || status=$?
[ "$status" -eq 0 ] && verdict=ok || verdict=FAIL
report "d2d1 coincident Bezier curves close fast (Fenix displays freezing)" "$verdict" "${output:-no output}"

FENCE_CHECK="D3D12 fence shared between processes (Fenix displays in GPU mode)"
EXIT_CHECK="exit after sharing a D3D12 fence, without a crash (Fenix displays leaving core dumps)"
FENCE_RUNS=3
CORE_DUMP_DEADLINE_SECONDS=5

report_fence_checks() {
  report "$FENCE_CHECK" "$1" "$2"
  report "$EXIT_CHECK" "$1" "$2"
}

wait_for_core_dumps() {
  local since="$1" deadline=$((SECONDS + CORE_DUMP_DEADLINE_SECONDS)) dumps
  while :; do
    dumps="$(count_core_dumps_since "$since")"
    if [ "$dumps" -gt 0 ] || [ "$SECONDS" -ge "$deadline" ]; then
      break
    fi
    sleep 0.2
  done
  echo "$dumps"
}

report_exit_check() {
  local failed_runs="$1" since="$2" output="$3" dumps verdict=FAIL clean
  if ! core_dumps_visible; then
    report "$EXIT_CHECK" skipped "A crash while a process exits leaves its exit status at 0 and shows up only as a core dump.
This check reads them from coredumpctl, which needs systemd-coredump and a core size limit above 0."
    return
  fi
  dumps="$(wait_for_core_dumps "$since")"
  clean="$(grep -c 'child exit code .* ok$' <<< "$output" || true)"
  [ "$failed_runs" -eq 0 ] && [ "$dumps" -eq 0 ] && [ "$clean" -eq "$FENCE_RUNS" ] && verdict=ok
  report "$EXIT_CHECK" "$verdict" "$FENCE_RUNS runs, $failed_runs with a non-zero exit status, $dumps core dump(s) of this Proton since the first one started
$output"
}

run_fence_test() {
  local gpu="$1" status failed_runs=0 since output outputs="" run fence_verdict=ok
  install_gpu_libraries
  since="$(date '+%F %T')"
  for run in $(seq "$FENCE_RUNS"); do
    status=0
    output="$(ulimit -c "$(ulimit -H -c)"; run_wine_gpu "$gpu" "$(windows_path "$WORK/d3d12-cross-process-fence-test.exe")")" || status=$?
    output="${output:-no output}"
    [ "$status" -eq 0 ] || failed_runs=$((failed_runs + 1))
    grep -q 'fence at .* ok$' <<< "$output" || fence_verdict=FAIL
    outputs+="run $run, exit status $status:
$output
"
  done
  outputs="${outputs%$'\n'}"
  report "$FENCE_CHECK" "$fence_verdict" "$outputs"
  report_exit_check "$failed_runs" "$since" "$outputs"
}

check_shared_fence() {
  local gpu status=0
  gpu="$(python3 -I "$HERE/../tools/discrete-gpu.py" 2>&1)" || status=$?
  case "$status" in
    0) run_fence_test "$gpu" ;;
    2 | 3) report_fence_checks skipped "$gpu" ;;
    *) report_fence_checks FAIL "$gpu" ;;
  esac
}
check_shared_fence

name="win32u child window drawn by another process (WebView2 in SayIntentions, GSX, REX)"
if wayland_socket; then
  run_wine reg add 'HKCU\Software\Wine\Drivers' /v Graphics /d wayland /f > /dev/null || true
  stop_prefix
  status=0
  output="$(run_wine_wayland "$(windows_path "$WORK/cross-process-child-test.exe")")" || status=$?
  [ "$status" -eq 0 ] && verdict=ok || verdict=FAIL
  report "$name" "$verdict" "${output:-no output}"
else
  report "$name" skipped "This check needs the Wayland driver, and WAYLAND_DISPLAY is '${WAYLAND_DISPLAY:-unset}'. Run the check from your desktop session."
fi

echo
if [ "$failures" -eq 0 ] && [ "$skipped" -eq 0 ]; then
  echo "All $total checks passed."
elif [ "$failures" -eq 0 ]; then
  echo "$((total - skipped)) checks passed, $skipped could not run here."
else
  echo "$failures of $total checks failed."
fi
[ "$failures" -eq 0 ]
