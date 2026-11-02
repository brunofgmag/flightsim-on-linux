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
LOADER_PROBE="$(fetch_probe)"
x86_64-w64-mingw32-gcc -O2 -o "$WORK/Fenix.exe" "$LOADER_PROBE"
x86_64-w64-mingw32-gcc -O2 -o "$WORK/Fenix.GqlGateway.exe" "$LOADER_PROBE"

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

report_read_only_modules() {
  local name="Wine's Windows modules are read-only, as in the release (Proton FS2024 overwritten when Steam starts)" writable
  writable="$(find "$TOOL/files/lib/wine" -type f -path '*-windows/*' -perm /222 -printf '%P\n' | sort)"
  if [ -z "$writable" ]; then
    report "$name" ok "no module under files/lib/wine can be written"
  else
    report "$name" FAIL "$(head -n 5 <<< "$writable")
$(wc -l <<< "$writable") module(s) can be written. A prefix links to them, and another Proton that sets up the same prefix writes its own module through the link."
  fi
}

report_read_only_modules

report_loader_case() {
  local name="$1" program="$2" consequence="$3" output
  output="$(cd "$WORK" && run_wine "$program")" || true
  if grep -q "KERNEL32.DLL" <<< "$output" && grep -q "KERNELBASE.dll" <<< "$output"; then
    report "$name" ok "$output"
  else
    report "$name" FAIL "${output:-no output}
$consequence"
  fi
}

report_loader_case "fenix-a320-linux-patch loader (Fenix runner)" Fenix.exe \
  "Without this the Fenix runner spins without opening a window."
report_loader_case "ntdll loader spells KERNEL32.DLL and KERNELBASE.dll for Fenix.GqlGateway.exe (Fenix EFB GraphQL gateway)" Fenix.GqlGateway.exe \
  "Without this the Fenix GraphQL gateway spins at full CPU and never listens on port 8083."

status=0
output="$(run_wine "$(windows_path "$WORK/notifyicon-guid-test.exe")")" || status=$?
case "$status" in
  0) report "shell32 tray icon by GUID (AutoFPS, SimAtlas)" ok "$output" ;;
  3) report "shell32 tray icon by GUID (AutoFPS, SimAtlas)" skipped "$output
Wine needs an X11 display for tray icons, and DISPLAY is '${DISPLAY:-unset}'. Run the check from your desktop session." ;;
  *) report "shell32 tray icon by GUID (AutoFPS, SimAtlas)" FAIL "$output" ;;
esac

status=0
output="$(run_wine "$(windows_path "$WORK/tray-click-foreground-test.exe")")" || status=$?
case "$status" in
  0) report "explorer hands the foreground to a clicked tray icon's owner (Volanta tray menu)" ok "$output" ;;
  3) report "explorer hands the foreground to a clicked tray icon's owner (Volanta tray menu)" skipped "$output
Wine needs an X11 display for tray icons, and DISPLAY is '${DISPLAY:-unset}'. Run the check from your desktop session." ;;
  *) report "explorer hands the foreground to a clicked tray icon's owner (Volanta tray menu)" FAIL "$output
Without this, Electron programs such as Volanta open no menu when you right-click their tray icon, except the first time." ;;
esac

xembed_tray_items() {
  local name pid
  busctl --user call org.kde.StatusNotifierWatcher /StatusNotifierWatcher org.freedesktop.DBus.Properties Get ss \
    org.kde.StatusNotifierWatcher RegisteredStatusNotifierItems 2> /dev/null | grep -o '":[0-9.]*/StatusNotifierItem"' | tr -d '"' |
    while IFS=/ read -r name _; do
      pid="$(busctl --user call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus GetConnectionUnixProcessID s "$name" 2> /dev/null | cut -d' ' -f2)"
      [ -n "$pid" ] && [ "$(cat "/proc/$pid/comm" 2> /dev/null)" = xembedsniproxy ] && echo "$name"
    done
}

tray_first_click() {
  local before item tester status=0 _
  command -v busctl > /dev/null || { echo "cannot test: busctl is missing"; return 3; }
  busctl --user status org.kde.xembedsniproxy > /dev/null 2>&1 || { echo "cannot test: KDE's xembedsniproxy is not running"; return 3; }
  before="$(xembed_tray_items | sort)"
  run_wine "$(windows_path "$WORK/tray-first-click-test.exe")" 20000 > "$WORK/tray-first-click.txt" &
  tester=$!
  for _ in $(seq 1 100); do
    item="$(comm -13 <(echo "$before") <(xembed_tray_items | sort) | head -n 1)"
    [ -n "$item" ] && break
    sleep 0.1
  done
  if [ -z "$item" ]; then
    wait "$tester" || true
    cat "$WORK/tray-first-click.txt"
    echo "cannot test: xembedsniproxy listed no item for the test icon within 10 seconds"
    return 3
  fi
  sleep 1
  busctl --user call "$item" /StatusNotifierItem org.kde.StatusNotifierItem ContextMenu ii 200 200 > /dev/null || true
  wait "$tester" || status=$?
  cat "$WORK/tray-first-click.txt"
  return "$status"
}

status=0
output="$(tray_first_click)" || status=$?
case "$status" in
  0) report "winex11 delivers the first click on a tray icon KDE docked (Volanta tray menu)" ok "$output" ;;
  3) report "winex11 delivers the first click on a tray icon KDE docked (Volanta tray menu)" skipped "$output
This check needs a KDE session with an X11 display, where xembedsniproxy docks Wine's tray icons." ;;
  *) report "winex11 delivers the first click on a tray icon KDE docked (Volanta tray menu)" FAIL "$output
Without this, the first right-click on a docked Wine tray icon does nothing, and the menu opens only on the second." ;;
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
output="$(run_wine "$(windows_path "$WORK/gdiplus-nested-containers-test.exe")")" || status=$?
[ "$status" -eq 0 ] && verdict=ok || verdict=FAIL
report "gdiplus nested containers (FSLabs PFD attitude in turns, ND traffic arrows)" "$verdict" "${output:-no output}"

status=0
output="$(run_wine "$(windows_path "$WORK/gdiplus-sourcecopy-text-test.exe")")" || status=$?
[ "$status" -eq 0 ] && verdict=ok || verdict=FAIL
report "gdiplus SourceCopy text keeps the background around the glyphs (FSLabs black boxes behind labels)" "$verdict" "${output:-no output}"

status=0
output="$(run_wine "$(windows_path "$WORK/font-cache-test.exe")")" || status=$?
[ "$status" -eq 0 ] && verdict=ok || verdict=FAIL
report "win32u font caches hold sixty fonts (FSLabs display CPU)" "$verdict" "${output:-no output}"

status=0
output="$(run_wine "$(windows_path "$WORK/banded-region-test.exe")")" || status=$?
[ "$status" -eq 0 ] && verdict=ok || verdict=FAIL
report "win32u builds a region from banded rectangles in one pass (FSLabs display CPU)" "$verdict" "${output:-no output}"

status=0
output="$(run_wine "$(windows_path "$WORK/font-family-lookup-test.exe")" "$(windows_path "$TOOL/files/share/fonts/arial.ttf")")" || status=$?
[ "$status" -eq 0 ] && verdict=ok || verdict=FAIL
report "gdiplus finds a font family without enumerating the fonts (FSLabs display CPU)" "$verdict" "${output:-no output}"

status=0
output="$(run_wine "$(windows_path "$WORK/alpha-blend-row-test.exe")")" || status=$?
[ "$status" -eq 0 ] && verdict=ok || verdict=FAIL
report "win32u blends rows of pixels with SSE2 and the same result (FSLabs display CPU)" "$verdict" "${output:-no output}"

status=0
output="$(run_wine "$(windows_path "$WORK/premultiply-row-test.exe")")" || status=$?
[ "$status" -eq 0 ] && verdict=ok || verdict=FAIL
report "gdiplus premultiplies rows of pixels with SSE2 and the same result (FSLabs display CPU)" "$verdict" "${output:-no output}"

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

status=0
output="$(run_wine "$(windows_path "$WORK/applicationdata-no-package-test.exe")")" || status=$?
[ "$status" -eq 0 ] && verdict=ok || verdict=FAIL
report "ApplicationData refuses a process without a package (Fenix Installer database)" "$verdict" "${output:-no output}"

report_windows_program() {
  local name="$1" program="$2" status=0 output verdict=FAIL
  output="$(run_wine "$(windows_path "$WORK/$program")")" || status=$?
  [ "$status" -eq 0 ] && verdict=ok
  report "$name" "$verdict" "${output:-no output}"
}

report_windows_program 'wintrust CryptSIPCreateIndirectData hashes a PE file with a certificate table (Fenix 3.0 Runner checking its own signature)' \
  wintrust-create-indirect-data-test.exe
report_windows_program 'ntdll opens \\?\GLOBALROOT\Device\HarddiskVolumeN paths (Fenix 3.0 reopening its child executables)' \
  globalroot-device-path-test.exe
report_windows_program 'ntdll keeps the \\.\ prefix of a device-path current directory (FenixSystem 3.0 finding its data files)' \
  device-path-current-directory-test.exe
report_windows_program 'bcrypt accepts BCRYPT_PBKDF2_ALG_HANDLE (FenixSystem 3.0 authorization)' \
  bcrypt-pbkdf2-alg-handle-test.exe
report_windows_program 'ncrypt agrees on a secret between two ECDH P-256 keys (Fenix 3.0 Runner and FenixSystem key exchange)' \
  ncrypt-ecdh-p256-test.exe
report_windows_program 'ncrypt imports an ECDSA P-256 public key and verifies a signature (LuminaXP release check in X-Plane 12)' \
  ncrypt-ecdsa-p256-test.exe
report_windows_program 'expand.exe extracts one file of a cabinet held open by another program into a folder with -R -F: (LuminaXP DLSS 5 models in X-Plane 12)' \
  expand-cabinet-filter-test.exe
report_windows_program 'ntdll leaves readable memory after every heap block (Axis and Ohs crashing while it copies SimConnect messages)' \
  heap-read-slack-test.exe
report_windows_program 'ntdll stops walking a free list of blocks that are too small (simulator stopping once a second with the FSLabs A321)' \
  heap-free-list-scan-test.exe

name="reading another process after an EPERM leaves its main thread running (stutter with ChasePlane)"
if [ "$(cat /proc/sys/kernel/yama/ptrace_scope 2> /dev/null)" = 1 ]; then
  stop_prefix
  status=0
  output="$(run_wine "$(windows_path "$WORK/process-memory-after-eperm-test.exe")")" || status=$?
  case "$status" in
    0) report "$name" ok "$output" ;;
    2) report "$name" skipped "${output:-no output}
The test could not make Wine's server get an EPERM, so it could not tell the two paths apart." ;;
    *) report "$name" FAIL "${output:-no output}" ;;
  esac
else
  report "$name" skipped "This check makes the EPERM through Yama, and /proc/sys/kernel/yama/ptrace_scope is not 1 here."
fi

name="another process writes an image page Wine reports as writable but Linux maps r-x (REX Atmos Core atmosphere sliders)"
stop_prefix
status=0
output="$(run_wine "$(windows_path "$WORK/process-memory-write-readonly-image-page-test.exe")")" || status=$?
case "$status" in
  0) report "$name" ok "$output" ;;
  2) report "$name" skipped "${output:-no output}
The test could not leave its image page PAGE_EXECUTE_WRITECOPY with a read-only Linux mapping." ;;
  *) report "$name" FAIL "${output:-no output}" ;;
esac

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

choose_gpu() {
  GPU_STATUS=0
  GPU="$(python3 -I "$HERE/../tools/discrete-gpu.py" 2>&1)" || GPU_STATUS=$?
}

check_shared_fence() {
  case "$GPU_STATUS" in
    0) run_fence_test "$GPU" ;;
    2 | 3) report_fence_checks skipped "$GPU" ;;
    *) report_fence_checks FAIL "$GPU" ;;
  esac
}

ROOT_SIGNATURE_CHECK="vkd3d-proton hands back the live root signature for an identical blob (Glass Cockpit DLSS Fix skipping every display)"
DESCRIPTOR_HEAP_CHECK="vkd3d-proton descriptor addresses stay inside their heap (Glass Cockpit DLSS Fix skipping some display draws)"

report_vkd3d_checks() {
  report "$ROOT_SIGNATURE_CHECK" "$1" "$2"
  report "$DESCRIPTOR_HEAP_CHECK" "$1" "$2"
}

run_vkd3d_program() {
  local name="$1" program="$2" status=0 output verdict=FAIL
  output="$(run_wine_gpu "$GPU" "$(windows_path "$WORK/$program")")" || status=$?
  [ "$status" -eq 0 ] && verdict=ok
  report "$name" "$verdict" "${output:-no output}"
}

check_vkd3d_proton() {
  case "$GPU_STATUS" in
    0)
      install_gpu_libraries
      run_vkd3d_program "$ROOT_SIGNATURE_CHECK" d3d12-identical-root-signature-test.exe
      run_vkd3d_program "$DESCRIPTOR_HEAP_CHECK" d3d12-descriptor-heap-addresses-test.exe
      ;;
    2 | 3) report_vkd3d_checks skipped "$GPU" ;;
    *) report_vkd3d_checks FAIL "$GPU" ;;
  esac
}

BUDGET_CHECK="DXVK dxgi takes dxgi.videoMemoryBudgetReserve off the VRAM budget, never below half (simulator told it has less VRAM)"
BUDGET_RESERVE_MIB=2048
BUDGET_EXCESS_MIB=65536
BUDGET_TOLERANCE_MIB=32

query_budget() {
  local -x DXVK_CONFIG="$1"
  local output
  output="$(run_wine_gpu "$GPU" "$(windows_path "$WORK/dxgi-video-memory-budget-test.exe")")" || return 1
  sed -n 's/^budget \([0-9][0-9]*\) MiB$/\1/p' <<< "$output" | grep . || { echo "$output"; return 1; }
}

reserved_budget() {
  local base="$1" reserve="$2" floor=$(($1 / 2))
  if [ "$base" -gt $((floor + reserve)) ]; then
    echo $((base - reserve))
  else
    echo "$floor"
  fi
}

close_to() {
  local difference=$(($1 - $2))
  [ "${difference#-}" -le "$BUDGET_TOLERANCE_MIB" ]
}

run_budget_test() {
  local base reserved excess expected_reserved expected_excess
  stop_prefix
  base="$(query_budget "")" || { echo "$base"; return 1; }
  reserved="$(query_budget "dxgi.videoMemoryBudgetReserve=$BUDGET_RESERVE_MIB")" || { echo "$reserved"; return 1; }
  excess="$(query_budget "dxgi.videoMemoryBudgetReserve=$BUDGET_EXCESS_MIB")" || { echo "$excess"; return 1; }
  expected_reserved="$(reserved_budget "$base" "$BUDGET_RESERVE_MIB")"
  expected_excess="$(reserved_budget "$base" "$BUDGET_EXCESS_MIB")"
  echo "without the option: budget $base MiB"
  echo "reserve $BUDGET_RESERVE_MIB MiB: budget $reserved MiB, expected $expected_reserved ± $BUDGET_TOLERANCE_MIB"
  echo "reserve $BUDGET_EXCESS_MIB MiB: budget $excess MiB, expected half, $expected_excess ± $BUDGET_TOLERANCE_MIB"
  close_to "$reserved" "$expected_reserved" && close_to "$excess" "$expected_excess"
}

check_video_memory_budget() {
  local status=0 output
  case "$GPU_STATUS" in
    0)
      install_gpu_libraries
      output="$(run_budget_test)" || status=$?
      [ "$status" -eq 0 ] && verdict=ok || verdict=FAIL
      report "$BUDGET_CHECK" "$verdict" "${output:-no output}"
      ;;
    2 | 3) report "$BUDGET_CHECK" skipped "$GPU" ;;
    *) report "$BUDGET_CHECK" FAIL "$GPU" ;;
  esac
}

choose_gpu
check_shared_fence
check_vkd3d_proton
check_video_memory_budget

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

run_hdr_output_test() {
  local -x WINEDLLOVERRIDES="$WINEDLLOVERRIDES;d3d11,d3d10core,dxgi,d3d12,d3d12core=n"
  local -x DXVK_LOG_PATH=none DXVK_FILTER_DEVICE_NAME="$GPU" WINE_USE_EGL=1 DXVK_CONFIG="" DXVK_HDR="" DXVK_NO_HDR=""
  run_wine_wayland "$(windows_path "$WORK/dxgi-hdr-output-test.exe")"
}

name="DXVK dxgi reports HDR10 on every output Wine sees in HDR (simulator HDR option)"
if [ "$GPU_STATUS" -ne 0 ]; then
  report "$name" skipped "$GPU"
elif wayland_socket; then
  run_wine reg add 'HKCU\Software\Wine\Drivers' /v Graphics /d wayland /f > /dev/null || true
  install_gpu_libraries
  stop_prefix
  status=0
  output="$(run_hdr_output_test)" || status=$?
  case "$status" in
    0) report "$name" ok "$output" ;;
    2) report "$name" skipped "${output:-no output}
No output is in HDR, so nothing could be compared. Turn HDR on in the display settings and run the check again." ;;
    *) report "$name" FAIL "${output:-no output}
Without the HDR detection patch of the Proton-CachyOS release, DXGI reports SDR and the simulator greys out its HDR option." ;;
  esac
else
  report "$name" skipped "This check needs the Wayland driver, and WAYLAND_DISPLAY is '${WAYLAND_DISPLAY:-unset}'. Run the check from your desktop session."
fi

name="winewayland paces vsync swaps on a hidden OpenGL window (BBS Nest taking GPU from the simulator)"
if wayland_socket; then
  run_wine reg add 'HKCU\Software\Wine\Drivers' /v Graphics /d wayland /f > /dev/null || true
  stop_prefix
  status=0
  output="$(WINE_USE_EGL=1 run_wine_wayland "$(windows_path "$WORK/hidden-gl-swap-pacing-test.exe")")" || status=$?
  case "$status" in
    0) report "$name" ok "$output" ;;
    2) report "$name" skipped "${output:-no output}
The test could not draw with OpenGL in a hidden window, so nothing could be counted." ;;
    *) report "$name" FAIL "${output:-no output}" ;;
  esac
else
  report "$name" skipped "This check needs the Wayland driver, and WAYLAND_DISPLAY is '${WAYLAND_DISPLAY:-unset}'. Run the check from your desktop session."
fi

pointer_constraint_between() {
  awk -v start="$2" -v stop="$3" '
    index($0, start) && /debugstr/ { inside = 1; next }
    index($0, stop) && /debugstr/ { inside = 0 }
    inside && /Locking to hwnd/ { print "lock" }
    inside && /Confining to hwnd/ { print "confine" }' "$1" | sort -u | tr '\n' ' '
}

run_hidden_cursor_test() {
  local log="$WORK/hidden-cursor-clip.log" inner status whole small
  inner="LD_LIBRARY_PATH=\"\$1\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}\"; shift; export LD_LIBRARY_PATH; exec \"\$@\""
  for _ in 1 2 3; do
    stop_prefix
    status=0
    WINEDEBUG="+cursor,+debugstr" timeout 120 env -u DISPLAY "${RUNNER[@]}" sh -c "$inner" sh \
      "$TOOL/files/lib/x86_64-linux-gnu:$TOOL/files/lib/i386-linux-gnu" "$TOOL/files/bin/wine" \
      "$(windows_path "$WORK/hidden-cursor-clip-test.exe")" > /dev/null 2> "$log" || status=$?
    [ "$status" -eq 2 ] || break
  done
  [ "$status" -eq 0 ] || return "$status"
  whole="$(pointer_constraint_between "$log" case-whole-window case-small-rectangle)"
  small="$(pointer_constraint_between "$log" case-small-rectangle case-end)"
  echo "hidden cursor clipped to the whole window: ${whole:-nothing}"
  echo "hidden cursor clipped to a small rectangle: ${small:-nothing}"
  [ -n "$whole$small" ] || return 2
  [[ "$small" == *lock* && "$whole" == *confine* && "$whole" != *lock* ]]
}

name="winewayland hidden cursor clipped to its window keeps moving (simulator arrow freezing)"
if wayland_socket; then
  run_wine reg add 'HKCU\Software\Wine\Drivers' /v Graphics /d wayland /f > /dev/null || true
  status=0
  output="$(run_hidden_cursor_test)" || status=$?
  case "$status" in
    0) report "$name" ok "$output" ;;
    2) report "$name" skipped "${output:-no output}
The test window never became the foreground window, or the driver logged no pointer constraint, so nothing could be compared." ;;
    *) report "$name" FAIL "${output:-no output}" ;;
  esac
else
  report "$name" skipped "This check needs the Wayland driver, and WAYLAND_DISPLAY is '${WAYLAND_DISPLAY:-unset}'. Run the check from your desktop session."
fi

run_constraint_commit_test() {
  local log="$WORK/pointer-constraint-commit.log" inner status
  inner="LD_LIBRARY_PATH=\"\$1\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}\"; shift; export LD_LIBRARY_PATH; exec \"\$@\""
  for _ in 1 2 3; do
    stop_prefix
    status=0
    WINEDEBUG="-all" WAYLAND_DEBUG=1 timeout 120 env -u DISPLAY "${RUNNER[@]}" sh -c "$inner" sh \
      "$TOOL/files/lib/x86_64-linux-gnu:$TOOL/files/lib/i386-linux-gnu" "$TOOL/files/bin/wine" \
      "$(windows_path "$WORK/pointer-constraint-commit-test.exe")" > /dev/null 2> "$log" || status=$?
    [ "$status" -eq 2 ] || break
  done
  [ "$status" -eq 0 ] || return "$status"
  awk '
    !/\{pointer-constraint-commit-test\.exe\}  -> / { next }
    pending && /  -> (wp_pointer_warp_v1#[0-9]+\.warp_pointer|wl_compositor#[0-9]+\.create_region|wl_region#[0-9]+\.|zwp_pointer_constraints_v1#[0-9]+\.(confine|lock)_pointer)\(/ { next }
    pending {
      pending = 0
      if (/wl_surface#[0-9]+\.attach\(nil/) { released--; next }
      if (!/wl_surface#[0-9]+\.commit\(\)/) uncommitted++
    }
    /zwp_(locked|confined)_pointer_v1#[0-9]+\.destroy\(\)/ { released++; pending = 1 }
    END {
      if (pending) uncommitted++
      printf "pointer constraints released with the window open: %d, without a surface commit right after: %d\n", released, uncommitted
      if (released == 0) exit 2
      exit uncommitted ? 1 : 0
    }' "$log"
}

name="winewayland commits a released pointer constraint (simulator arrow staying frozen under KWin 6.8)"
if wayland_socket; then
  run_wine reg add 'HKCU\Software\Wine\Drivers' /v Graphics /d wayland /f > /dev/null || true
  status=0
  output="$(run_constraint_commit_test)" || status=$?
  case "$status" in
    0) report "$name" ok "$output" ;;
    2) report "$name" skipped "${output:-no output}
The test window never became the foreground window, or the driver released no pointer constraint, so nothing could be compared." ;;
    *) report "$name" FAIL "${output:-no output}" ;;
  esac
else
  report "$name" skipped "This check needs the Wayland driver, and WAYLAND_DISPLAY is '${WAYLAND_DISPLAY:-unset}'. Run the check from your desktop session."
fi

ONE_PIXEL_REGION_CHECK="winewayland confines a one-pixel clip at 125% scale to at least one pixel (ChasePlane drag)"
ONE_PIXEL_WARP_CHECK="winewayland warps the pointer inside a small clip (simulator arrow leaving the screen in a ChasePlane drag)"
ONE_PIXEL_LOG="$WORK/one-pixel-clip.log"
DRAG_CHECK="winewayland holds the pointer on a one-pixel clip while the mouse moves (simulator arrow shaking in a ChasePlane drag under KWin 6.7)"
DRAG_LOG="$WORK/pointer-drag-clip.log"
OVERLAY_INPUT_CHECK="win32u keeps mouse input away from the game while the Steam overlay is open under the Wayland driver (the simulator reacting to clicks meant for Shift+Tab)"
OVERLAY_INPUT_LOG="$WORK/steam-overlay-input.log"

report_one_pixel_checks() {
  report "$ONE_PIXEL_REGION_CHECK" "$1" "$2"
  report "$ONE_PIXEL_WARP_CHECK" "$1" "$2"
}

report_private_kwin_checks() {
  report_one_pixel_checks "$1" "$2"
  report "$DRAG_CHECK" "$1" "$2"
  report "$OVERLAY_INPUT_CHECK" "$1" "$2"
}

overlay_input_result() {
  cat "$WORK/steam-overlay-input.txt" 2> /dev/null || echo "the test window wrote no result"
}

run_overlay_input_test() {
  export WINE_WAYLAND_STEAM_OVERLAY_LAYER=1
  run_isolated_session steam-overlay-input-test.exe "$OVERLAY_INPUT_LOG" sweep
}

missing_for_private_kwin() {
  local tool
  for tool in kwin_wayland dbus-run-session busctl; do
    command -v "$tool" > /dev/null || echo "$tool"
  done
  [ -x "$WORK/eis-pointer" ] || echo "eis-pointer (built only where libei-1.0 and libsystemd are installed)"
  [ -n "${XDG_RUNTIME_DIR:-}" ] || echo "XDG_RUNTIME_DIR (the isolated KWin puts its socket there)"
}

wait_ten_seconds_for() {
  local description="$1" _
  shift
  for _ in $(seq 1 100); do
    "$@" > /dev/null 2>&1 && return 0
    sleep 0.1
  done
  echo "$description did not happen within 10 seconds"
  return 2
}

kwin_socket_exists() {
  [ -e "$XDG_RUNTIME_DIR/$SOCKET" ]
}

kwin_on_bus() {
  busctl --user status org.kde.KWin
}

seat_has_pointer() {
  local info
  info="$(WAYLAND_DISPLAY="$SOCKET" wayland-info 2> /dev/null)" || return 1
  grep -q 'capabilities:.*pointer' <<< "$info"
}

kwin_output_config() {
  printf '%s\n' '[{"data":[{"connectorName":"Virtual-0","mode":{"basic":{"height":1500,"refreshRate":60000,"width":2000},"flags":1},"scale":1.25,"uuid":"3a1c5b44-0d6c-4a6e-9f0e-5f6f2f2c7e10"}],"name":"outputs"},{"data":[{"lidClosed":false,"outputs":[{"enabled":true,"outputIndex":0,"position":{"x":0,"y":0},"priority":0,"replicationSource":""}]}],"name":"setups"}]'
}

start_private_kwin() {
  mkdir "$PRIVATE/config"
  kwin_output_config > "$PRIVATE/config/kwinoutputconfig.json"
  env -u WAYLAND_DISPLAY -u QT_QPA_PLATFORM -u KDE_FULL_SESSION -u KDE_SESSION_VERSION -u DISPLAY \
    XDG_CONFIG_HOME="$PRIVATE/config" KWIN_WAYLAND_NO_PERMISSION_CHECKS=1 \
    kwin_wayland --virtual --no-lockscreen --width 1600 --height 1200 --scale 1.25 --socket "$SOCKET" > "$PRIVATE/kwin.log" 2>&1 &
  KWIN_PID=$!
  wait_ten_seconds_for "the isolated KWin's socket $SOCKET" kwin_socket_exists \
    && wait_ten_seconds_for "the isolated KWin on its private bus" kwin_on_bus
}

start_pointer_device() {
  "$WORK/eis-pointer" "$PRIVATE/eis-ready" ${POINTER_MOTION:+"$POINTER_MOTION"} > "$PRIVATE/eis.log" 2>&1 &
  EIS_PID=$!
  wait_ten_seconds_for "the virtual pointer to be ready" test -e "$PRIVATE/eis-ready" || return $?
  command -v wayland-info > /dev/null || return 0
  wait_ten_seconds_for "the isolated KWin's seat to advertise a pointer" seat_has_pointer
}

runner_sees_socket() {
  timeout 60 env WAYLAND_DISPLAY="$SOCKET" "${RUNNER[@]}" sh -c "test -S \"\$XDG_RUNTIME_DIR/\$WAYLAND_DISPLAY\"" > /dev/null 2>&1 && return 0
  echo "the runner (${RUNNER[*]}) does not see $XDG_RUNTIME_DIR/$SOCKET"
  return 2
}

run_private_program() {
  local inner status _
  inner="LD_LIBRARY_PATH=\"\$1\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}\"; shift; export LD_LIBRARY_PATH; exec \"\$@\""
  for _ in 1 2 3; do
    stop_prefix
    status=0
    WINEDEBUG="-all" WAYLAND_DEBUG=1 timeout 120 env -u DISPLAY WAYLAND_DISPLAY="$SOCKET" "${RUNNER[@]}" sh -c "$inner" sh \
      "$TOOL/files/lib/x86_64-linux-gnu:$TOOL/files/lib/i386-linux-gnu" "$TOOL/files/bin/wine" \
      "$(windows_path "$WORK/$PROGRAM")" > /dev/null 2> "$PROGRAM_LOG" || status=$?
    [ "$status" -eq 2 ] || break
  done
  [ "$status" -eq 0 ] || echo "$PROGRAM exited with status $status"
  return "$status"
}

isolated_cleanup() {
  stop_prefix
  [ -z "$EIS_PID" ] || kill "$EIS_PID" 2> /dev/null || true
  [ -z "$KWIN_PID" ] || kill "$KWIN_PID" 2> /dev/null || true
  wait 2> /dev/null || true
  rm -rf "$PRIVATE"
}

isolated_session() {
  local status=0
  SOCKET="flightsim-on-linux-check-$$"
  PRIVATE="$WORK/private-kwin"
  KWIN_PID=""
  EIS_PID=""
  trap isolated_cleanup EXIT
  mkdir "$PRIVATE"
  start_private_kwin && start_pointer_device && runner_sees_socket && run_private_program || status=$?
  [ "$status" -eq 0 ] || tail -n 5 "$PRIVATE/kwin.log" "$PRIVATE/eis.log" 2> /dev/null
  return "$status"
}

run_isolated_session() {
  local PROGRAM="$1" PROGRAM_LOG="$2" POINTER_MOTION="${3:-}" state
  state="$(
    declare -p TOOL WORK RUNNER PROGRAM PROGRAM_LOG POINTER_MOTION
    declare -f windows_path stop_prefix wait_ten_seconds_for kwin_socket_exists kwin_on_bus seat_has_pointer \
      kwin_output_config start_private_kwin start_pointer_device runner_sees_socket run_private_program isolated_cleanup isolated_session
  )"
  dbus-run-session -- bash -c "$state
isolated_session" 2> /dev/null
}

judge_one_pixel_clip() {
  awk -v mode="$1" '
    function arguments(text) {
      sub(/^[^(]*\(/, "", text)
      sub(/\)$/, "", text)
      return text
    }
    /\{one-pixel-clip-test\.exe\} .*preferred_scale\(150\)/ { scaled = 1 }
    !/\{one-pixel-clip-test\.exe\}  -> / { next }
    {
      line = $0
      sub(/^.*\{one-pixel-clip-test\.exe\}  -> /, "", line)
    }
    line ~ /^wl_region#[0-9]+\.add\(/ {
      id = line
      sub(/^wl_region#/, "", id)
      sub(/\..*$/, "", id)
      split(arguments(line), a, /, /)
      box[id] = a[1] " " a[2] " " a[3] " " a[4]
      next
    }
    function attach_pending_warps(    i) {
      for (i = 1; i <= pending; i++) {
        warps[count] = warps[count] sprintf(" %g,%g", px[i], py[i])
        if (px[i] >= x[count] && px[i] < x[count] + w[count] && py[i] >= y[count] && py[i] < y[count] + h[count]) landed[count] = 1
      }
      pending = 0
    }
    line ~ /^zwp_pointer_constraints_v1#[0-9]+\.confine_pointer\(/ || line ~ /^zwp_confined_pointer_v1#[0-9]+\.set_region\(/ {
      if (!match(line, /wl_region#[0-9]+/)) next
      id = substr(line, RSTART + 10, RLENGTH - 10)
      split(box[id], b, " ")
      count++
      x[count] = b[1]; y[count] = b[2]; w[count] = b[3]; h[count] = b[4]
      attach_pending_warps()
      next
    }
    line ~ /^wp_pointer_warp_v1#[0-9]+\.warp_pointer\(/ {
      split(arguments(line), a, /, /)
      pending++
      px[pending] = a[3]; py[pending] = a[4]
      next
    }
    (line ~ /^wl_surface#[0-9]+\.commit\(/ || line ~ /^zwp_(confined|locked)_pointer_v1#[0-9]+\.destroy\(/) && count > 0 {
      attach_pending_warps()
      next
    }
    END {
      if (count > 0) attach_pending_warps()
      if (!scaled) { print "the surface never received preferred_scale(150): the isolated KWin did not stay at 125% scale"; exit 2 }
      if (count < 6) { printf "pointer confinements: %d, fewer than the 6 the test makes\n", count; exit 2 }
      for (n = 1; n <= count; n++) {
        name = n <= 5 ? "i=" (n - 1) : (n == 6 ? "wide" : "extra")
        if (mode == "region") ok = (w[n] >= 1 && h[n] >= 1)
        else ok = landed[n]
        printf "%-6s region %d,%d+%dx%d  warp%s  %s\n", name, x[n], y[n], w[n], h[n], (warps[n] == "" ? " none" : warps[n]), (ok ? "ok" : "FAILED")
        if (!ok) failures++
      }
      exit failures ? 1 : 0
    }' "$ONE_PIXEL_LOG"
}

judge_pointer_drag() {
  awk '
    function arguments(text) {
      sub(/^[^(]*\(/, "", text)
      sub(/\)$/, "", text)
      return text
    }
    function distance(a, b) {
      return a > b ? a - b : b - a
    }
    /\{pointer-drag-clip-test\.exe\} .*preferred_scale\(150\)/ { scaled = 1 }
    /\{pointer-drag-clip-test\.exe\}  -> zwp_pointer_constraints_v1#[0-9]+\.confine_pointer\(/ { dragging = 1 }
    /\{pointer-drag-clip-test\.exe\}  -> wp_pointer_warp_v1#[0-9]+\.warp_pointer\(/ && dragging {
      split(arguments($0), a, /, /)
      target_x = a[3]; target_y = a[4]
      cycles++
      next
    }
    /\{pointer-drag-clip-test\.exe\} zwp_relative_pointer_v1#[0-9]+\.relative_motion\(/ && cycles {
      split(arguments($0), a, /, /)
      if (a[3] != 0 || a[4] != 0) moved++
      next
    }
    /\{pointer-drag-clip-test\.exe\} wl_pointer#[0-9]+\.motion\(/ && cycles {
      split(arguments($0), a, /, /)
      if (distance(a[2], target_x) > 3 || distance(a[3], target_y) > 3) strayed[cycles] = 1
    }
    END {
      if (!scaled) { print "the surface never received preferred_scale(150): the isolated KWin did not stay at 125% scale"; exit 2 }
      if (cycles < 50) { printf "drag cycles: %d, fewer than the 50 a 3-second drag makes\n", cycles; exit 2 }
      if (moved < 1000) { printf "relative pointer motions during the drag: %d, so the virtual mouse hardly moved\n", moved; exit 2 }
      for (n in strayed) count++
      printf "drag cycles in which the pointer went more than 3 pixels from the clip: %d of %d\n", count, cycles
      exit count * 4 > cycles ? 1 : 0
    }' "$DRAG_LOG"
}

report_one_pixel_judgement() {
  local name="$1" mode="$2" status=0 output
  output="$(judge_one_pixel_clip "$mode")" || status=$?
  case "$status" in
    0) report "$name" ok "$output" ;;
    2) report "$name" skipped "${output:-no output}
The isolated session did not reach the conditions this check needs, so nothing could be compared." ;;
    *) report "$name" FAIL "$output" ;;
  esac
}

missing="$(missing_for_private_kwin | tr '\n' ',')"
if [ -n "$missing" ]; then
  report_private_kwin_checks skipped "This check runs in a private KWin session with a virtual pointer, and it is missing: ${missing%,}."
else
  run_wine reg add 'HKCU\Software\Wine\Drivers' /v Graphics /d wayland /f > /dev/null || true
  status=0
  output="$(run_isolated_session one-pixel-clip-test.exe "$ONE_PIXEL_LOG")" || status=$?
  case "$status" in
    0)
      report_one_pixel_judgement "$ONE_PIXEL_REGION_CHECK" region
      report_one_pixel_judgement "$ONE_PIXEL_WARP_CHECK" warp
      ;;
    2) report_one_pixel_checks skipped "${output:-no output}
The private KWin session could not run the test window to the end, so nothing could be compared." ;;
    *) report_one_pixel_checks FAIL "${output:-no output}" ;;
  esac
  status=0
  output="$(run_isolated_session pointer-drag-clip-test.exe "$DRAG_LOG" sweep)" || status=$?
  [ "$status" -ne 0 ] || output="$(judge_pointer_drag)" || status=$?
  case "$status" in
    0) report "$DRAG_CHECK" ok "$output" ;;
    2) report "$DRAG_CHECK" skipped "${output:-no output}
The private KWin session could not run the drag to the end, so nothing could be compared." ;;
    *) report "$DRAG_CHECK" FAIL "${output:-no output}" ;;
  esac
  status=0
  output="$(run_overlay_input_test)" || status=$?
  case "$status" in
    0) report "$OVERLAY_INPUT_CHECK" ok "$(overlay_input_result)" ;;
    2) report "$OVERLAY_INPUT_CHECK" skipped "$(overlay_input_result)
${output:-}
No mouse motion reached the test window before or after the overlay, so nothing could be compared." ;;
    *) report "$OVERLAY_INPUT_CHECK" FAIL "$(overlay_input_result)" ;;
  esac
fi

OVERLAY_LAYER_CHECK="The Steam overlay layer for the Wayland driver loads in the runtime only when Proton turns it on (Shift+Tab and the FPS counter under PROTON_ENABLE_WAYLAND)"
OVERLAY_LAYER_MANIFESTS="$TOOL/files/share/steam-overlay-wayland/implicit_layer.d"
OVERLAY_EXPORTS_CHECK="win32u.so exports the cursor helpers the Steam overlay bridge calls (game cursor drawn over the overlay, or left hidden after it closes)"

overlay_layer_loader_log() {
  local inner="VK_IMPLICIT_LAYER_PATH=\"\${VK_IMPLICIT_LAYER_PATH:+\$VK_IMPLICIT_LAYER_PATH:}\$1\" WINE_WAYLAND_STEAM_OVERLAY_LAYER=\"\$2\" VK_LOADER_DEBUG=layer vulkaninfo --summary 2>&1"
  timeout 120 "${RUNNER[@]}" sh -c "$inner" sh "$OVERLAY_LAYER_MANIFESTS" "$1" 2> /dev/null || true
}

overlay_layer_insertions() {
  grep -c 'Insert instance layer "VK_LAYER_GE_wayland_steam_overlay"' <<< "$1" || true
}

check_overlay_layer() {
  local on off
  if [ ! -f "$OVERLAY_LAYER_MANIFESTS/VkLayer_GE_wayland_steam_overlay.json" ]; then
    report "$OVERLAY_LAYER_CHECK" FAIL "$OVERLAY_LAYER_MANIFESTS has no VkLayer_GE_wayland_steam_overlay.json"
    return
  fi
  if ! timeout 60 "${RUNNER[@]}" sh -c 'command -v vulkaninfo' > /dev/null 2>&1; then
    report "$OVERLAY_LAYER_CHECK" skipped "The runner (${RUNNER[*]}) has no vulkaninfo, so the Vulkan loader could not be asked."
    return
  fi
  on="$(overlay_layer_loader_log 1)"
  off="$(overlay_layer_loader_log 0)"
  if ! grep -q 'deviceName' <<< "$on"; then
    report "$OVERLAY_LAYER_CHECK" skipped "vulkaninfo found no Vulkan device in the runner, so no instance was created."
  elif [ "$(overlay_layer_insertions "$on")" -ge 1 ] && [ "$(overlay_layer_insertions "$off")" -eq 0 ]; then
    report "$OVERLAY_LAYER_CHECK" ok "inserted with WINE_WAYLAND_STEAM_OVERLAY_LAYER=1, left out with 0"
  else
    report "$OVERLAY_LAYER_CHECK" FAIL "instance insertions with the switch on: $(overlay_layer_insertions "$on"), off: $(overlay_layer_insertions "$off")
$(grep -iE 'GE_Wayland|GE_wayland' <<< "$on" | grep -iE 'error|fail|cannot|unable' | head -n 3)"
  fi
}

check_overlay_exports() {
  local library="$TOOL/files/lib/wine/x86_64-unix/win32u.so" symbols missing="" name
  symbols="$(readelf --dyn-syms --wide "$library" 2> /dev/null | awk '$7 != "UND" { print $8 }')"
  for name in __wine_suppress_driver_cursor __wine_reapply_driver_cursor; do
    grep -qx "$name" <<< "$symbols" || missing="$missing $name"
  done
  if [ -z "$missing" ]; then
    report "$OVERLAY_EXPORTS_CHECK" ok "__wine_suppress_driver_cursor and __wine_reapply_driver_cursor are exported"
  else
    report "$OVERLAY_EXPORTS_CHECK" FAIL "missing from win32u.so:$missing"
  fi
}

check_overlay_layer
check_overlay_exports

echo
if [ "$failures" -eq 0 ] && [ "$skipped" -eq 0 ]; then
  echo "All $total checks passed."
elif [ "$failures" -eq 0 ]; then
  echo "$((total - skipped)) checks passed, $skipped could not run here."
else
  echo "$failures of $total checks failed."
fi
[ "$failures" -eq 0 ]
