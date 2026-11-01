#!/usr/bin/env bash
DISPATCH_PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/flightsim-on-linux/flightsim-dispatch"
DISPATCH_FOLDER="$DISPATCH_PREFIX/drive_c/FlightSimDispatch"
DISPATCH_PATTERN='^[A-Za-z]:\\FlightSimDispatch\\FlightSimDispatch\.exe'
export DISPATCH_FOLDER DISPATCH_PATTERN

dispatch_wine() {
  env -u NoDefaultCurrentDirectoryInExePath WINEPREFIX="$DISPATCH_PREFIX" WINEDEBUG=-all WINEDLLOVERRIDES=winemenubuilder.exe=d "$@"
}

dispatch_wait() {
  timeout 60 env WINEPREFIX="$DISPATCH_PREFIX" wineserver -w
}
