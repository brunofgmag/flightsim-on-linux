#!/usr/bin/env bash
MIME_LIST="${XDG_CONFIG_HOME:-$HOME/.config}/mimeapps.list"

mime_default() {
  xdg-mime query default "$SIMLINK_SCHEME" 2> /dev/null || true
}

mime_apply() {
  local current backup="$1"
  if ! command -v xdg-mime > /dev/null; then
    echo "FAIL xdg-mime is not installed (xdg-utils), so the sign-in link cannot be handed to Simlink"
    return 1
  fi
  current="$(mime_default)"
  if [ "$current" = "$SIMLINK_HANDLER" ]; then
    echo "already links that start with navigraph-traffic-desktop: open $SIMLINK_HANDLER"
    return 0
  fi
  if [ -n "$current" ] && [ ! -e "$backup" ]; then
    mkdir -p "$(dirname "$backup")"
    printf '%s' "$current" > "$backup"
  fi
  mkdir -p "$(dirname "$MIME_LIST")"
  xdg-mime default "$SIMLINK_HANDLER" "$SIMLINK_SCHEME"
  if [ "$(mime_default)" != "$SIMLINK_HANDLER" ]; then
    echo "FAIL xdg-mime ran, but links that start with navigraph-traffic-desktop: still do not open $SIMLINK_HANDLER; check that the file exists in $(dirname "$SIMLINK_ENTRY") and that its Exec line starts with a program that exists"
    return 1
  fi
  echo "changed links that start with navigraph-traffic-desktop: open $SIMLINK_HANDLER (before: ${current:-nothing})"
}

mime_undo() {
  local backup="$1"
  if [ -e "$backup" ]; then
    xdg-mime default "$(cat "$backup")" "$SIMLINK_SCHEME"
    rm -f "$backup"
    rmdir "$(dirname "$backup")" 2> /dev/null || true
    echo "changed links that start with navigraph-traffic-desktop: open $(mime_default) again"
  elif [ "$(mime_default)" = "$SIMLINK_HANDLER" ]; then
    [ ! -f "$MIME_LIST" ] || sed -i --follow-symlinks "\\|^$SIMLINK_SCHEME=$SIMLINK_HANDLER;\\?\$|d" "$MIME_LIST"
    echo "changed links that start with navigraph-traffic-desktop: no program is set for them in $MIME_LIST"
  else
    echo "already $SIMLINK_HANDLER was not set for those links by this add-on"
  fi
}
