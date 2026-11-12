#!/usr/bin/env bash
mfg_sha256() {
  sha256sum "$1" | cut -d ' ' -f 1
}

mfg_fetch() {
  local url="$1" sha="$2" file="$3"
  if [ -f "$file" ] && [ "$(mfg_sha256 "$file")" = "$sha" ]; then
    echo "already downloaded: $file"
    return 0
  fi
  mkdir -p "$(dirname "$file")"
  curl --fail --location --silent --show-error --output "$file.part" "$url"
  if [ "$(mfg_sha256 "$file.part")" != "$sha" ]; then
    rm -f "$file.part"
    echo "the download from $url is not the file this add-on was tested with (sha256 $sha); nothing was installed" >&2
    return 1
  fi
  mv "$file.part" "$file"
}

mfg_require() {
  local file="$1" sha="$2"
  if [ ! -f "$file" ]; then
    echo "cannot read $file; nothing was installed" >&2
    return 1
  fi
  if [ "$(mfg_sha256 "$file")" != "$sha" ]; then
    echo "$file is not the release this add-on was tested with (sha256 $sha); nothing was installed" >&2
    return 1
  fi
}
