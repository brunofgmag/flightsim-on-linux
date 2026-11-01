#!/usr/bin/env bash
gsx_curl_zip() {
  local folder="$FSOL_CACHE/downloads" target
  target="$folder/$(basename "$GSX_CURL_URL")"
  mkdir -p "$folder"
  if [ ! -f "$target" ]; then
    curl -fL -sS --retry 3 -o "$target.part" "$GSX_CURL_URL" || gsx_die "cannot download $GSX_CURL_URL"
    mv "$target.part" "$target"
  fi
  [ "$(sha256sum "$target" | cut -d' ' -f1)" = "$GSX_CURL_SHA256" ] || gsx_die "$target does not match the sha256 $GSX_CURL_SHA256; delete it and run again"
  echo "$target"
}

gsx_curl_apply() {
  local zip work folder name changed=0 files
  read -ra files <<< "$GSX_CURL_FILES"
  zip="$(gsx_curl_zip)"
  work="$(mktemp -d "${TMPDIR:-/tmp}/gsx-curl.XXXXXX")"
  python3 -I "$GSX_PARTS/curl_zip.py" "$zip" "$work" "${files[@]}" || { rm -rf "$work"; gsx_die "$zip has no bin/curl.exe with its certificates"; }
  for folder in $GSX_CURL_FOLDERS; do
    for name in $GSX_CURL_FILES; do
      if ! cmp -s "$work/$name" "$FSOL_PREFIX/drive_c/windows/$folder/$name"; then
        addon_stash "$FSOL_PREFIX/drive_c/windows/$folder/$name" "$GSX_BACKUP/curl/$folder"
        cp "$work/$name" "$FSOL_PREFIX/drive_c/windows/$folder/$name"
        changed=1
      fi
    done
  done
  rm -rf "$work"
  if [ "$changed" = 1 ]; then
    echo "changed curl.exe and curl-ca-bundle.crt in system32 and syswow64 ($(basename "$GSX_CURL_URL"))"
  else
    echo "already curl.exe and curl-ca-bundle.crt in system32 and syswow64"
  fi
}

gsx_curl_undo() {
  local folder name
  for folder in $GSX_CURL_FOLDERS; do
    for name in $GSX_CURL_FILES; do
      addon_unstash "$FSOL_PREFIX/drive_c/windows/$folder/$name" "$GSX_BACKUP/curl/$folder"
    done
  done
  echo "restored system32 and syswow64 without our curl.exe"
}
