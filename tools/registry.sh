#!/usr/bin/env bash
REGISTRY_TOOLS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
REGISTRY_WAIT="${FSOL_REGISTRY_WAIT:-40}"

registry_get() {
  python3 -I "$REGISTRY_TOOLS/registry-value.py" "$FSOL_PREFIX" "$1" "$2"
}

registry_stash() {
  : "${REGISTRY_BACKUP:?FSOL_COMPATDATA is not set}"
  local name="$2" stash="$REGISTRY_BACKUP/$2" value
  if [ -e "$stash" ] || [ -e "$stash.absent" ]; then
    return 0
  fi
  mkdir -p "$REGISTRY_BACKUP"
  if value="$(registry_get "$1" "$name")"; then
    printf '%s' "$value" > "$stash"
  else
    : > "$stash.absent"
  fi
}

registry_wait() {
  local second
  for ((second = 0; second < REGISTRY_WAIT; second++)); do
    [ "$(registry_get "$1" "$2" || true)" = "$3" ] && return 0
    sleep 1
  done
  [ "$(registry_get "$1" "$2" || true)" = "$3" ]
}

registry_write() {
  addon_alone reg add "HKCU\\$1" /v "$2" /t REG_SZ /d "$3" /f > /dev/null
  if ! registry_wait "$1" "$2" "$3"; then
    echo "FAIL Wine has not written $2 to user.reg yet; close the programs open in the prefix and run apply again" >&2
    return 1
  fi
}

registry_set() {
  local key="$1" name="$2" value="$3"
  if [ "$(registry_get "$key" "$name" || true)" = "$value" ]; then
    echo "already $name is $value"
    return 0
  fi
  registry_stash "$key" "$name"
  registry_write "$key" "$name" "$value"
  echo "changed $name to $value"
}

registry_restore() {
  : "${REGISTRY_BACKUP:?FSOL_COMPATDATA is not set}"
  local key="$1" name="$2" stash="$REGISTRY_BACKUP/$2"
  if [ -e "$stash" ]; then
    addon_alone reg add "HKCU\\$key" /v "$name" /t REG_SZ /d "$(cat "$stash")" /f > /dev/null
    rm -f "$stash"
    echo "changed $name is back to the value it had before"
  elif [ -e "$stash.absent" ]; then
    addon_alone reg delete "HKCU\\$key" /v "$name" /f > /dev/null
    rm -f "$stash.absent"
    echo "changed $name is removed from the registry"
  else
    echo "already $name was not changed by this add-on"
  fi
  rmdir "$REGISTRY_BACKUP" 2> /dev/null || true
}
