#!/usr/bin/env bash
GSX_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
FSOL_ROOT="$(cd "$GSX_PARTS/../../.." && pwd)"
GSX_BACKUP="${FSOL_COMPATDATA:?FSOL_COMPATDATA is not set}/flightsim-on-linux-backup/gsx-pro"
GSX_ADDON_MANAGER="${FSOL_PREFIX:?FSOL_PREFIX is not set}/drive_c/Program Files (x86)/Addon Manager"
GSX_MANIFEST="$GSX_ADDON_MANAGER/QlmLicenseLib.dll.manifest"
GSX_WRAPPER_FOLDER="$FSOL_PREFIX/drive_c/flightsim-on-linux"
GSX_WRAPPER_WINDOWS='C:\flightsim-on-linux\com-environment.exe'
GSX_WEBVIEW2_OPTIONS="--in-process-gpu --disable-gpu --disable-gpu-compositing --disable-software-rasterizer"
GSX_CURL_URL="${GSX_CURL_URL:-https://curl.se/windows/dl-8.22.0_3/curl-8.22.0_3-win64-mingw.zip}"
GSX_CURL_SHA256="${GSX_CURL_SHA256:-f38b0ac2e3a280fd414b607c874a21e6dd4d1ed0e2ae622afbf0020917446561}"
GSX_CURL_FOLDERS="system32 syswow64"
GSX_CURL_FILES="curl.exe curl-ca-bundle.crt"
export GSX_PARTS FSOL_ROOT GSX_BACKUP GSX_MANIFEST GSX_WRAPPER_FOLDER GSX_WRAPPER_WINDOWS GSX_WEBVIEW2_OPTIONS GSX_CURL_URL GSX_CURL_SHA256 GSX_CURL_FOLDERS GSX_CURL_FILES

# shellcheck source=tools/addon-launch.sh
. "$FSOL_ROOT/tools/addon-launch.sh"
# shellcheck source=tools/shim.sh
. "$FSOL_ROOT/tools/shim.sh"

gsx_die() {
  echo "gsx-pro: $*" >&2
  exit 2
}

gsx_regedit() {
  addon_alone regedit /S "$(addon_windows_path "$1")" > /dev/null
}
