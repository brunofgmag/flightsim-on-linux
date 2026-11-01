#!/usr/bin/env bash

find_steam() {
  local candidate
  for candidate in "${STEAM_DIR:-}" "$HOME/.local/share/Steam" "$HOME/.steam/steam" \
    "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam" "$HOME/snap/steam/common/.local/share/Steam"; do
    [ -n "$candidate" ] && [ -f "$candidate/steamapps/libraryfolders.vdf" ] || continue
    readlink -f "$candidate"
    return
  done
}

find_runtime() {
  local steam appid library manifest installdir
  steam="$(find_steam)"
  [ -n "$steam" ] && [ -f "$TOOL/toolmanifest.vdf" ] || return 0
  appid="$(sed -n 's/^[[:space:]]*"require_tool_appid"[[:space:]]*"\([0-9]*\)"$/\1/p' "$TOOL/toolmanifest.vdf")"
  [ -n "$appid" ] || return 0
  while read -r library; do
    manifest="$library/steamapps/appmanifest_$appid.acf"
    [ -f "$manifest" ] || continue
    installdir="$(sed -n 's/^[[:space:]]*"installdir"[[:space:]]*"\(.*\)"$/\1/p' "$manifest")"
    [ -x "$library/steamapps/common/$installdir/run" ] || continue
    echo "$library/steamapps/common/$installdir/run"
    return
  done < <(sed -n 's/^[[:space:]]*"path"[[:space:]]*"\(.*\)"$/\1/p' "$steam/steamapps/libraryfolders.vdf")
}

export_settings() {
  local line
  [ -f "$TOOL/user_settings.py" ] || return 0
  while IFS= read -r line; do
    export "${line?}"
  done < <(python3 -I -c 'import ast, sys
tree = ast.parse(open(sys.argv[1]).read())
for node in tree.body:
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "user_settings" for t in node.targets):
        for key, value in ast.literal_eval(node.value).items():
            print(f"{key}={value}")' "$TOOL/user_settings.py")
}

windows_path() {
  local path="$1"
  echo "Z:${path//\//\\}"
}

run_wine() {
  timeout 120 "${RUNNER[@]}" "$TOOL/files/bin/wine" "$@" 2> /dev/null | tr -d '\r'
}

stop_prefix() {
  timeout 60 "${RUNNER[@]}" "$TOOL/files/bin/wineserver" -k > /dev/null 2>&1 || true
}

set_runner() {
  RUNTIME="$(find_runtime)"
  if [ -n "$RUNTIME" ]; then
    RUNNER=("$RUNTIME" --)
  else
    RUNNER=(env)
  fi
}

addon_prefix_run() {
  local prefix="$1" program="$2" runner=(env) runtime
  shift 2
  runtime="$(TOOL="$FSOL_PROTON" STEAM_DIR="${STEAM_DIR:-${FSOL_STEAM:-}}" find_runtime)"
  [ -z "$runtime" ] || runner=("$runtime" --)
  "${runner[@]}" env WINEPREFIX="$prefix" WINEDEBUG="${WINEDEBUG:--all}" \
    WINEDLLPATH="$FSOL_PROTON/files/lib/vkd3d:$FSOL_PROTON/files/lib/wine" \
    "$FSOL_PROTON/files/bin/$program" "$@" < /dev/null
}
