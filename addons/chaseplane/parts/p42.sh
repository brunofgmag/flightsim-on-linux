#!/usr/bin/env bash
P42_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
FSOL_ROOT="$(cd "$P42_PARTS/../../.." && pwd)"
P42_FOLDER="${FSOL_PREFIX:?FSOL_PREFIX is not set}/drive_c/appresolver"
P42_BACKUP="${FSOL_COMPATDATA:?FSOL_COMPATDATA is not set}/flightsim-on-linux-backup/parallel42"
P42_USERS="$P42_BACKUP/users"
P42_STAND_INS="appresolver:AppResolverShim.dll toaststub:toaststub.dll"
export P42_PARTS FSOL_ROOT P42_FOLDER P42_BACKUP P42_USERS P42_STAND_INS

# shellcheck source=tools/addon-launch.sh
. "$FSOL_ROOT/tools/addon-launch.sh"
# shellcheck source=tools/shim.sh
. "$FSOL_ROOT/tools/shim.sh"

p42_die() {
  echo "${FSOL_ADDON:-parallel42}: $*" >&2
  exit 2
}

p42_import() {
  addon_alone regedit /S "$(addon_windows_path "$1")" > /dev/null || p42_die "regedit could not import $(basename "$1")"
}

p42_users() {
  [ -d "$P42_USERS" ] || return 0
  find "$P42_USERS" -maxdepth 1 -type f -printf '%f\n' | sort | paste -sd' '
}

p42_apply() {
  local id="$1" pair built
  for pair in $P42_STAND_INS; do
    built="$(shim_path "${pair%%:*}" "${pair##*:}")" || exit 2
    mkdir -p "$P42_FOLDER" "$P42_USERS"
    addon_stash "$P42_FOLDER/${pair##*:}" "$P42_BACKUP/files"
    cp "$built" "$P42_FOLDER/${pair##*:}"
  done
  p42_import "$P42_PARTS/p42-install.reg"
  : > "$P42_USERS/$id"
  printf '%s\n' "changed C:\\appresolver and the registry: the CAppResolver class and the ToastNotificationManager class that Parallel 42 programs ask Windows for, which Wine does not have"
}

p42_undo() {
  local id="$1" pair others
  if [ ! -e "$P42_USERS/$id" ]; then
    echo "already no stand-ins of ours for $id"
    return 0
  fi
  rm -f "$P42_USERS/$id"
  others="$(p42_users)"
  if [ -n "$others" ]; then
    printf '%s\n' "left the stand-ins in C:\\appresolver and the registry: $others still uses them"
    return 0
  fi
  p42_import "$P42_PARTS/p42-remove.reg"
  for pair in $P42_STAND_INS; do
    addon_unstash "$P42_FOLDER/${pair##*:}" "$P42_BACKUP/files"
  done
  rmdir "$P42_FOLDER" 2> /dev/null || true
  printf '%s\n' "changed C:\\appresolver and the registry: the stand-in classes are gone"
}

p42_clean_backup() {
  [ ! -d "$P42_BACKUP" ] || find "$P42_BACKUP" -depth -type d -empty -delete
}

p42_check() {
  python3 -I "$P42_PARTS/p42.py" check "$FSOL_PREFIX" "${FSOL_ADDON:-chaseplane}"
}
