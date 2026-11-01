#!/usr/bin/env bash
TRITRISIM_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# shellcheck source=tools/addon-launch.sh
. "$TRITRISIM_PARTS/../../../tools/addon-launch.sh"
# shellcheck source=tools/shim.sh
. "$TRITRISIM_PARTS/../../../tools/shim.sh"

TRITRISIM_PATTERN='^[A-Za-z]:.*\\TriTriSim Hub\\tritrisim-hub\.exe'

tritrisim_exe() {
  addon_newest "${FSOL_PREFIX:?FSOL_PREFIX is not set}/drive_c/users/*/AppData/Local/TriTriSim Hub/tritrisim-hub.exe"
}

tritrisim_imports() {
  python3 -I "$TRITRISIM_PARTS/patch_imports.py" "$@"
}

tritrisim_backup() {
  echo "${FSOL_COMPATDATA:?FSOL_COMPATDATA is not set}/flightsim-on-linux-backup/tritrisim-hub/tritrisim-hub.exe.orig"
}

tritrisim_refuse_while_open() {
  if pgrep -f -- "$TRITRISIM_PATTERN" > /dev/null; then
    echo "the TriTriSim Hub is open; close it first (its menu entry has a Close action), then run this again" >&2
    return 1
  fi
}

tritrisim_patch() {
  local exe="$1" shim state backup
  shim="$(shim_path oletr oletr.dll)" || { echo "the oletr.dll stand-in did not build, so $(basename "$exe") stays as it is" >&2; return 1; }
  state="$(tritrisim_imports status "$exe")"
  if [ "$state" != original ] && [ "$state" != patched ]; then
    echo "$(basename "$exe") has no single ole32.dll import name to change; this Hub version needs another look" >&2
    return 1
  fi
  install -m 644 "$shim" "$(dirname "$exe")/oletr.dll"
  if [ "$state" = patched ]; then
    echo "the import in $(basename "$exe") already names oletr.dll"
    return 0
  fi
  backup="$(tritrisim_backup)"
  mkdir -p "$(dirname "$backup")"
  cp -p "$exe" "$backup"
  tritrisim_imports patch "$exe"
  echo "the import in $(basename "$exe") now names oletr.dll; the original is kept in $backup"
}

tritrisim_apply() {
  local exe
  exe="$(tritrisim_exe)"
  [ -n "$exe" ] || { echo "the TriTriSim Hub is not installed in the prefix; run: flightsim-on-linux addon install tritrisim-hub <installer>" >&2; return 1; }
  tritrisim_refuse_while_open
  tritrisim_patch "$exe"
}

tritrisim_check() {
  local exe status=0
  exe="$(tritrisim_exe)"
  if [ -z "$exe" ]; then
    echo "FAIL the TriTriSim Hub is not installed in the prefix"
    return 1
  fi
  if [ "$(tritrisim_imports status "$exe")" = patched ]; then
    echo "ok $(basename "$exe") imports oletr.dll once and ole32.dll not at all"
  else
    echo "FAIL $(basename "$exe") still imports ole32.dll, or is in a state this fix does not know; the Hub restores its executable when it updates; run: flightsim-on-linux addon apply tritrisim-hub"
    status=1
  fi
  if cmp -s "$(shim_path oletr oletr.dll)" "$(dirname "$exe")/oletr.dll"; then
    echo "ok oletr.dll beside the executable is the stand-in built from this repository"
  else
    echo "FAIL oletr.dll is missing beside the executable, or is an older build of the stand-in"
    status=1
  fi
  return "$status"
}

tritrisim_undo() {
  local exe
  echo "the menu entry patches the program again on its next start; take it out with: flightsim-on-linux addon shortcut tritrisim-hub --remove"
  exe="$(tritrisim_exe)"
  if [ -z "$exe" ]; then
    echo "the TriTriSim Hub is not installed in the prefix; nothing to restore"
    return 0
  fi
  tritrisim_refuse_while_open
  if [ "$(tritrisim_imports status "$exe")" = patched ]; then
    tritrisim_imports restore "$exe"
    echo "$(basename "$exe") imports ole32.dll again"
  else
    echo "$(basename "$exe") already imports ole32.dll"
  fi
  rm -f "$(dirname "$exe")/oletr.dll"
}
