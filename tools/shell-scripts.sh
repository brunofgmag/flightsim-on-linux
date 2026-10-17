#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/.."
git ls-files -z --cached --others --exclude-standard | while IFS= read -r -d '' file; do
  if [ -f "$file" ] && head -n 1 "$file" | grep -qE '^#!.*[/ ](ba)?sh$'; then
    printf '%s\n' "$file"
  fi
done
