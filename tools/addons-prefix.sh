#!/usr/bin/env bash

PROTON_OVERRIDES='steam.exe=b;dotnetfx35.exe=b;dotnetfx35setup.exe=b;beclient.dll=b,n;beclient_x64.dll=b,n;winebth.sys=d;opencl=n,d'
DXVK_OVERRIDES='d3d11=n;d3d10core=n;d3d9=n;dxgi=n;d3d12=n;d3d12core=n'
DRIVER_OVERRIDES='winewayland.drv=d;winex11.drv=b'

addon_sibling_prefix() {
  echo "${XDG_DATA_HOME:-$HOME/.local/share}/flightsim-on-linux/addons-prefix"
}

addon_sibling_prefix_running() {
  local prefix="$1" folder pid
  [ -d "$prefix" ] || return 1
  folder="/tmp/.wine-$(id -u)/server-$(stat -L -c %D "$prefix")-$(printf '%x' "$(stat -L -c %i "$prefix")")"
  for pid in $(pgrep -x wineserver); do
    [ "$(readlink "/proc/$pid/cwd")" = "$folder" ] && return 0
  done
  return 1
}

addon_sibling_prefix_link_drives() {
  local sim="$1" sibling="$2" drive name
  ln -sfn "$sim/drive_c" "$sibling/drive_c"
  ln -sfn ../drive_c "$sibling/dosdevices/c:"
  ln -sfn / "$sibling/dosdevices/z:"
  for drive in "$sim"/dosdevices/*:; do
    name="$(basename "$drive")"
    case "$name" in c: | z:) continue ;; esac
    [ -L "$drive" ] && ln -sfn "$(readlink "$drive")" "$sibling/dosdevices/$name"
  done
  return 0
}

addon_sibling_prefix_create() {
  local sim="${FSOL_PREFIX:?FSOL_PREFIX is not set}" sibling file
  sibling="$(addon_sibling_prefix)"
  if [ -f "$sibling/system.reg" ] && [ "${FSOL_ADDONS_PREFIX_RESET:-0}" != 1 ]; then
    echo "the add-ons prefix already exists at $sibling; FSOL_ADDONS_PREFIX_RESET=1 copies the registry again" >&2
    return 0
  fi
  if addon_sibling_prefix_running "$sim"; then
    echo "close the simulator and every program of its prefix first; only then is its registry whole on disk" >&2
    return 1
  fi
  mkdir -p "$sibling/dosdevices"
  if addon_sibling_prefix_running "$sibling"; then
    echo "close the programs of the add-ons prefix before its registry is copied" >&2
    return 1
  fi
  for file in system.reg user.reg userdef.reg; do
    [ -f "$sim/$file" ] || { [ "$file" = userdef.reg ] && continue; echo "$sim/$file is missing" >&2; return 1; }
    cp -p "$sim/$file" "$sibling/"
  done
  printf 'disable\n' > "$sibling/.update-timestamp"
  addon_sibling_prefix_link_drives "$sim" "$sibling"
}

addon_sibling_prefix_wine() {
  local lib="${FSOL_PROTON:?FSOL_PROTON is not set}/files/lib" sibling
  sibling="$(addon_sibling_prefix)"
  mkdir -p "$sibling-gstreamer"
  (
    export WINEPREFIX="$sibling"
    export WINEDLLPATH="$lib/vkd3d:$lib/wine"
    export WINEDLLOVERRIDES="$PROTON_OVERRIDES;$DXVK_OVERRIDES;$DRIVER_OVERRIDES;winemenubuilder.exe=d${EXTRA_WINEDLLOVERRIDES:+;$EXTRA_WINEDLLOVERRIDES}"
    export LD_LIBRARY_PATH="$lib/x86_64-linux-gnu:$lib/i386-linux-gnu"
    export GST_PLUGIN_SYSTEM_PATH_1_0="$lib/x86_64-linux-gnu/gstreamer-1.0:$lib/i386-linux-gnu/gstreamer-1.0"
    export WINE_GST_REGISTRY_DIR="$sibling-gstreamer/"
    export WINEESYNC=1 WINEFSYNC=1 WINE_LARGE_ADDRESS_AWARE=1
    export WINEDEBUG="${WINEDEBUG:--all}"
    unset NoDefaultCurrentDirectoryInExePath
    exec "$FSOL_PROTON/files/bin/wine" "$@"
  )
}
