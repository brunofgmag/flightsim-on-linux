#!/usr/bin/env bash
HUB_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# shellcheck source=tools/shim.sh
. "$HUB_PARTS/../../../tools/shim.sh"
HUB_SHIM_MARK="navigraph-7z-"

hub_procgov() {
  compgen -G "${FSOL_PREFIX:?FSOL_PREFIX is not set}/drive_c/users/*/AppData/Local/Programs/navigraph-hub/resources/assets/bin/procgov.exe" | head -n 1 || true
}

hub_backup() {
  echo "${FSOL_COMPATDATA:?FSOL_COMPATDATA is not set}/flightsim-on-linux-backup/navigraph-hub/procgov.exe.orig"
}

hub_apply() {
  local target shim backup
  target="$(hub_procgov)"
  [ -n "$target" ] || { echo "the Navigraph Hub is not installed in the prefix; run: flightsim-on-linux addon install navigraph-hub <installer>" >&2; return 1; }
  shim="$(shim_path procgov procgov.exe)"
  if cmp -s "$shim" "$target"; then
    echo "already applied: procgov.exe is the shim"
    return 0
  fi
  backup="$(hub_backup)"
  mkdir -p "$(dirname "$backup")"
  if grep -qa "$HUB_SHIM_MARK" "$target"; then
    echo "procgov.exe is an older build of the shim: the copy of the original in $backup stays"
  else
    cp -p "$target" "$backup"
  fi
  install -m 755 "$shim" "$target"
  echo "procgov.exe replaced by the shim; the original is kept in $backup"
  echo "close the Hub and open it again before you install a cycle or a plugin"
}

hub_check() {
  local target shim backup status=0
  target="$(hub_procgov)"
  if [ -z "$target" ]; then
    echo "FAIL the Navigraph Hub is not installed in the prefix"
    return 1
  fi
  shim="$(shim_path procgov procgov.exe)" || { echo "FAIL the shim could not be built"; return 1; }
  if cmp -s "$shim" "$target"; then
    echo "ok procgov.exe is the shim built from this repository"
  else
    echo "FAIL procgov.exe is not the shim; the Hub restores its own when it updates; run: flightsim-on-linux addon apply navigraph-hub"
    status=1
  fi
  backup="$(hub_backup)"
  if [ -f "$backup" ]; then
    echo "ok the original procgov.exe is kept in $backup"
  else
    echo "FAIL no copy of the original procgov.exe in $backup"
    status=1
  fi
  return "$status"
}

hub_undo() {
  local target backup
  target="$(hub_procgov)"
  backup="$(hub_backup)"
  if [ -z "$target" ]; then
    echo "the Navigraph Hub is not installed in the prefix; nothing to restore"
    return 0
  fi
  if ! grep -qa "$HUB_SHIM_MARK" "$target"; then
    rm -f "$backup"
    echo "procgov.exe is not the shim: the Hub put its own back when it updated, so it stays as it is and the older copy kept in $backup is dropped"
    return 0
  fi
  if [ ! -f "$backup" ]; then
    echo "no copy of the original procgov.exe in $backup; if the Hub still has the shim, reinstall the Hub to get its own back" >&2
    return 1
  fi
  cp -p "$backup" "$target"
  rm "$backup"
  echo "procgov.exe is the Hub's own again"
}
