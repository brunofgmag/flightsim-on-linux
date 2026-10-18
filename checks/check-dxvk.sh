#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
# shellcheck source=/dev/null
. "$HERE/../tools/proton-runner.sh"

die() {
  echo "check-dxvk: $*" >&2
  exit 2
}

simulator_proton() {
  "$HERE/../bin/flightsim-on-linux" machine --json | python3 -I -c 'import json, sys; print(json.load(sys.stdin).get("proton") or "")'
}

[ $# -ge 1 ] && [ $# -le 2 ] || die "usage: $(basename "$0") <DXVK folder with d3d11.dll and dxgi.dll> [<Proton folder>]"
DXVK="$(readlink -f "$1")"
for dll in d3d11 dxgi; do
  [ -f "$DXVK/$dll.dll" ] || die "$DXVK has no $dll.dll"
done
TOOL="$(readlink -f "${2:-$(simulator_proton)}")"
[ -x "$TOOL/files/bin/wine" ] || die "no Proton folder given and none found for the simulator; pass one as the second argument"
command -v x86_64-w64-mingw32-gcc > /dev/null || die "missing program: x86_64-w64-mingw32-gcc (the MinGW cross compiler)"

VKD3D="$TOOL/files/lib/wine/vkd3d-proton/x86_64-windows"
PROTON_DXVK="$TOOL/files/lib/wine/dxvk/x86_64-windows"
for dll in "$PROTON_DXVK/d3d10core.dll" "$VKD3D/d3d12.dll" "$VKD3D/d3d12core.dll"; do
  [ -f "$dll" ] || die "$TOOL has no $dll"
done

cleanup() {
  stop_prefix
  rm -rf "$WORK"
}

choose_gpu() {
  local name status=0
  name="$(python3 -I "$HERE/../tools/discrete-gpu.py" 2> "$WORK/gpu.txt")" || status=$?
  case "$status" in
    0)
      if [ -n "$name" ]; then
        export DXVK_FILTER_DEVICE_NAME="$name" VKD3D_FILTER_DEVICE_NAME="$name"
        GPU="$name"
      else
        GPU="the only GPU there is"
      fi
      ;;
    2 | 3) SKIP_REASON="$(cat "$WORK/gpu.txt")" ;;
    *) die "tools/discrete-gpu.py failed with status $status: $(cat "$WORK/gpu.txt")" ;;
  esac
}

install_dlls() {
  local system32="$WINEPREFIX/drive_c/windows/system32"
  cp "$PROTON_DXVK/d3d10core.dll" "$system32/d3d10core.dll"
  cp "$DXVK/d3d11.dll" "$system32/d3d11.dll"
  cp "$DXVK/dxgi.dll" "$system32/dxgi.dll"
  cp "$VKD3D/d3d12.dll" "$system32/d3d12.dll"
  cp "$VKD3D/d3d12core.dll" "$system32/d3d12core.dll"
}

WORK="$(mktemp -d "${TMPDIR:-/tmp}/flightsim-on-linux-check.XXXXXX")"
set_runner
trap cleanup EXIT

export WINEPREFIX="$WORK/pfx"
export WINEDEBUG=-all
export WINEDLLOVERRIDES="winemenubuilder.exe=d;mscoree,mshtml=;d3d11,d3d10core,dxgi,d3d12,d3d12core=n"
export WINEESYNC=0
export WINEFSYNC=0
export DXVK_LOG_PATH=none
export DXVK_LOG_LEVEL=none
export WINEDLLPATH="$TOOL/files/lib/vkd3d:$TOOL/files/lib/wine"
export_settings

SKIP_REASON=
GPU=
choose_gpu

"$HERE/build-checks.sh" "$WORK"

echo "DXVK: $DXVK"
echo "Proton: $TOOL"
echo "Runtime: ${RUNTIME:-none, running on the host}"
echo "GPU: ${GPU:-not chosen}"
echo "Throwaway prefix: $WINEPREFIX"
if [ -z "$SKIP_REASON" ]; then
  run_wine wineboot -i > /dev/null || true
  install_dlls
fi

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

run_test() {
  local name="$1" program="$2" hint="$3" argument="${4:-}" status=0 output
  if [ -n "$SKIP_REASON" ]; then
    report "$name" skipped "$SKIP_REASON"
    return
  fi
  output="$(run_wine "$(windows_path "$WORK/$program")" ${argument:+"$argument"})" || status=$?
  if [ "$status" -eq 0 ]; then
    report "$name" ok "$output"
  else
    report "$name" FAIL "${output:-no output}
$hint"
  fi
}

run_test "dxgi GetSharedResourceAdapterLuid for a texture another process shared (Fenix displays in GPU mode)" \
  dxgi-shared-resource-luid-test.exe "Without this the Fenix displays fall back to copying through the CPU, or stop when the call is not implemented."
run_test "d3d11 keyed mutex hands over what the producer drew (Fenix displays in GPU mode)" \
  d3d11-keyed-mutex-test.exe "Without this the consumer reads a texture of zeros: the Fenix displays stay black."
run_test "d3d11 keyed mutex wait gives up when its timeout runs out (Fenix displays in GPU mode)" \
  d3d11-keyed-mutex-test.exe "Without this AcquireSync ignores a finite timeout and waits as long as the producer holds the mutex, so a producer that stalls hangs the display." timeout

echo
if [ "$failures" -eq 0 ] && [ "$skipped" -eq 0 ]; then
  echo "All $total checks passed."
elif [ "$failures" -eq 0 ]; then
  echo "$((total - skipped)) checks passed, $skipped could not run here."
else
  echo "$failures of $total checks failed."
fi
[ "$failures" -eq 0 ]
