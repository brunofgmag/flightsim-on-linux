#!/usr/bin/env bash
FENIX_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
FSOL_ROOT="$(cd "$FENIX_PARTS/../../.." && pwd)"
FENIX_DEPS="$FSOL_PREFIX/drive_c/Program Files/FenixSim A320/deps"
FENIX_CONFIG="$FSOL_PREFIX/drive_c/ProgramData/Fenix/FenixSim A320/fenixConfig.xml"
FENIX_BACKUP="$FSOL_COMPATDATA/flightsim-on-linux-backup/fenix-a320"
FENIX_CACHE_PATH_LIMIT=150
export FENIX_PARTS FSOL_ROOT FENIX_DEPS FENIX_CONFIG FENIX_BACKUP

# shellcheck source=/dev/null
. "$FSOL_ROOT/tools/proton-runner.sh"

ok() {
  echo "ok $*"
}

fail() {
  echo "FAIL $*"
  return 1
}

die() {
  echo "fenix-a320: $*" >&2
  exit 2
}

fenix_download() {
  local url="$1" sha256="$2" name="$3" folder="$FSOL_CACHE/downloads" target
  [ "${#FSOL_CACHE}" -le "$FENIX_CACHE_PATH_LIMIT" ] || die "the cache folder $FSOL_CACHE is longer than $FENIX_CACHE_PATH_LIMIT characters, and Windows installers inside Wine fail on paths over 260; set FSOL_CACHE to a shorter folder"
  target="$folder/$name"
  mkdir -p "$folder"
  if [ ! -f "$target" ]; then
    curl -fL -sS --retry 3 -o "$target.part" "$url" || die "cannot download $url"
    mv "$target.part" "$target"
  fi
  [ "$(sha256sum "$target" | cut -d' ' -f1)" = "$sha256" ] || die "$target does not match the sha256 $sha256; delete it and run again"
  echo "$target"
}

fenix_wine() {
  local prefix="$1"
  shift
  addon_prefix_run "$prefix" wine "$@"
}

fenix_wineserver_kill() {
  addon_prefix_run "$1" wineserver -k > /dev/null 2>&1 || true
}

fenix_stash() {
  local target="$1" backup="$FENIX_BACKUP/$2"
  if [ -e "$backup" ] || [ -e "$backup.absent" ]; then
    return 0
  fi
  mkdir -p "$FENIX_BACKUP"
  if [ -f "$target" ]; then
    cp -p "$target" "$backup"
  else
    : > "$backup.absent"
  fi
}

fenix_restore() {
  local target="$1" name="$2" where="$3" backup="$FENIX_BACKUP/$2"
  if [ -e "$backup" ]; then
    mv "$backup" "$target"
    echo "changed $name restored from the backup"
  elif [ -e "$backup.absent" ]; then
    rm -f "$target" "$backup.absent"
    echo "changed $name removed from $where"
  else
    echo "already $name was not put in $where by this add-on"
  fi
}
