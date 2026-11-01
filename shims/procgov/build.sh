#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
[ $# -eq 1 ] || { echo "usage: $(basename "$0") <output folder>" >&2; exit 2; }
command -v go > /dev/null || { echo "go is missing: install go (Arch), golang-go (Debian, Ubuntu) or golang (Fedora)" >&2; exit 1; }
OUT="$(mkdir -p "$1" && cd "$1" && pwd)"
cd "$HERE"
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o "$OUT/procgov.exe" .
