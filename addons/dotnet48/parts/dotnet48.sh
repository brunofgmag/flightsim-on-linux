#!/usr/bin/env bash
DOTNET48_URL="https://download.visualstudio.microsoft.com/download/pr/7afca223-55d2-470a-8edc-6a1739ae3252/abd170b4b0ec15ad0222a809b761a036/ndp48-x86-x64-allos-enu.exe"
DOTNET48_SHA256="95889d6de3f2070c07790ad6cf2000d33d9a1bdfc6a381725ab82ab1c314fd53"
DOTNET48_FILE="ndp48-x86-x64-allos-enu.exe"
DOTNET40_URL="https://download.microsoft.com/download/9/5/A/95A9616B-7A37-4AF6-BC36-D6EA96C8DAAE/dotNetFx40_Full_x86_x64.exe"
DOTNET40_SHA256="65e064258f2e418816b304f646ff9e87af101e4c9552ab064bb74d281c38659f"
DOTNET40_FILE="dotNetFx40_Full_x86_x64.exe"
DOTNET48_WINDOWS_VERSION="$DOTNET48_BACKUP/windows-version.json"
DOTNET48_MSCOREE_FOLDERS="system32:amd64 syswow64:x86"
DOTNET48_REGISTRY_BACKUPS=(
  "HKCU\\Software\\Wine\\DllOverrides"
  "HKCU\\Software\\Wine\\Mono"
  "HKLM\\Software\\Microsoft\\.NETFramework"
  "HKLM\\Software\\Microsoft\\.NETFramework /reg:32"
)

dotnet48_py() {
  python3 -I "$DOTNET48_PARTS/dotnet48.py" "$@"
}

dotnet48_check() {
  dotnet48_py check "$FSOL_PREFIX"
}

dotnet48_clear_symlinks() {
  local version
  for version in Framework Framework64; do
    [ ! -d "$FSOL_PREFIX/drive_c/windows/Microsoft.NET/$version/v4.0.30319" ] ||
      find "$FSOL_PREFIX/drive_c/windows/Microsoft.NET/$version/v4.0.30319" -maxdepth 1 -type l -delete
  done
}

dotnet48_run_wine_arguments() {
  local arguments
  while IFS=$'\t' read -r -a arguments; do
    [ "${#arguments[@]}" -gt 0 ] || continue
    dotnet48_wine "$FSOL_PREFIX" "${arguments[@]}" > /dev/null
  done <<< "$1"
  dotnet48_wineserver_kill "$FSOL_PREFIX"
}

dotnet48_restore_windows_version() {
  local saved="$DOTNET48_WINDOWS_VERSION" arguments
  [ -f "$saved" ] || return 0
  arguments="$(dotnet48_py winver-restore "$FSOL_PREFIX" "$saved")"
  dotnet48_run_wine_arguments "$arguments"
  [ -z "$(dotnet48_py winver-restore "$FSOL_PREFIX" "$saved")" ] || dotnet48_die "the Windows version keys of the prefix did not go back to what they were; the originals are in $saved"
  rm -f "$saved"
}

dotnet48_run_installer() {
  local installer="$1" status=0
  dotnet48_restore_windows_version
  mkdir -p "$DOTNET48_BACKUP"
  dotnet48_py winver-save "$FSOL_PREFIX" "$DOTNET48_WINDOWS_VERSION"
  dotnet48_wine "$FSOL_PREFIX" winecfg -v win7
  dotnet48_wineserver_kill "$FSOL_PREFIX"
  dotnet48_clear_symlinks
  WINEDLLOVERRIDES=fusion=b dotnet48_wine "$FSOL_PREFIX" "$installer" /q /norestart > /dev/null || status=$?
  dotnet48_wineserver_kill "$FSOL_PREFIX"
  dotnet48_restore_windows_version
  return "$status"
}

dotnet48_install_runtime() {
  local installer
  if dotnet48_py check "$FSOL_PREFIX" clr > /dev/null; then
    echo "already .NET Framework 4.8 in the prefix"
    return 0
  fi
  installer="$(dotnet48_download "$DOTNET48_URL" "$DOTNET48_SHA256" "$DOTNET48_FILE")"
  local status=0
  dotnet48_run_installer "$installer" || status=$?
  dotnet48_py check "$FSOL_PREFIX" clr > /dev/null || dotnet48_die "the .NET Framework 4.8 installer finished with exit code $status but clr.dll is not 4.8 in the prefix"
  echo "changed installed .NET Framework 4.8 (the Windows version was Windows 7 only while it ran)"
}

dotnet48_extract_mscoree() {
  local archive="$1" out="$2" work status=0
  work="$(mktemp -d "${TMPDIR:-/tmp}/dotnet48-mscoree.XXXXXX")"
  7z x -y -o"$work" "$archive" netfx_Core.mzz > /dev/null &&
    cabextract -q -d "$work" -F '_003_mscoree_dll_*' "$work/netfx_Core.mzz" &&
    cp "$work"/_003_mscoree_dll_* "$out" || status=$?
  rm -rf "$work"
  return "$status"
}

dotnet48_verify_mscoree() {
  local folder="$1" file="$2"
  [ "$(sha256sum "$file" | cut -d' ' -f1)" = "$(dotnet48_py sha256 "$folder")" ] ||
    dotnet48_die "the mscoree.dll extracted for $folder does not match its sha256"
}

dotnet48_back_up_mscoree() {
  local folder="$1" target="$2" saved="$DOTNET48_BACKUP/mscoree/$1/mscoree.dll"
  [ -e "$target" ] || [ -L "$target" ] || return 0
  [ ! -e "$saved" ] || return 0
  mkdir -p "$(dirname "$saved")"
  cp -a "$target" "$saved"
}

dotnet48_place_mscoree() {
  local folder="$1" source="$2" target="$FSOL_PREFIX/drive_c/windows/$1/mscoree.dll"
  dotnet48_verify_mscoree "$folder" "$source"
  dotnet48_back_up_mscoree "$folder" "$target"
  cp --remove-destination "$source" "$target"
  echo "changed $folder/mscoree.dll is Microsoft's 4.0.31106.0 (original in $DOTNET48_BACKUP/mscoree)"
}

dotnet48_install_mscoree() {
  local archive extracted pair arch
  if dotnet48_py check "$FSOL_PREFIX" mscoree > /dev/null; then
    echo "already Microsoft's mscoree.dll in system32 and syswow64"
    return 0
  fi
  archive="$(dotnet48_download "$DOTNET40_URL" "$DOTNET40_SHA256" "$DOTNET40_FILE")"
  extracted="$(mktemp -d "${TMPDIR:-/tmp}/dotnet48-mscoree-out.XXXXXX")"
  dotnet48_extract_mscoree "$archive" "$extracted" || dotnet48_die "cannot extract mscoree.dll from $archive"
  for pair in $DOTNET48_MSCOREE_FOLDERS; do
    arch="${pair##*:}"
    dotnet48_place_mscoree "${pair%%:*}" "$extracted/_003_mscoree_dll_$arch"
  done
  rm -rf "$extracted"
}

dotnet48_back_up_registry() {
  local entry key view file
  mkdir -p "$DOTNET48_BACKUP/registry"
  for entry in "${DOTNET48_REGISTRY_BACKUPS[@]}"; do
    file="$DOTNET48_BACKUP/registry/$(tr -c 'A-Za-z0-9\n' _ <<< "$entry").reg"
    [ ! -e "$file" ] || continue
    read -r key view <<< "$entry"
    dotnet48_wine "$FSOL_PREFIX" reg export "$key" "$(windows_path "$file")" /y ${view:+"$view"} > /dev/null 2>&1 || true
  done
}

dotnet48_set_registry() {
  local missing
  missing="$(dotnet48_py missing "$FSOL_PREFIX")"
  if [ -z "$missing" ]; then
    echo "already the mscoree overrides, InstallRoot and gac=n in the registry"
    return 0
  fi
  dotnet48_back_up_registry
  dotnet48_run_wine_arguments "$missing"
  echo "changed $(wc -l <<< "$missing") registry values (originals exported to $DOTNET48_BACKUP/registry)"
}

dotnet48_apply() {
  dotnet48_install_runtime
  dotnet48_install_mscoree
  dotnet48_set_registry
}

dotnet48_restore_mscoree() {
  local pair folder saved target restored=0
  for pair in $DOTNET48_MSCOREE_FOLDERS; do
    folder="${pair%%:*}"
    saved="$DOTNET48_BACKUP/mscoree/$folder/mscoree.dll"
    target="$FSOL_PREFIX/drive_c/windows/$folder/mscoree.dll"
    [ -e "$saved" ] || [ -L "$saved" ] || continue
    cp -a --remove-destination "$saved" "$target"
    rm -f "$saved"
    restored=$((restored + 1))
    echo "removed Microsoft's $folder/mscoree.dll, the original is back"
  done
  [ "$restored" -gt 0 ] || echo "already no backed-up mscoree.dll to restore"
}

dotnet48_undo() {
  dotnet48_restore_mscoree
  echo "left .NET Framework 4.8 installed and gac=n in the registry: with 4.8 in the Windows GAC, removing gac=n makes wine-mono programs load Microsoft's WPF and crash"
}
