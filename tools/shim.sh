#!/usr/bin/env bash

SHIMS_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)/shims"

shim_stale() {
  local built="$1" name="$2"
  [ -f "$built" ] || return 0
  [ -n "$(find "$SHIMS_ROOT/$name" -type f -newer "$built" -print -quit)" ]
}

shim_path() {
  local name="$1" file="${2:-$1.dll}" out="${FSOL_CACHE:?FSOL_CACHE is not set}/shims"
  [ -x "$SHIMS_ROOT/$name/build.sh" ] || { echo "shim $name does not exist" >&2; return 1; }
  if shim_stale "$out/$file" "$name"; then
    "$SHIMS_ROOT/$name/build.sh" "$out" >&2 || return 1
  fi
  [ -f "$out/$file" ] || { echo "shim $name did not build $file" >&2; return 1; }
  echo "$out/$file"
}
