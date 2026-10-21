#!/usr/bin/env bash
set -uo pipefail

INSTALL_PROCESSES='FenixApp\\(Update|Fenix Installer|current\\FenixApp)[.]exe'
QUIET_CHECKS=3

status=0
wine "$PWD/current/FenixApp.exe" || status=$?

quiet=0
while [ "$quiet" -lt "$QUIET_CHECKS" ]; do
  if pgrep -f "$INSTALL_PROCESSES" > /dev/null; then
    quiet=0
  else
    quiet=$((quiet + 1))
  fi
  sleep 1
done
exit "$status"
