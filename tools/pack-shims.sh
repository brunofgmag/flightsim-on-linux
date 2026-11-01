#!/usr/bin/env bash
set -euo pipefail

[ $# -eq 1 ] && [ -d "$1" ] || { echo "usage: $(basename "$0") <shims folder>" >&2; exit 2; }
OUT="$(cd "$1" && pwd)"

for folder in "$OUT"/*/; do
  [ -d "$folder" ] || continue
  name="$(basename "$folder")"
  tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -C "$OUT" -cf - "$name" | gzip -n > "$OUT/$name.tar.gz"
  rm -rf "${folder:?}"
done
rm -f "$OUT/SHA256SUMS"
mapfile -t files < <(cd "$OUT" && find . -maxdepth 1 -type f -printf '%P\n' | sort)
if [ "${#files[@]}" -gt 0 ]; then
  (cd "$OUT" && sha256sum "${files[@]}" > SHA256SUMS)
fi
