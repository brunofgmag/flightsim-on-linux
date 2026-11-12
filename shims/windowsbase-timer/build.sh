#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
[ $# -eq 1 ] || { echo "usage: $(basename "$0") <output folder>" >&2; exit 2; }
command -v dotnet > /dev/null || { echo "dotnet is missing: install the .NET SDK, version 8 or newer (dotnet-sdk on Arch, dotnet-sdk-8.0 on Debian and Ubuntu); it restores Mono.Cecil from NuGet while it builds" >&2; exit 1; }
framework="net$(dotnet --version | cut -d. -f1).0"
work="$(mktemp -d "${TMPDIR:-/tmp}/windowsbase-timer-build.XXXXXX")"
trap 'rm -rf "$work"' EXIT
cp "$HERE/Program.cs" "$HERE/windowsbase-timer.csproj" "$work/"
dotnet restore "$work/windowsbase-timer.csproj" -p:TargetFramework="$framework" -v quiet > "$work/build.log" 2>&1 || { cat "$work/build.log" >&2; exit 1; }
dotnet build "$work/windowsbase-timer.csproj" --no-restore -c Release -o "$work/out" -p:TargetFramework="$framework" -nologo -v quiet >> "$work/build.log" 2>&1 || { cat "$work/build.log" >&2; exit 1; }
rm -rf "${1:?}/windowsbase-timer"
mkdir -p "$1/windowsbase-timer"
cp "$work"/out/* "$1/windowsbase-timer/"
