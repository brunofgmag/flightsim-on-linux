#!/usr/bin/env bash

community_check() {
  local link status=0
  if [ ! -d "${FSOL_COMMUNITY:-}" ]; then
    fail "the Community folder was not found, so the Fenix packages could not be looked at; run: flightsim-on-linux machine" || return 1
  fi
  while IFS= read -r link; do
    fail "$link in Community is a Linux link: an update through the Fenix Installer replaces every file of the package, and through Windows calls Wine deleted the folder behind a link and left the link dangling; delete the link with rm, not the folder behind it, and reinstall the aircraft in the Fenix Installer, which makes a real folder" || status=1
  done < <(find "$FSOL_COMMUNITY" -maxdepth 1 -name 'fnx-*' -type l -printf '%f\n' | sort)
  [ "$status" -eq 1 ] || ok "no package named fnx-* in Community is a Linux link"
  return "$status"
}
