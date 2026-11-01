#!/usr/bin/env bash
CONTRAIL_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# shellcheck source=tools/shim.sh
. "$CONTRAIL_PARTS/../../../tools/shim.sh"
CONTRAIL_PROXY_MARK="winhttp_wine.DllCanUnloadNow"

contrail_apps() {
  compgen -G "${FSOL_PREFIX:?FSOL_PREFIX is not set}/drive_c/users/*/AppData/Local/ContrailApp/app-*" | sort -V || true
}

contrail_newest() {
  contrail_apps | tail -n 1
}

contrail_wine_winhttp() {
  echo "${FSOL_PROTON:?FSOL_PROTON is not set}/files/lib/wine/x86_64-windows/winhttp.dll"
}

contrail_backup() {
  echo "${FSOL_COMPATDATA:?FSOL_COMPATDATA is not set}/flightsim-on-linux-backup/contrail/$(basename "$1")/winhttp.dll"
}

contrail_install_into() {
  local app="$1" proxy="$2" wine_winhttp backup
  wine_winhttp="$(contrail_wine_winhttp)"
  [ -f "$wine_winhttp" ] || { echo "Proton has no winhttp.dll at $wine_winhttp" >&2; return 1; }
  if [ -f "$app/winhttp.dll" ] && ! grep -qa "$CONTRAIL_PROXY_MARK" "$app/winhttp.dll"; then
    backup="$(contrail_backup "$app")"
    mkdir -p "$(dirname "$backup")"
    cp -p "$app/winhttp.dll" "$backup"
    echo "the app's own winhttp.dll is kept in $backup"
  fi
  install -m 644 "$proxy" "$app/winhttp.dll"
  install -m 644 "$wine_winhttp" "$app/winhttp_wine.dll"
}

contrail_apply() {
  local app proxy
  app="$(contrail_newest)"
  [ -n "$app" ] || { echo "Contrail is not installed in the prefix; run: flightsim-on-linux addon install contrail <ContrailSetup.exe>" >&2; return 1; }
  proxy="$(shim_path winhttp-proxy winhttp.dll)"
  contrail_install_into "$app" "$proxy"
  echo "winhttp.dll and winhttp_wine.dll are in $app"
  echo "the launch script puts them in the newest app folder again each time, so a Contrail update needs nothing from you"
}

contrail_check() {
  local app proxy status=0
  app="$(contrail_newest)"
  if [ -z "$app" ]; then
    echo "FAIL Contrail is not installed in the prefix"
    return 1
  fi
  proxy="$(shim_path winhttp-proxy winhttp.dll)" || { echo "FAIL the winhttp stand-in could not be built"; return 1; }
  if cmp -s "$proxy" "$app/winhttp.dll"; then
    echo "ok $(basename "$app") has the winhttp stand-in built from this repository"
  else
    echo "FAIL $(basename "$app") has no winhttp stand-in, or an older build of it; start Contrail from its menu entry or run: flightsim-on-linux addon apply contrail"
    status=1
  fi
  if cmp -s "$(contrail_wine_winhttp)" "$app/winhttp_wine.dll"; then
    echo "ok winhttp_wine.dll in $(basename "$app") is the winhttp.dll of the Proton in use"
  else
    echo "FAIL winhttp_wine.dll in $(basename "$app") is missing or is not the winhttp.dll of the Proton in use; the stand-in forwards to it"
    status=1
  fi
  return "$status"
}

contrail_undo() {
  local app backup
  echo "the menu entry puts the stand-in back on its next start; take it out with: flightsim-on-linux addon shortcut contrail --remove"
  while IFS= read -r app; do
    [ -n "$app" ] || continue
    rm -f "$app/winhttp_wine.dll"
    if [ -f "$app/winhttp.dll" ] && grep -qa "$CONTRAIL_PROXY_MARK" "$app/winhttp.dll"; then
      rm "$app/winhttp.dll"
    fi
    backup="$(contrail_backup "$app")"
    if [ -f "$backup" ]; then
      cp -p "$backup" "$app/winhttp.dll"
      rm "$backup"
    fi
    echo "$(basename "$app") is as the installer left it"
  done < <(contrail_apps)
}
