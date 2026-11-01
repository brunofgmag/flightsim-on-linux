#!/usr/bin/env bash
SIMLINK_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
SIMLINK_DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
SIMLINK_PREFIX="$SIMLINK_DATA/flightsim-on-linux/navigraph-simlink"
SIMLINK_FOLDER="$SIMLINK_PREFIX/drive_c/Program Files/Navigraph/Simlink"
SIMLINK_EXE='C:\Program Files\Navigraph\Simlink\NavigraphSimlink.exe'
SIMLINK_PATTERN='^[A-Za-z]:.*\\Simlink\\NavigraphSimlink\.exe'
SIMLINK_HANDLER=flightsim-on-linux-navigraph-simlink.desktop
SIMLINK_ENTRY="$SIMLINK_DATA/applications/$SIMLINK_HANDLER"
SIMLINK_SCHEME=x-scheme-handler/navigraph-traffic-desktop
SIMLINK_SCHEME_PREFIX=navigraph-traffic-desktop:
export SIMLINK_EXE SIMLINK_PATTERN SIMLINK_ENTRY SIMLINK_SCHEME SIMLINK_SCHEME_PREFIX

simlink_wine() {
  env -u NoDefaultCurrentDirectoryInExePath WINEPREFIX="$SIMLINK_PREFIX" WINEDEBUG=-all WINEDLLOVERRIDES=winemenubuilder.exe=d "$@"
}

simlink_wait() {
  timeout 60 env WINEPREFIX="$SIMLINK_PREFIX" wineserver -w
}

simlink_cfg() {
  python3 -I "$SIMLINK_PARTS/../../../fsol/simconnect.py" "$1" "${FSOL_ADDON:-navigraph-simlink}" "${FSOL_BASE:-}" "${2:-$SIMLINK_FOLDER}"
}
