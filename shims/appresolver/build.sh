#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

build_with_dotnet() {
  local work="$1"
  cp "$HERE/AppResolverShim.cs" "$HERE/appresolver.csproj" "$work/"
  dotnet build "$work/appresolver.csproj" -c Release -o "$work/out" -nologo -v quiet > "$work/build.log" 2>&1 || { cat "$work/build.log" >&2; return 1; }
  cp "$work/out/AppResolverShim.dll" "$work/AppResolverShim.dll"
}

wine_mono_compiler() {
  local compiler
  compiler="$(find "${FSOL_PROTON:-/nonexistent}/files/share/wine/mono" -path '*/lib/mono/4.5/csc.exe' 2> /dev/null | sort -V | tail -n 1)"
  [ -n "$compiler" ] || return 1
  echo "$compiler"
}

build_with_wine_mono() {
  local work="$1" compiler
  compiler="$(wine_mono_compiler)" || return 1
  [ -n "${FSOL_PREFIX:-}" ] || return 1
  # shellcheck source=tools/proton-runner.sh
  . "$ROOT/tools/proton-runner.sh"
  cp "$HERE/AppResolverShim.cs" "$work/"
  addon_prefix_run "$FSOL_PREFIX" wine "$(windows_path "$compiler")" /nologo /target:library /optimize+ "/out:$(windows_path "$work/AppResolverShim.dll")" "$(windows_path "$work/AppResolverShim.cs")" > "$work/build.log" 2>&1 || { cat "$work/build.log" >&2; return 1; }
  [ -f "$work/AppResolverShim.dll" ]
}

[ $# -eq 1 ] || { echo "usage: $(basename "$0") <output folder>" >&2; exit 2; }
mkdir -p "$1"
work="$(mktemp -d "${TMPDIR:-/tmp}/appresolver-build.XXXXXX")"
trap 'rm -rf "$work"' EXIT

if command -v dotnet > /dev/null; then
  build_with_dotnet "$work"
elif ! build_with_wine_mono "$work"; then
  echo "cannot build AppResolverShim.dll: install the .NET SDK (dotnet-sdk on Arch, dotnet-sdk-8.0 on Debian and Ubuntu), or run this from an add-on script so Proton's wine-mono compiler can run in the game's prefix" >&2
  exit 1
fi
cp "$work/AppResolverShim.dll" "$1/AppResolverShim.dll"
