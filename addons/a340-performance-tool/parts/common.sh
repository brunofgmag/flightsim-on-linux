#!/usr/bin/env bash
A340_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# shellcheck source=tools/addon-launch.sh
. "$A340_PARTS/../../../tools/addon-launch.sh"

A340_TITLE="A340 Performance Tool"
A340_WORK="${FSOL_BASE:-}/WASM/MSFS2024/aerosoft-aircraft-a346-pro/work"
A340_PREVLOG="$A340_WORK/ToLissPrevLog.txt"
A340_STATE="${FSOL_COMPATDATA:-}/flightsim-on-linux-backup/a340-performance-tool"
A340_SESSION="$A340_STATE/session"
A340_LAST_EVENT="$A340_STATE/last_event.txt"
A340_POLL="${A340_POLL:-3}"
A340_IDLE_LIMIT="${A340_IDLE_LIMIT:-10800}"
A340_SIM_PATTERN='^[A-Za-z]:.*\\common\\(.*\\)?FlightSimulator2024\.ex[e]'

a340_say() {
  echo "$1"
  addon_say "$A340_TITLE" "$1" 2> /dev/null
}

a340_fail() {
  a340_say "$1"
  exit 1
}

a340_sim_running() {
  [ "${FSOL_SIM_RUNNING:-0}" = 1 ]
}

a340_sim_alive() {
  pgrep -if -- "$A340_SIM_PATTERN" > /dev/null
}

a340_session_value() {
  sed -n "s/^$1=//p" "$A340_SESSION" 2> /dev/null
}

a340_set_last_event() {
  mkdir -p "$A340_STATE"
  printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" > "$A340_LAST_EVENT"
}

a340_watcher_alive() {
  local pid
  pid="$(cat "$A340_STATE/watch_$1.pid" 2> /dev/null || true)"
  [ -f "$A340_STATE/watch_$1.ready" ] && [ -n "$pid" ] && kill -0 "$pid" 2> /dev/null
}

a340_restore() {
  local mode
  [ -f "$A340_SESSION" ] || { echo "No session is active. ToLissPrevLog.txt was not changed."; return 0; }
  if [ -e "$A340_PREVLOG" ]; then
    [ ! -L "$A340_PREVLOG" ] || { echo "ToLissPrevLog.txt has become a link. Nothing was restored."; return 1; }
    mode="$(a340_session_value mode)"
    chmod "$mode" "$A340_PREVLOG"
    [ -w "$A340_PREVLOG" ] || { echo "ToLissPrevLog.txt is still not writable."; return 1; }
  fi
  rm -f "$A340_SESSION"
  a340_set_last_event "$1"
  echo "Write access to ToLissPrevLog.txt is back."
}
