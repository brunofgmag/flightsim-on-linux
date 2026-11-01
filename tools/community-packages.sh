#!/usr/bin/env bash

community_packages_check() {
  local gone="$1" reinstall="$2" install="$3" name folder version status=0 found=0
  shift 3
  if [ -z "${FSOL_COMMUNITY:-}" ]; then
    echo "FAIL no Community folder found; run: flightsim-on-linux machine"
    return 1
  fi
  for name in "$@"; do
    folder="$FSOL_COMMUNITY/$name"
    if [ -L "$folder" ] && [ ! -e "$folder" ]; then
      echo "FAIL $name is a link to $(readlink "$folder"), which is gone; $gone"
      status=1
    elif [ -e "$folder" ]; then
      if version="$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["package_version"])' "$folder/manifest.json" 2> /dev/null)"; then
        echo "ok $name $version is in the Community folder"
        found=1
      else
        echo "FAIL $name has no readable manifest.json; $reinstall"
        status=1
      fi
    fi
  done
  if [ "$found" = 0 ] && [ "$status" = 0 ]; then
    echo "FAIL none of $* is in the Community folder; $install"
    status=1
  fi
  return "$status"
}
