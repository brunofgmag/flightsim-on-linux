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

status=0
output="$(run_wine "$(windows_path "$WORK/applicationdata-no-package-test.exe")")" || status=$?
[ "$status" -eq 0 ] && verdict=ok || verdict=FAIL
report "ApplicationData refuses a process without a package (Fenix Installer database)" "$verdict" "${output:-no output}"

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

choose_gpu
check_shared_fence
check_vkd3d_proton

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

report_one_pixel_checks() {
  report "$ONE_PIXEL_REGION_CHECK" "$1" "$2"
  report "$ONE_PIXEL_WARP_CHECK" "$1" "$2"
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
  "$WORK/eis-pointer" "$PRIVATE/eis-ready" > "$PRIVATE/eis.log" 2>&1 &
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

run_one_pixel_clip_program() {
  local inner status _
  inner="LD_LIBRARY_PATH=\"\$1\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}\"; shift; export LD_LIBRARY_PATH; exec \"\$@\""
  for _ in 1 2 3; do
    stop_prefix
    status=0
    WINEDEBUG="-all" WAYLAND_DEBUG=1 timeout 120 env -u DISPLAY WAYLAND_DISPLAY="$SOCKET" "${RUNNER[@]}" sh -c "$inner" sh \
      "$TOOL/files/lib/x86_64-linux-gnu:$TOOL/files/lib/i386-linux-gnu" "$TOOL/files/bin/wine" \
      "$(windows_path "$WORK/one-pixel-clip-test.exe")" > /dev/null 2> "$ONE_PIXEL_LOG" || status=$?
    [ "$status" -eq 2 ] || break
  done
  [ "$status" -eq 0 ] || echo "one-pixel-clip-test.exe exited with status $status"
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
  start_private_kwin && start_pointer_device && runner_sees_socket && run_one_pixel_clip_program || status=$?
  [ "$status" -eq 0 ] || tail -n 5 "$PRIVATE/kwin.log" "$PRIVATE/eis.log" 2> /dev/null
  return "$status"
}

run_isolated_one_pixel_session() {
  local state
  state="$(
    declare -p TOOL WORK RUNNER ONE_PIXEL_LOG
    declare -f windows_path stop_prefix wait_ten_seconds_for kwin_socket_exists kwin_on_bus seat_has_pointer \
      kwin_output_config start_private_kwin start_pointer_device runner_sees_socket run_one_pixel_clip_program isolated_cleanup isolated_session
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
    line ~ /^zwp_pointer_constraints_v1#[0-9]+\.confine_pointer\(/ || line ~ /^zwp_confined_pointer_v1#[0-9]+\.set_region\(/ {
      if (!match(line, /wl_region#[0-9]+/)) next
      id = substr(line, RSTART + 10, RLENGTH - 10)
      split(box[id], b, " ")
      count++
      x[count] = b[1]; y[count] = b[2]; w[count] = b[3]; h[count] = b[4]
      next
    }
    line ~ /^wp_pointer_warp_v1#[0-9]+\.warp_pointer\(/ && count > 0 {
      split(arguments(line), a, /, /)
      warps[count] = warps[count] sprintf(" %g,%g", a[3], a[4])
      if (a[3] >= x[count] && a[3] < x[count] + w[count] && a[4] >= y[count] && a[4] < y[count] + h[count]) landed[count] = 1
      next
    }
    END {
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
  report_one_pixel_checks skipped "This check runs in a private KWin session with a virtual pointer, and it is missing: ${missing%,}."
else
  run_wine reg add 'HKCU\Software\Wine\Drivers' /v Graphics /d wayland /f > /dev/null || true
  status=0
  output="$(run_isolated_one_pixel_session)" || status=$?
  case "$status" in
    0)
      report_one_pixel_judgement "$ONE_PIXEL_REGION_CHECK" region
      report_one_pixel_judgement "$ONE_PIXEL_WARP_CHECK" warp
      ;;
    2) report_one_pixel_checks skipped "${output:-no output}
The private KWin session could not run the test window to the end, so nothing could be compared." ;;
    *) report_one_pixel_checks FAIL "${output:-no output}" ;;
  esac
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
