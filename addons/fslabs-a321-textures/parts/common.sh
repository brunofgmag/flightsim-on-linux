#!/usr/bin/env bash
FSL_TEXTURES_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

fsl_textures() {
  local action="$1" package="" backup="" caps=()
  [ -z "${FSOL_COMMUNITY:-}" ] || package="$FSOL_COMMUNITY/fsl-a32x"
  [ -z "${FSOL_COMPATDATA:-}" ] || backup="$FSOL_COMPATDATA/flightsim-on-linux-backup/fslabs-a321-textures"
  [ -z "${FSL_TEXTURE_EXTERIOR:-}" ] || caps+=(--exterior "$FSL_TEXTURE_EXTERIOR")
  [ -z "${FSL_TEXTURE_COCKPIT:-}" ] || caps+=(--cockpit "$FSL_TEXTURE_COCKPIT")
  python3 -I "$FSL_TEXTURES_PARTS/textures.py" "$action" "$package" "$backup" "${caps[@]}"
}
