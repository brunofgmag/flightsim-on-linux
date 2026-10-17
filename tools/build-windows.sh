#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
[ $# -eq 1 ] || { echo "usage: $(basename "$0") <output folder>" >&2; exit 2; }
OUT="$(mkdir -p "$1" && cd "$1" && pwd)"

"$ROOT/checks/build-checks.sh" "$OUT/checks"
mkdir -p "$OUT/shims"
for build in "$ROOT"/shims/*/build.sh; do
  [ -x "$build" ] || continue
  "$build" "$OUT/shims"
done
mapfile -t shims < <(cd "$OUT/shims" && find . -maxdepth 1 -type f ! -name SHA256SUMS -printf '%P\n' | sort)
if [ "${#shims[@]}" -gt 0 ]; then
  (cd "$OUT/shims" && sha256sum "${shims[@]}" > SHA256SUMS)
fi
find "$OUT" -type f | sort
