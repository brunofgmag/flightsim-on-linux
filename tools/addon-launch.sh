#!/usr/bin/env bash

TOOLS="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=tools/proton-runner.sh
. "$TOOLS/proton-runner.sh"

addon_say() {
  local title="$1" message="$2"
  echo "$title: $message" >&2
  if [ ! -t 2 ] && command -v notify-send > /dev/null; then
    notify-send -a "$title" -i dialog-information "$title" "$message" || true
  fi
}

addon_refuse_second() {
  if pgrep -f -- "$1" > /dev/null; then
    addon_say "${FSOL_ADDON:-add-on}" "it is already running"
    exit 0
  fi
}

addon_need_simulator() {
  if [ "${FSOL_SIM_RUNNING:-0}" != 1 ]; then
    addon_say "${FSOL_ADDON:-add-on}" "start Microsoft Flight Simulator 2024 first; this program runs inside the simulator's container"
    exit 1
  fi
}

addon_windows_path() {
  local path="$1" drive_c="${FSOL_PREFIX:-}/drive_c/"
  if [ -n "${FSOL_PREFIX:-}" ] && [ "${path#"$drive_c"}" != "$path" ]; then
    path="${path#"$drive_c"}"
    echo "C:\\${path//\//\\}"
  else
    windows_path "$path"
  fi
}

addon_in_prefix() {
  (
    while [[ "${1:-}" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; do
      export "${1?}"
      shift
    done
    [ -z "${EXTRA_WINEDLLOVERRIDES:-}" ] || export WINEDLLOVERRIDES="${WINEDLLOVERRIDES:+$WINEDLLOVERRIDES;}$EXTRA_WINEDLLOVERRIDES"
    unset NoDefaultCurrentDirectoryInExePath
    addon_prefix_run "$FSOL_PREFIX" wine "$@"
  )
}

addon_beside_simulator() {
  if [ "${FSOL_SIM_RUNNING:-0}" = 1 ]; then
    "$FSOL_CLI" run "$@"
  else
    addon_in_prefix "$@"
  fi
}

addon_alone() {
  local code=0
  addon_in_prefix "$@" || code=$?
  timeout 60 env WINEPREFIX="$FSOL_PREFIX" "$FSOL_PROTON/files/bin/wineserver" -w > /dev/null 2>&1 || echo "programs are still open in the prefix after the installer; close them before the next one" >&2
  return "$code"
}

addon_log_file() {
  local folder="${FSOL_CACHE:?FSOL_CACHE is not set}/logs" file
  file="$folder/$1.log"
  (umask 077 && mkdir -p "$folder" && : > "$file")
  echo "$file"
}

addon_newest() {
  compgen -G "$1" | sort -V | tail -n 1 || true
}

addon_close_by_pattern() {
  local title="$1" pattern="$2"
  if ! pgrep -f -- "$pattern" > /dev/null; then
    echo "$title is not running"
    return 0
  fi
  pkill -TERM -f -- "$pattern" || true
  if ! addon_wait_gone "$pattern"; then
    addon_say "${FSOL_ADDON:-add-on}" "$title is still running after the request to end it"
    return 1
  fi
}

addon_prefix_free_or_say() {
  local title="$1" state busy programs
  state="$("$FSOL_CLI" machine --json 2> /dev/null | python3 -I -c 'import json, sys; d = json.load(sys.stdin); print("busy" if d.get("prefix_in_use") else "free"); print(", ".join(d.get("prefix_programs") or []))' 2> /dev/null)" || return 0
  busy="$(sed -n 1p <<< "$state")"
  programs="$(sed -n 2p <<< "$state")"
  [ "$busy" = busy ] || return 0
  if [ -n "$programs" ]; then
    addon_say "$title" "close $programs first; opened alone, it needs the simulator's prefix to itself"
  else
    addon_say "$title" "Wine is still running in the prefix; wait a few seconds and try again"
  fi
  exit 1
}

addon_stash() {
  local target="$1" folder="$2" backup
  backup="$folder/$(basename "$target")"
  if [ -e "$backup" ] || [ -e "$backup.absent" ]; then
    return 0
  fi
  mkdir -p "$folder"
  if [ -f "$target" ]; then
    cp -p "$target" "$backup"
  else
    : > "$backup.absent"
  fi
}

addon_unstash() {
  local target="$1" backup
  backup="$2/$(basename "$1")"
  if [ -f "$backup" ]; then
    cp -p "$backup" "$target"
  elif [ -e "$backup.absent" ]; then
    rm -f "$target"
  fi
  rm -f "$backup" "$backup.absent"
}

addon_wait_gone() {
  local pattern="$1" seconds="${2:-${FSOL_CLOSE_WAIT:-10}}" second
  for ((second = 0; second < seconds; second++)); do
    pgrep -f -- "$pattern" > /dev/null || return 0
    sleep 1
  done
  ! pgrep -f -- "$pattern" > /dev/null
}
