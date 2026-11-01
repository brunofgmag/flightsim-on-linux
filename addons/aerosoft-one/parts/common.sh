#!/usr/bin/env bash
AEROSOFT_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

aerosoft_run() {
  local store="${XDG_CONFIG_HOME:-${HOME:?HOME is not set}/.config}/Aerosoft One/persistent-store.json"
  python3 -I "$AEROSOFT_PARTS/aerosoft.py" "$1" "$store" "${FSOL_PACKAGES:-}"
}
