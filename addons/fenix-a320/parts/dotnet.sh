#!/usr/bin/env bash
# shellcheck source=addons/dotnet48/parts/common.sh
. "$FSOL_ROOT/addons/dotnet48/parts/common.sh"
# shellcheck source=addons/dotnet48/parts/dotnet48.sh
. "$FSOL_ROOT/addons/dotnet48/parts/dotnet48.sh"

dotnet_overrides() {
  python3 -I "$FENIX_PARTS/overrides.py" "$@"
}

dotnet_back_up_overrides() {
  local file="$FENIX_BACKUP/registry/HKCU_Software_Wine_AppDefaults.reg"
  [ ! -e "$file" ] || return 0
  mkdir -p "$(dirname "$file")"
  dotnet48_wine "$FSOL_PREFIX" reg export 'HKCU\Software\Wine\AppDefaults' "$(windows_path "$file")" /y > /dev/null 2>&1 || true
}

dotnet_set_overrides() {
  local missing
  missing="$(dotnet_overrides missing "$FSOL_PREFIX")"
  if [ -z "$missing" ]; then
    echo "already mscoree native for the Fenix executables"
    return 0
  fi
  dotnet_back_up_overrides
  dotnet48_run_wine_arguments "$missing"
  echo "changed $(wc -l <<< "$missing") registry values: mscoree is native for the Fenix executables only"
}

dotnet_apply() {
  dotnet48_apply
  dotnet_set_overrides
}

dotnet_check() {
  local status=0
  dotnet48_check || status=1
  dotnet_overrides check "$FSOL_PREFIX" || status=1
  return "$status"
}

dotnet_remove_overrides() {
  local lines
  lines="$(dotnet_overrides remove "$FSOL_PREFIX")"
  if [ -z "$lines" ]; then
    echo "already no mscoree override for the Fenix executables"
    return 0
  fi
  dotnet48_run_wine_arguments "$lines"
  echo "removed $(wc -l <<< "$lines") mscoree overrides for the Fenix executables"
}

dotnet_undo() {
  dotnet_remove_overrides
  echo "left .NET Framework 4.8, Microsoft's mscoree.dll and gac=n in the prefix: other add-ons use them; flightsim-on-linux addon undo dotnet48 gives the original mscoree.dll back"
}
