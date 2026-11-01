#!/usr/bin/env bash
VOLANTA_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
VOLANTA_PATTERN='^[A-Za-z]:.*\\Volanta\\Volanta\.exe'
export VOLANTA_PATTERN

volanta_exe() {
  compgen -G "$FSOL_PREFIX/drive_c/users/*/AppData/Local/Programs/Volanta/Volanta.exe" | head -n 1 || true
}

volanta_cfg() {
  python3 -I "$VOLANTA_PARTS/../../../fsol/simconnect.py" "$1" "${FSOL_ADDON:-volanta}" "${FSOL_BASE:-}" "$(addon_sibling_prefix)"
}
