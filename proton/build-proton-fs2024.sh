#!/usr/bin/env bash
set -euo pipefail

VERIFIED_RELEASE="cachyos-11.0-20261005-slr"
RELEASE="${1:-$VERIFIED_RELEASE}"
ARCH="x86_64"
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
CACHE="${FSOL_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/flightsim-on-linux}"
DOWNLOADS="$CACHE/downloads"
WORK="${WORK:-$CACHE/build-$RELEASE-$(date +%Y%m%d-%H%M%S)}"
JOBS="${JOBS:-$(nproc)}"

OVERLAY_TAG="v0.1.0-preview.7"
OVERLAY_VARIANT="cachyos-11"
OVERLAY_PATCH_URL="https://raw.githubusercontent.com/marselnenaj/fenix-a320-linux-patch/$OVERLAY_TAG/patches/proton/$OVERLAY_VARIANT.patch"
OVERLAY_PATCH_SHA256="0992d4c491f37f041490743612b81dd5b2f79ddd618c5c57f91dc03e518373e2"

PATCHES=(
  "$DOWNLOADS/fenix-a320-linux-patch-$OVERLAY_TAG-$OVERLAY_VARIANT.patch"
  "$ROOT/patches/wine/gdiplus-path-gradient-and-centered-text.patch"
  "$ROOT/patches/wine/shell32-notifyicon-guid.patch"
  "$ROOT/patches/wine/win32u-cross-process-child-windows.patch"
  "$ROOT/patches/wine/winewayland-opaque-xrgb.patch"
  "$ROOT/patches/wine/gdiplus-font-family-after-substitution.patch"
  "$ROOT/patches/wine/win32u-cross-process-d3d12-fence.patch"
  "$ROOT/patches/wine/win32u-signaller-stops-at-exit.patch"
  "$ROOT/patches/wine/d2d1-non-finite-geometry.patch"
  "$ROOT/patches/wine/d2d1-free-figure-vertex-types.patch"
  "$ROOT/patches/wine/d2d1-bezier-split-ceiling.patch"
)

MODULES=(
  "server/wineserver:bin/wineserver"
  "dlls/ntdll/ntdll.so:lib/wine/x86_64-unix/ntdll.so"
  "dlls/win32u/win32u.so:lib/wine/x86_64-unix/win32u.so"
  "dlls/winex11.drv/winex11.so:lib/wine/x86_64-unix/winex11.so"
  "dlls/winewayland.drv/winewayland.so:lib/wine/x86_64-unix/winewayland.so"
  "dlls/dwrite/dwrite.so:lib/wine/x86_64-unix/dwrite.so"
  "dlls/ntdll/x86_64-windows/ntdll.dll:lib/wine/x86_64-windows/ntdll.dll"
  "dlls/kernelbase/x86_64-windows/kernelbase.dll:lib/wine/x86_64-windows/kernelbase.dll"
  "dlls/crypt32/x86_64-windows/crypt32.dll:lib/wine/x86_64-windows/crypt32.dll"
  "dlls/d2d1/x86_64-windows/d2d1.dll:lib/wine/x86_64-windows/d2d1.dll"
  "dlls/dwrite/x86_64-windows/dwrite.dll:lib/wine/x86_64-windows/dwrite.dll"
  "dlls/mmdevapi/x86_64-windows/mmdevapi.dll:lib/wine/x86_64-windows/mmdevapi.dll"
  "dlls/gdiplus/x86_64-windows/gdiplus.dll:lib/wine/x86_64-windows/gdiplus.dll"
  "dlls/shell32/x86_64-windows/shell32.dll:lib/wine/x86_64-windows/shell32.dll"
)

CONFIGURE=(--enable-win64 --disable-tests --enable-silent-rules --without-ffmpeg --without-unwind)

USER_SETTINGS=(
  "WINE_TRACK_WRITECOPY=apps:Fenix.exe,FenixSystem.exe,FenixDisplay.exe,FenixCDU.exe,FlightSimulator2024.exe,FlightSimulator.exe,cef_browser.exe"
  "WINE_D2D1_DISPLAY_EFFECTS=FenixDisplay.exe;FenixCDU.exe"
  "WINE_D2D1_GEOMETRY_PROVIDER=FenixDisplay.exe"
  "WINE_DWRITE_UNHINTED_OUTLINES=FenixDisplay.exe;FenixCDU.exe"
  "WINE_FENIX_HELPER_WINDOWS=1"
  "DOTNET_SYSTEM_GLOBALIZATION_USENLS=1"
  "DOTNET_ReadyToRun=0"
  "PROTON_NO_ESYNC=1"
  "PROTON_NO_FSYNC=1"
)

RUNTIME_LIBRARIES=(libgcc_s.so.1)

die() {
  echo "build-proton-fs2024: $*" >&2
  exit 1
}

simulator_running() {
  python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); from fsol.machine import sim_pid; sys.exit(0 if sim_pid() else 1)' "$ROOT"
}

step() {
  echo
  echo "== $*"
}

usage() {
  echo "usage: $(basename "$0") [proton-cachyos release, default $VERIFIED_RELEASE]" >&2
  echo "Environment: STEAM_DIR, DEST, DISPLAY_NAME, PREVIOUS, WORK, JOBS, FSOL_CACHE." >&2
  exit 2
}

explain_cleanup() {
  local status=$?
  [ "$status" -ne 0 ] && [ -n "${CREATED_WORK:-}" ] || return 0
  echo >&2
  echo "Stopped with the build folder left on disk. To start over:" >&2
  echo "  rm -rf '$WORK'" >&2
}

module_source() {
  echo "${1%%:*}"
}

module_target() {
  echo "${1#*:}"
}

find_steam() {
  local candidate
  for candidate in "${STEAM_DIR:-}" "$HOME/.local/share/Steam" "$HOME/.steam/steam" \
    "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam" "$HOME/snap/steam/common/.local/share/Steam"; do
    [ -n "$candidate" ] && [ -f "$candidate/steamapps/libraryfolders.vdf" ] || continue
    readlink -f "$candidate"
    return
  done
}

protocol_version() {
  sed -n 's/^#define SERVER_PROTOCOL_VERSION \([0-9]*\)$/\1/p' "$SOURCE/include/wine/server_protocol.h"
}

check_preconditions() {
  step "Checking the starting point"
  [[ "$RELEASE" == cachyos-11.* ]] || die "only cachyos-11 releases have an overlay variant; $RELEASE does not"
  [ "$RELEASE" = "$VERIFIED_RELEASE" ] || echo "note: $RELEASE is not $VERIFIED_RELEASE, the release this project verified; the patches may not apply"
  local tool
  for tool in curl git patch python3 perl autoreconf make gcc flex bison x86_64-w64-mingw32-gcc x86_64-w64-mingw32-strip strip readelf sha512sum sha256sum tar; do
    command -v "$tool" > /dev/null || die "missing program: $tool"
  done
  local patch
  for patch in "${PATCHES[@]:1}"; do
    [ -f "$patch" ] || die "missing patch $patch"
  done
  STEAM="$(find_steam)"
  [ -n "$STEAM" ] || die "no Steam installation found; set STEAM_DIR to the folder that has steamapps/libraryfolders.vdf"
  echo "Steam: $STEAM"
  DEST="${DEST:-$STEAM/compatibilitytools.d/Proton-FS2024}"
  DISPLAY_NAME="${DISPLAY_NAME:-Proton FS2024}"
  PREVIOUS="${PREVIOUS:-$CACHE/previous/$(basename "$DEST")}"
  ! simulator_running || die "the simulator is running; close it first, because the build ends by swapping the Proton it uses"
  [ ! -e "$WORK" ] || die "$WORK already exists; remove it or set WORK to a new folder"
}

download_release() {
  step "Downloading and checking proton-$RELEASE-$ARCH"
  local base="proton-$RELEASE-$ARCH"
  local url="https://github.com/CachyOS/proton-cachyos/releases/download/$RELEASE"
  TARBALL="$DOWNLOADS/$base.tar.xz"
  mkdir -p "$DOWNLOADS"
  local sums expected listed
  sums="$(curl -fsSL --retry 3 "$url/$base.sha512sum")"
  read -r expected listed <<< "$sums"
  [ "$listed" = "$base.tar.xz" ] || [ "$listed" = "*$base.tar.xz" ] || die "$base.sha512sum lists $listed, not $base.tar.xz"
  [[ "$expected" =~ ^[0-9a-f]{128}$ ]] || die "$base.sha512sum has no sha512"
  if [ ! -f "$TARBALL" ]; then
    curl -fL --retry 3 -o "$TARBALL.part" "$url/$base.tar.xz"
    mv "$TARBALL.part" "$TARBALL"
  fi
  TARBALL_SHA512="$(sha512sum "$TARBALL" | cut -d' ' -f1)"
  [ "$TARBALL_SHA512" = "$expected" ] || die "$TARBALL does not match the release sha512; delete it and run again"
  echo "sha512 ok: $TARBALL"
}

download_overlay_patch() {
  step "Downloading the fenix-a320-linux-patch $OVERLAY_TAG patch for $OVERLAY_VARIANT"
  local target="${PATCHES[0]}"
  if [ ! -f "$target" ]; then
    curl -fsSL --retry 3 -o "$target.part" "$OVERLAY_PATCH_URL"
    mv "$target.part" "$target"
  fi
  [ "$(sha256sum "$target" | cut -d' ' -f1)" = "$OVERLAY_PATCH_SHA256" ] || die "$target does not match the pinned sha256; delete it and run again"
  echo "sha256 ok: $target"
}

find_wine_commit() {
  step "Finding the wine-cachyos commit of the release"
  local answer kind url
  answer="$(curl -fsSL --retry 3 -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/CachyOS/proton-cachyos/contents/wine?ref=$RELEASE")"
  read -r kind WINE_COMMIT url < <(python3 -I -c 'import json, sys; d = json.load(sys.stdin); print(d.get("type", ""), d.get("sha", ""), d.get("submodule_git_url", ""))' <<< "$answer")
  [ "$kind" = submodule ] || die "the wine folder of release $RELEASE is not a submodule"
  [[ "$WINE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "invalid commit: $WINE_COMMIT"
  [[ "$url" =~ ^https://github\.com/CachyOS/wine-cachyos(\.git)?$ ]] || die "the wine submodule points to $url"
  echo "wine-cachyos $WINE_COMMIT"
}

fetch_source() {
  step "Fetching wine-cachyos $WINE_COMMIT"
  mkdir -p "$WORK"
  CREATED_WORK=1
  SOURCE="$WORK/wine"
  git init -q "$SOURCE"
  git -C "$SOURCE" fetch -q --depth=1 https://github.com/CachyOS/wine-cachyos "$WINE_COMMIT"
  git -C "$SOURCE" -c advice.detachedHead=false checkout -q FETCH_HEAD
}

apply_patches() {
  step "Applying the patches"
  cd "$SOURCE"
  ./tools/make_requests
  local before after patch
  before="$(protocol_version)"
  for patch in "${PATCHES[@]}"; do
    echo "-- $(basename "$patch")"
    patch --batch --forward --fuzz=0 -p1 --dry-run -i "$patch" > /dev/null || die "$(basename "$patch") does not apply to $WINE_COMMIT"
    patch --batch --forward --fuzz=0 -p1 -i "$patch"
  done
  ./tools/make_requests
  after="$(protocol_version)"
  [ -n "$before" ] && [ "$before" = "$after" ] || die "the wineserver protocol changed from $before to $after; the release's 32-bit clients would not talk to it"
  PROTOCOL="$after"
  echo "protocol $PROTOCOL"
}

compile_modules() {
  step "Compiling ${#MODULES[@]} modules"
  mkdir -p "$WORK/objects"
  export XDG_CACHE_HOME="$WORK/cache"
  cd "$SOURCE"
  ./tools/make_specfiles
  python3 dlls/winevulkan/make_vulkan --xml "$SOURCE/dlls/winevulkan/vk.xml" --video-xml "$SOURCE/dlls/winevulkan/video.xml"
  autoreconf -f
  local flags="-O2 -ffile-prefix-map=$SOURCE=/usr/src/fenix-proton/wine -ffile-prefix-map=$WORK=/usr/src/fenix-proton/build"
  cd "$WORK/objects"
  "$SOURCE/configure" "${CONFIGURE[@]}" CFLAGS="$flags" CROSSCFLAGS="$flags" > "$WORK/configure.log" 2>&1 || die "configure failed; see $WORK/configure.log"
  make depend > "$WORK/depend.log" 2>&1 || die "make depend failed; see $WORK/depend.log"
  local targets=() module
  for module in "${MODULES[@]}"; do
    targets+=("$(module_source "$module")")
  done
  make -j"$JOBS" "${targets[@]}" > "$WORK/make.log" 2>&1 || die "make failed; see $WORK/make.log"
}

extract_release() {
  step "Extracting the release"
  mkdir "$WORK/release"
  tar -xf "$TARBALL" -C "$WORK/release"
  STAGE="$WORK/release/proton-$RELEASE-$ARCH"
  [ -f "$STAGE/proton" ] && [ -f "$STAGE/toolmanifest.vdf" ] || die "the tarball has no proton-$RELEASE-$ARCH/proton"
}

needed_libraries() {
  readelf -d "$1" | sed -n 's/.*(NEEDED).*Shared library: \[\(.*\)\]/\1/p' | sort -u
}

check_libraries() {
  step "Checking the libraries the ELF modules need"
  local module source target extra
  for module in "${MODULES[@]}"; do
    source="$WORK/objects/$(module_source "$module")"
    target="$STAGE/files/$(module_target "$module")"
    case "$target" in
      *.dll) continue ;;
    esac
    [ -f "$target" ] || die "the release has no $(module_target "$module")"
    extra="$(comm -23 <(needed_libraries "$source") <(needed_libraries "$target") | grep -vxF -f <(printf '%s\n' "${RUNTIME_LIBRARIES[@]}") || true)"
    [ -z "$extra" ] || die "$(module_target "$module") needs $(echo "$extra" | tr '\n' ' ')and the release one does not"
    echo "ok $(module_target "$module")"
  done
}

install_modules() {
  step "Swapping the modules in the release copy"
  local module source target
  for module in "${MODULES[@]}"; do
    source="$WORK/objects/$(module_source "$module")"
    target="$STAGE/files/$(module_target "$module")"
    [ -f "$source" ] || die "make did not produce $(module_source "$module")"
    [ -f "$target" ] && [ ! -L "$target" ] || die "the release has no regular file $(module_target "$module")"
    rm -f "$target"
    install -m 0755 "$source" "$target"
    case "$target" in
      *.dll) x86_64-w64-mingw32-strip --strip-debug "$target" ;;
      *) strip --strip-debug "$target" ;;
    esac
    echo "$(sha256sum "$target" | cut -d' ' -f1)  $(module_target "$module")"
  done
}

write_settings() {
  step "Writing user_settings.py and compatibilitytool.vdf"
  python3 -I - "$STAGE/user_settings.py" "${USER_SETTINGS[@]}" <<'PY'
import json
import sys
target, pairs = sys.argv[1], sys.argv[2:]
settings = dict(pair.split("=", 1) for pair in pairs)
with open(target, "w") as out:
    out.write("user_settings = {\n")
    for key, value in settings.items():
        out.write(f"    {json.dumps(key)}: {json.dumps(value)},\n")
    out.write("}\n")
PY
  local name
  name="$(basename "$DEST")"
  cat > "$STAGE/compatibilitytool.vdf" <<EOF
"compatibilitytools"
{
  "compat_tools"
  {
    "$name"
    {
      "install_path" "."
      "display_name" "$DISPLAY_NAME"
      "from_oslist"  "windows"
      "to_oslist"    "linux"
    }
  }
}
EOF
  echo "$name: $DISPLAY_NAME"
}

find_runtime() {
  local appid="$1" library manifest installdir
  while read -r library; do
    manifest="$library/steamapps/appmanifest_$appid.acf"
    [ -f "$manifest" ] || continue
    installdir="$(sed -n 's/^[[:space:]]*"installdir"[[:space:]]*"\(.*\)"$/\1/p' "$manifest")"
    [ -x "$library/steamapps/common/$installdir/run" ] || continue
    echo "$library/steamapps/common/$installdir"
    return
  done < <(sed -n 's/^[[:space:]]*"path"[[:space:]]*"\(.*\)"$/\1/p' "$STEAM/steamapps/libraryfolders.vdf")
}

check_runtime() {
  local appid runtime prefix output status=0
  appid="$(sed -n 's/^[[:space:]]*"require_tool_appid"[[:space:]]*"\([0-9]*\)"$/\1/p' "$STAGE/toolmanifest.vdf")"
  [ -n "$appid" ] || die "the release toolmanifest.vdf does not name its runtime"
  step "Starting a Windows process inside runtime $appid"
  runtime="$(find_runtime "$appid")"
  [ -n "$runtime" ] || die "Steam does not have runtime $appid installed; install it from the Steam library (Tools) and run again"
  echo "runtime: $runtime"
  prefix="$WORK/container-prefix"
  output="$(env -u DISPLAY -u WAYLAND_DISPLAY WINEPREFIX="$prefix" WINEDEBUG=-all WINEDLLOVERRIDES="winemenubuilder.exe=d;mscoree,mshtml=" \
    timeout 300 "$runtime/run" -- "$STAGE/files/bin/wine" cmd.exe /c echo "container start OK" 2>&1)" || status=$?
  env -u DISPLAY -u WAYLAND_DISPLAY WINEPREFIX="$prefix" timeout 60 "$runtime/run" -- "$STAGE/files/bin/wineserver" -k > /dev/null 2>&1 || true
  echo "$output" | tail -n 5
  if [ "$status" -ne 0 ] || ! grep -q "container start OK" <<< "$output"; then
    die "the rebuilt Proton cannot start a process inside $(basename "$runtime")"
  fi
}

write_record() {
  local record="$STAGE/fs2024-build.txt" patch module
  {
    echo "release $RELEASE $ARCH"
    echo "sha512 $TARBALL_SHA512"
    echo "wine-cachyos $WINE_COMMIT"
    echo "protocol $PROTOCOL"
    echo "overlay fenix-a320-linux-patch $OVERLAY_TAG $OVERLAY_VARIANT"
    for patch in "${PATCHES[@]}"; do
      echo "patch $(sha256sum "$patch" | cut -d' ' -f1) $(basename "$patch")"
    done
    for module in "${MODULES[@]}"; do
      echo "module $(sha256sum "$STAGE/files/$(module_target "$module")" | cut -d' ' -f1) $(module_target "$module")"
    done
  } > "$record"
}

run_checks() {
  step "Running the checks on the built Proton"
  "$ROOT/checks/check-proton.sh" "$STAGE" || die "a check failed; the built Proton is in $STAGE and $DEST did not change"
}

publish() {
  step "Installing into $DEST"
  ! simulator_running || die "the simulator started during the build; the built Proton is in $STAGE and $DEST did not change"
  local first=1
  if [ -e "$DEST" ]; then
    first=
    rm -rf "$PREVIOUS"
    mkdir -p "$(dirname "$PREVIOUS")"
    mv "$DEST" "$PREVIOUS"
    echo "previous build: $PREVIOUS"
  fi
  mkdir -p "$(dirname "$DEST")"
  mv "$STAGE" "$DEST"
  echo "done: $DEST"
  if [ -n "$first" ]; then
    echo "Restart Steam, then pick \"$DISPLAY_NAME\" in Microsoft Flight Simulator 2024 > Properties > Compatibility."
  else
    echo "Steam keeps using \"$DISPLAY_NAME\"; the next start of the simulator runs the new build."
    echo "To go back, close the simulator and swap $DEST with $PREVIOUS."
  fi
}

case "${1:-}" in
  -h|--help) usage ;;
esac
[ $# -le 1 ] || usage

trap explain_cleanup EXIT

check_preconditions
download_release
download_overlay_patch
find_wine_commit
fetch_source
apply_patches
compile_modules
extract_release
check_libraries
install_modules
write_settings
check_runtime
write_record
run_checks
publish
