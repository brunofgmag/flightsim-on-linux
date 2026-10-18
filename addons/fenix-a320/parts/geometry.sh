#!/usr/bin/env bash

FENIX_GEOMETRY_SHA256="${FENIX_GEOMETRY_SHA256:-663f1d59ec1c014b9ea47a6cef71b3d43579e9759a82f3ee2cbd80b8c6d9e85f}"
GEOMETRY_KB_URL="https://download.microsoft.com/download/1/4/9/14936FE9-4D16-4019-A093-5E00182609EB/Windows6.1-KB2670838-x64.msu"
GEOMETRY_KB_SHA256="9fe71e7dcd2280ce323880b075ade6e56c49b68fc702a9b4c0a635f0f1fb9db8"
GEOMETRY_MSDELTA_URL="https://msdl.microsoft.com/download/symbols/msdelta.dll/559F38C482000/msdelta.dll"
GEOMETRY_MSDELTA_SHA256="29c10fb3ffa0e3cfd04c5247c3ed3a975575fad44d8a1e447b23b43213781653"
GEOMETRY_TAG="v0.1.0-preview.7"
GEOMETRY_HELPER_URL="https://raw.githubusercontent.com/marselnenaj/fenix-a320-linux-patch/$GEOMETRY_TAG/native/geometry-setup.c"
GEOMETRY_HELPER_SHA256="30acb1cbe446042263f06ab0b7150c92c651102c61f1cd5e76f62bf8d5570787"
GEOMETRY_CAB_SHA256="470b9ab769a46bbd1acc5e352156e2173a57cbbff03ff6f950d0989d9a150fbc"
GEOMETRY_MEMBER0_SHA256="e74c3bf4727f145ad81d9f1c2674cf6f8bab20acccee9d9c4cf2c375c75aed20"
GEOMETRY_MEMBER1_SHA256="1cc95120454739d69a08fddd7e0e0f125e76115c4eba0748db94c7753c84788c"
GEOMETRY_BASIS_SHA256="82dc3ddb8c3441ef2de0eb43ed187bc56b747e422a2f240cd33c978356746a7d"

geometry_target() {
  echo "$FSOL_PREFIX/drive_c/windows/system32/d2d1_geometry.dll"
}

geometry_cached() {
  echo "$FSOL_CACHE/fenix-geometry/d2d1_geometry.dll"
}

geometry_sha256() {
  sha256sum "$1" | cut -d' ' -f1
}

geometry_has_hash() {
  [ -f "$1" ] && [ "$(geometry_sha256 "$1")" = "$2" ]
}

geometry_expect() {
  geometry_has_hash "$1" "$2" || fail "$(basename "$1") does not have the expected sha256 $2" || return 1
}

geometry_provider_names() {
  python3 -I -c 'import ast, sys
tree = ast.parse(open(sys.argv[1]).read())
for node in tree.body:
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "user_settings" for t in node.targets):
        print(ast.literal_eval(node.value).get("WINE_D2D1_GEOMETRY_PROVIDER", ""))' "$1" 2> /dev/null
}

geometry_helper() {
  local work="$1" step="$2"
  shift 2
  fenix_wine "$work/pfx" "$(windows_path "$work/FenixGeometrySetup.exe")" "$@" || fail "the geometry helper failed to $step" || return 1
}

geometry_extract_dll() {
  local work="$1" msu="$2" msdelta="$3" cab delta
  cab="$(windows_path "$work/update.cab")"
  delta="$(windows_path "$msdelta")"
  export WINEDLLOVERRIDES="winemenubuilder.exe=d;mscoree,mshtml="
  fenix_wine "$work/pfx" wineboot -i > /dev/null 2>&1 || true
  geometry_helper "$work" "extract the update cab" extract "$(windows_path "$msu")" Windows6.1-KB2670838-x64.cab "$cab" || return 1
  geometry_expect "$work/update.cab" "$GEOMETRY_CAB_SHA256" || return 1
  geometry_helper "$work" "extract member 0" extract "$cab" 0 "$(windows_path "$work/0")" || return 1
  geometry_expect "$work/0" "$GEOMETRY_MEMBER0_SHA256" || return 1
  geometry_helper "$work" "extract member 1" extract "$cab" 1 "$(windows_path "$work/1")" || return 1
  geometry_expect "$work/1" "$GEOMETRY_MEMBER1_SHA256" || return 1
  geometry_helper "$work" "build the basis library" delta "$delta" - "$(windows_path "$work/0")" "$(windows_path "$work/basis.dll")" || return 1
  geometry_expect "$work/basis.dll" "$GEOMETRY_BASIS_SHA256" || return 1
  geometry_helper "$work" "apply the geometry delta" delta "$delta" "$(windows_path "$work/basis.dll")" "$(windows_path "$work/1")" "$(windows_path "$work/geometry.dll")" || return 1
  geometry_expect "$work/geometry.dll" "$FENIX_GEOMETRY_SHA256"
}

geometry_build_in() {
  local work="$1" msu msdelta source
  msu="$(fenix_download "$GEOMETRY_KB_URL" "$GEOMETRY_KB_SHA256" Windows6.1-KB2670838-x64.msu)" || return 1
  msdelta="$(fenix_download "$GEOMETRY_MSDELTA_URL" "$GEOMETRY_MSDELTA_SHA256" msdelta.dll)" || return 1
  source="$(fenix_download "$GEOMETRY_HELPER_URL" "$GEOMETRY_HELPER_SHA256" "geometry-setup-$GEOMETRY_TAG.c")" || return 1
  x86_64-w64-mingw32-gcc -Os -s -municode -Wl,--no-insert-timestamp -o "$work/FenixGeometrySetup.exe" "$source" -lsetupapi || fail "the geometry helper did not compile" || return 1
  echo "helper FenixGeometrySetup.exe sha256 $(geometry_sha256 "$work/FenixGeometrySetup.exe")"
  geometry_extract_dll "$work" "$msu" "$msdelta"
}

geometry_build() {
  local cached work status=0
  cached="$(geometry_cached)"
  geometry_has_hash "$cached" "$FENIX_GEOMETRY_SHA256" && return 0
  command -v x86_64-w64-mingw32-gcc > /dev/null || fail "missing program: x86_64-w64-mingw32-gcc (the MinGW cross compiler)" || return 1
  mkdir -p "$FSOL_CACHE/fenix-geometry"
  work="$(mktemp -d "$FSOL_CACHE/fenix-geometry/work.XXXXXX")"
  (geometry_build_in "$work") || status=1
  fenix_wineserver_kill "$work/pfx" > /dev/null 2>&1 || true
  if [ "$status" -eq 0 ]; then
    cp "$work/geometry.dll" "$cached"
  fi
  rm -rf "$work"
  return "$status"
}

geometry_apply() {
  local target
  target="$(geometry_target)"
  if geometry_has_hash "$target" "$FENIX_GEOMETRY_SHA256"; then
    echo "already d2d1_geometry.dll is in system32"
    return 0
  fi
  geometry_build || return 1
  fenix_stash "$target" d2d1_geometry.dll
  mkdir -p "$(dirname "$target")"
  cp "$(geometry_cached)" "$target"
  echo "changed d2d1_geometry.dll installed in system32"
}

geometry_check() {
  local target settings names status=0
  target="$(geometry_target)"
  settings="$FSOL_PROTON/user_settings.py"
  if geometry_has_hash "$target" "$FENIX_GEOMETRY_SHA256"; then
    ok "d2d1_geometry.dll in system32 has the expected sha256"
  elif [ -f "$target" ]; then
    fail "d2d1_geometry.dll in system32 is not the expected file; run apply" || status=1
  else
    fail "d2d1_geometry.dll is missing from system32; run apply" || status=1
  fi
  names="$(geometry_provider_names "$settings")"
  if [[ ";$names;" == *";FenixDisplay.exe;"* ]]; then
    ok "Proton loads the geometry provider in FenixDisplay.exe"
  else
    fail "$settings does not set WINE_D2D1_GEOMETRY_PROVIDER to FenixDisplay.exe; use Proton FS2024" || status=1
  fi
  return "$status"
}

geometry_undo() {
  fenix_restore "$(geometry_target)" d2d1_geometry.dll system32
}
