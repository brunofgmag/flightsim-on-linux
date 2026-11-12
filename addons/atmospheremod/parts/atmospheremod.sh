#!/usr/bin/env bash
ATMOSPHEREMOD_VERSION="0.7.2"
ATMOSPHEREMOD_URL="https://github.com/tomwhoca/AtmosphereMod/releases/download/v$ATMOSPHEREMOD_VERSION/AtmosphereMod-$ATMOSPHEREMOD_VERSION.zip"
ATMOSPHEREMOD_SHA256="6d916cab5c7331708911b63286547452991ed0456d70883d7ef249efa937a118"
ATMOSPHEREMOD_WINDOWS_DATA='C:\users\steamuser\AppData\Local\AtmosphereMod'
ATMOSPHEREMOD_EFB="atmospheremod-weather"

atmospheremod_sha256() {
  sha256sum "$1" | cut -d ' ' -f 1
}

atmospheremod_sim() {
  echo "${FSOL_SIM_INSTALL:?no simulator install folder found; run: flightsim-on-linux machine}"
}

atmospheremod_data_folder() {
  echo "${FSOL_PREFIX:?FSOL_PREFIX is not set}/drive_c/users/steamuser/AppData/Local/AtmosphereMod"
}

atmospheremod_refuse_running() {
  [ "${FSOL_SIM_RUNNING:-0}" != 1 ] || { echo "close the simulator before you $1 AtmosphereMod" >&2; exit 1; }
}

atmospheremod_refuse_without_reshade() {
  [ -f "$(atmospheremod_sim)/dxgi.dll" ] || { echo "ReShade is not in the simulator folder; run first: flightsim-on-linux addon install reshade <dxgi.dll of a Windows ReShade install>" >&2; exit 1; }
}

atmospheremod_beside_renodx() {
  compgen -G "$(atmospheremod_sim)/renodx-*.addon64" > /dev/null
}

atmospheremod_is_installed() {
  local sim
  sim="$(atmospheremod_sim)"
  [ -e "$sim/AtmosphereMod.addon64" ] && return 0
  grep -qa '^\[ATMOSPHEREMOD\]' "$sim/ReShade.ini" 2> /dev/null && return 0
  [ -e "${FSOL_COMMUNITY:-/nonexistent}/$ATMOSPHEREMOD_EFB" ] || [ -L "${FSOL_COMMUNITY:-/nonexistent}/$ATMOSPHEREMOD_EFB" ]
}

atmospheremod_fetch() {
  local url="$1" sha="$2" file="$3"
  if [ -f "$file" ] && [ "$(atmospheremod_sha256 "$file")" = "$sha" ]; then
    echo "already downloaded: $file"
    return 0
  fi
  mkdir -p "$(dirname "$file")"
  curl --fail --location --silent --show-error --output "$file.part" "$url"
  if [ "$(atmospheremod_sha256 "$file.part")" != "$sha" ]; then
    rm -f "$file.part"
    echo "the download from $url is not the file this add-on was tested with (sha256 $sha); nothing was run" >&2
    return 1
  fi
  mv "$file.part" "$file"
}

atmospheremod_zip() {
  local given="${FSOL_INSTALLER:-}"
  if [ -z "$given" ]; then
    ATMOSPHEREMOD_ZIP="${FSOL_CACHE:?FSOL_CACHE is not set}/downloads/AtmosphereMod-$ATMOSPHEREMOD_VERSION.zip"
    atmospheremod_fetch "$ATMOSPHEREMOD_URL" "$ATMOSPHEREMOD_SHA256" "$ATMOSPHEREMOD_ZIP"
    return
  fi
  if [ "$(atmospheremod_sha256 "$given")" != "$ATMOSPHEREMOD_SHA256" ]; then
    echo "$given is not the release this add-on was tested with (sha256 $ATMOSPHEREMOD_SHA256); nothing was run" >&2
    return 1
  fi
  ATMOSPHEREMOD_ZIP="$given"
}

atmospheremod_community() {
  local community="${FSOL_COMMUNITY:-}" link
  [ -d "$community" ] || { echo "no Community folder found; run: flightsim-on-linux machine" >&2; return 1; }
  link="$community/$ATMOSPHEREMOD_EFB"
  if [ -L "$link" ]; then
    dirname "$(readlink -f "$link")"
  else
    echo "$community"
  fi
}

atmospheremod_release_folder() {
  local releases=("$1"/AtmosphereMod-*)
  [ "${#releases[@]}" -eq 1 ] && [ -f "${releases[0]}/installer/install.py" ] || { echo "the zip does not hold one AtmosphereMod-<version> folder with installer/install.py" >&2; return 1; }
  echo "${releases[0]}"
}

atmospheremod_install_arguments() {
  local installer="$1" community="$2"
  ATMOSPHEREMOD_ARGUMENTS=(--data "$ATMOSPHEREMOD_WINDOWS_DATA" --yes --sim "$(addon_windows_path "$(atmospheremod_sim)")" --community "$(addon_windows_path "$community")" --dxc "$installer\\dxc\\bin\\x64")
  if atmospheremod_beside_renodx; then
    echo "RenoDX is in the simulator folder, so the installer runs with --renodx and puts AtmosphereMod's parameters in register space 51, away from RenoDX's"
    ATMOSPHEREMOD_ARGUMENTS+=(--renodx)
  fi
}

atmospheremod_uninstall_arguments() {
  local community="$2"
  ATMOSPHEREMOD_ARGUMENTS=(--data "$ATMOSPHEREMOD_WINDOWS_DATA" --uninstall --sim "$(addon_windows_path "$(atmospheremod_sim)")" --community "$(addon_windows_path "$community")")
}

atmospheremod_wine() {
  local mode="$1" release="$2" community="$3" installer
  installer="$(addon_windows_path "$release/installer")"
  "atmospheremod_${mode}_arguments" "$installer" "$community"
  [ -f "$(addon_sibling_prefix)/system.reg" ] || addon_sibling_prefix_create
  if [ "$mode" = uninstall ]; then
    addon_sibling_prefix_wine "$installer\\python\\python.exe" "$installer\\install.py" "${ATMOSPHEREMOD_ARGUMENTS[@]}" <<< $'y\nn'
  else
    addon_sibling_prefix_wine "$installer\\python\\python.exe" "$installer\\install.py" "${ATMOSPHEREMOD_ARGUMENTS[@]}" < /dev/null
  fi
}

atmospheremod_run() {
  local mode="$1" zip="$2" community work release code=0
  community="$(atmospheremod_community)" || return 1
  work="$(mktemp -d)"
  python3 -I -m zipfile -e "$zip" "$work" \
    && release="$(atmospheremod_release_folder "$work")" \
    && atmospheremod_wine "$mode" "$release" "$community" || code=$?
  rm -rf "$work"
  return "$code"
}
