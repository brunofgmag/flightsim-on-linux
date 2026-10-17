#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
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

windows_path() {
  local path="$1"
  echo "Z:${path//\//\\}"
}

find_steam() {
  local candidate
  for candidate in "${STEAM_DIR:-}" "$HOME/.local/share/Steam" "$HOME/.steam/steam" \
    "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam" "$HOME/snap/steam/common/.local/share/Steam"; do
    [ -n "$candidate" ] && [ -f "$candidate/steamapps/libraryfolders.vdf" ] || continue
    readlink -f "$candidate"
    return
  done
}

find_runtime() {
  local steam appid library manifest installdir
  steam="$(find_steam)"
  [ -n "$steam" ] && [ -f "$TOOL/toolmanifest.vdf" ] || return 0
  appid="$(sed -n 's/^[[:space:]]*"require_tool_appid"[[:space:]]*"\([0-9]*\)"$/\1/p' "$TOOL/toolmanifest.vdf")"
  [ -n "$appid" ] || return 0
  while read -r library; do
    manifest="$library/steamapps/appmanifest_$appid.acf"
    [ -f "$manifest" ] || continue
    installdir="$(sed -n 's/^[[:space:]]*"installdir"[[:space:]]*"\(.*\)"$/\1/p' "$manifest")"
    [ -x "$library/steamapps/common/$installdir/run" ] || continue
    echo "$library/steamapps/common/$installdir/run"
    return
  done < <(sed -n 's/^[[:space:]]*"path"[[:space:]]*"\(.*\)"$/\1/p' "$steam/steamapps/libraryfolders.vdf")
}

export_settings() {
  local line
  [ -f "$TOOL/user_settings.py" ] || return 0
  while IFS= read -r line; do
    export "${line?}"
  done < <(python3 -I -c 'import ast, sys
tree = ast.parse(open(sys.argv[1]).read())
for node in tree.body:
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "user_settings" for t in node.targets):
        for key, value in ast.literal_eval(node.value).items():
            print(f"{key}={value}")' "$TOOL/user_settings.py")
}

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

run_wine() {
  timeout 120 "${RUNNER[@]}" "$TOOL/files/bin/wine" "$@" 2> /dev/null | tr -d '\r'
}

stop_prefix() {
  timeout 60 "${RUNNER[@]}" "$TOOL/files/bin/wineserver" -k > /dev/null 2>&1 || true
}

cleanup() {
  stop_prefix
  rm -rf "$WORK"
}

WORK="$(mktemp -d "${TMPDIR:-/tmp}/flightsim-on-linux-check.XXXXXX")"
RUNTIME="$(find_runtime)"
if [ -n "$RUNTIME" ]; then
  RUNNER=("$RUNTIME" --)
else
  RUNNER=(env)
fi
trap cleanup EXIT

export WINEPREFIX="$WORK/pfx"
export WINEDEBUG=-all
export WINEDLLOVERRIDES="winemenubuilder.exe=d;mscoree,mshtml="
export WINEESYNC=0
export WINEFSYNC=0
export_settings

x86_64-w64-mingw32-gcc -O2 -o "$WORK/notifyicon-guid-test.exe" "$HERE/src/notifyicon-guid-test.c" -lshell32 -luser32
x86_64-w64-mingw32-gcc -O2 -o "$WORK/pathgradient-test.exe" "$HERE/src/pathgradient-test.c" -lgdiplus
x86_64-w64-mingw32-gcc -O2 -municode -o "$WORK/centered-point-test.exe" "$HERE/src/centered-point-test.c" -lgdiplus -lgdi32
x86_64-w64-mingw32-gcc -O2 -o "$WORK/Fenix.exe" "$(fetch_probe)"

echo "Proton: $TOOL"
echo "Runtime: ${RUNTIME:-none, running on the host}"
echo "Throwaway prefix: $WINEPREFIX"
run_wine wineboot -i > /dev/null || true

failures=0
skipped=0
report() {
  local name="$1" status="$2" detail="$3"
  echo
  echo "== $name: $status"
  echo "$detail" | sed 's/^/   /'
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

echo
if [ "$failures" -eq 0 ] && [ "$skipped" -eq 0 ]; then
  echo "All 4 checks passed."
elif [ "$failures" -eq 0 ]; then
  echo "$((4 - skipped)) checks passed, $skipped could not run here."
else
  echo "$failures of 4 checks failed."
fi
[ "$failures" -eq 0 ]
