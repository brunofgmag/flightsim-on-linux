#!/usr/bin/env bash
DOTNET48_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
FSOL_ROOT="$(cd "$DOTNET48_PARTS/../../.." && pwd)"
DOTNET48_CACHE_PATH_LIMIT=150

# shellcheck source=tools/addon-launch.sh
. "$FSOL_ROOT/tools/addon-launch.sh"

dotnet48_backup_folder() {
  local base="${FSOL_COMPATDATA:?FSOL_COMPATDATA is not set}/flightsim-on-linux-backup" legacy
  legacy="$base/fenix-a320"
  if [ ! -e "$base/dotnet48" ] && { [ -e "$legacy/mscoree" ] || [ -e "$legacy/registry" ] || [ -e "$legacy/windows-version.json" ]; }; then
    echo "$legacy"
  else
    echo "$base/dotnet48"
  fi
}

DOTNET48_BACKUP="$(dotnet48_backup_folder)"
export DOTNET48_PARTS FSOL_ROOT DOTNET48_BACKUP

dotnet48_die() {
  echo "dotnet48: $*" >&2
  exit 2
}

dotnet48_download() {
  local url="$1" sha256="$2" name="$3" folder="$FSOL_CACHE/downloads" target
  [ "${#FSOL_CACHE}" -le "$DOTNET48_CACHE_PATH_LIMIT" ] || dotnet48_die "the cache folder $FSOL_CACHE is longer than $DOTNET48_CACHE_PATH_LIMIT characters, and Windows installers inside Wine fail on paths over 260; set FSOL_CACHE to a shorter folder"
  target="$folder/$name"
  mkdir -p "$folder"
  if [ ! -f "$target" ]; then
    curl -fL -sS --retry 3 -o "$target.part" "$url" || dotnet48_die "cannot download $url"
    mv "$target.part" "$target"
  fi
  [ "$(sha256sum "$target" | cut -d' ' -f1)" = "$sha256" ] || dotnet48_die "$target does not match the sha256 $sha256; delete it and run again"
  echo "$target"
}

dotnet48_wine() {
  local prefix="$1"
  shift
  addon_prefix_run "$prefix" wine "$@"
}

dotnet48_wineserver_kill() {
  addon_prefix_run "$1" wineserver -k > /dev/null 2>&1 || true
}
