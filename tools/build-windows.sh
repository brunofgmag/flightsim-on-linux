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
"$ROOT/tools/pack-shims.sh" "$OUT/shims"
find "$OUT" -type f | sort
