#!/usr/bin/env bash
MONO_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
MONO_COPY_RELATIVE="drive_c/windows/mono/mono-2.0"
MONO_MSCORLIB="lib/mono/4.5/mscorlib.dll"
MONO_SYSTEM="lib/mono/gac/System/4.0.0.0__b77a5c561934e089/System.dll"
MONO_WINDOWSBASE="lib/mono/gac/WindowsBase/4.0.0.0__31bf3856ad364e35/WindowsBase.dll"
MONO_UNPATCHED_MARKER="lib/mono/4.5/System.Core.dll"
export MONO_PARTS MONO_COPY_RELATIVE MONO_MSCORLIB MONO_SYSTEM MONO_WINDOWSBASE MONO_UNPATCHED_MARKER

mono_fail() {
  echo "wine-mono copy: $*" >&2
  exit 2
}

mono_copy_dir() {
  echo "${FSOL_PREFIX:?FSOL_PREFIX is not set}/$MONO_COPY_RELATIVE"
}

mono_record_dir() {
  echo "${FSOL_COMPATDATA:?FSOL_COMPATDATA is not set}/flightsim-on-linux-backup/mono-copy"
}

mono_source_dir() {
  find "${FSOL_PROTON:?FSOL_PROTON is not set}/files/share/wine/mono" -maxdepth 1 -type d -name 'wine-mono-*' 2> /dev/null | sort -V | tail -n 1
}

mono_sha256() {
  sha256sum "$1" 2> /dev/null | cut -d' ' -f1
}

mono_copy_is_stale() {
  local source copy="$1"
  source="$(mono_source_dir)"
  [ -n "$source" ] && [ -f "$copy/$MONO_UNPATCHED_MARKER" ] && [ "$(mono_sha256 "$source/$MONO_UNPATCHED_MARKER")" != "$(mono_sha256 "$copy/$MONO_UNPATCHED_MARKER")" ]
}

mono_copy_users() {
  local record
  record="$(mono_record_dir)/users"
  [ -d "$record" ] && find "$record" -maxdepth 1 -type f -printf '%f\n' | sort | paste -sd' '
  return 0
}

mono_copy_make() {
  local source copy record
  source="$(mono_source_dir)"
  [ -n "$source" ] || mono_fail "Proton has no wine-mono folder under $FSOL_PROTON/files/share/wine/mono"
  copy="$(mono_copy_dir)"
  record="$(mono_record_dir)"
  rm -rf "$copy"
  mkdir -p "$copy" "$record/users"
  cp -a "$source/." "$copy/"
  basename "$source" > "$record/created"
  echo "copied $(basename "$source") into the prefix at $copy"
}

mono_copy_ensure() {
  local id="$1" copy others
  copy="$(mono_copy_dir)"
  if [ ! -f "$copy/$MONO_MSCORLIB" ]; then
    mono_copy_make
  elif mono_copy_is_stale "$copy"; then
    others="$(mono_copy_users)"
    echo "the wine-mono copy in the prefix is not the one Proton ships now; copying again${others:+ (the patches of $others are lost with it; run apply for each)}"
    mono_copy_make
  else
    echo "already a wine-mono copy in the prefix"
  fi
  mkdir -p "$(mono_record_dir)/users"
  : > "$(mono_record_dir)/users/$id"
}

mono_backup_original() {
  local id="$1" relative="$2" copy backup
  copy="$(mono_copy_dir)/$relative"
  backup="${FSOL_COMPATDATA:?FSOL_COMPATDATA is not set}/flightsim-on-linux-backup/$id/mono/$relative"
  mkdir -p "$(dirname "$backup")"
  cp -p "$copy" "$backup"
}

mono_restore_original() {
  local id="$1" relative="$2" copy backup
  copy="$(mono_copy_dir)/$relative"
  backup="${FSOL_COMPATDATA:?FSOL_COMPATDATA is not set}/flightsim-on-linux-backup/$id/mono/$relative"
  if [ -f "$backup" ] && [ -d "$(dirname "$copy")" ]; then
    cp -p "$backup" "$copy.fsol-new"
    mv -f "$copy.fsol-new" "$copy"
    echo "restored $relative"
  fi
  rm -f "$backup"
}

mono_patch_state() {
  python3 -I "$MONO_PARTS/clr.py" state "$(mono_copy_dir)/$1" "$2"
}

mono_patch_apply() {
  local id="$1" relative="$2" spec="$3" state
  state="$(mono_patch_state "$relative" "$spec")" || mono_fail "cannot read $relative"
  case "$state" in
    patched) echo "already $spec in $relative" ;;
    original)
      mono_backup_original "$id" "$relative"
      python3 -I "$MONO_PARTS/clr.py" apply "$(mono_copy_dir)/$relative" "$spec" > /dev/null || mono_fail "cannot patch $relative"
      echo "changed $relative: $spec"
      ;;
    *) mono_fail "$relative has neither the original nor the patched $spec; not touching it" ;;
  esac
}

mono_patch_undo() {
  local id="$1" relative="$2" spec="$3"
  if [ -f "$(mono_copy_dir)/$relative" ] && [ "$(mono_patch_state "$relative" "$spec" 2> /dev/null)" = patched ]; then
    mono_restore_original "$id" "$relative"
  else
    rm -f "${FSOL_COMPATDATA:?}/flightsim-on-linux-backup/$id/mono/$relative"
    echo "already no $spec in $relative"
  fi
}

mono_copy_release() {
  local id="$1" record
  record="$(mono_record_dir)"
  rm -f "$record/users/$id"
  if [ -f "$record/created" ] && [ -z "$(mono_copy_users)" ]; then
    rm -rf "$(mono_copy_dir)" "$record"
    rmdir "$(dirname "$(mono_copy_dir)")" 2> /dev/null || true
    echo "removed the wine-mono copy this tool made: nothing needs it any more"
  else
    echo "left the wine-mono copy in the prefix: another add-on still uses it, or it was not made by this tool"
  fi
}
