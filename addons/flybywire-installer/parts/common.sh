#!/usr/bin/env bash
FLYBYWIRE_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

flybywire_run() {
  local config="${XDG_CONFIG_HOME:-${HOME:?HOME is not set}/.config}/FlyByWire Installer/config.json"
  python3 -I "$FLYBYWIRE_PARTS/flybywire.py" "$1" "$config" "${FSOL_COMMUNITY:-}"
}
