#!/usr/bin/env bash
set -euo pipefail

DXVK_URL="https://github.com/doitsujin/dxvk.git"
DXVK_COMMIT="685301564ea3486a7098bd37194020da84299617"
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
CACHE="${FSOL_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/flightsim-on-linux}"
DOWNLOADS="$CACHE/downloads"
CHECKOUT="$DOWNLOADS/dxvk"
WORK="${WORK:-$CACHE/build-dxvk-$(date +%Y%m%d-%H%M%S)}"
DEST="$CACHE/dxvk-fenix"
PREVIOUS="$CACHE/previous/dxvk-fenix"
JOBS="${JOBS:-$(nproc)}"
MESON_MINIMUM="0.58"

PATCHES=(
  "$ROOT/patches/dxvk/dxgi-get-shared-resource-adapter-luid.patch"
  "$ROOT/patches/dxvk/d3d11-keyed-mutex-release-flush.patch"
  "$ROOT/patches/dxvk/keyed-mutex-timeout.patch"
)

TARGETS=(src/dxgi/dxgi.dll src/d3d11/d3d11.dll)

die() {
  echo "build-dxvk-fenix: $*" >&2
  exit 1
}

step() {
  echo
  echo "== $*"
}

usage() {
  echo "usage: $(basename "$0")" >&2
  echo "Environment: WORK, JOBS, FSOL_CACHE." >&2
  exit 2
}

explain_cleanup() {
  local status=$?
  [ "$status" -ne 0 ] && [ -n "${CREATED_WORK:-}" ] || return 0
  echo >&2
  echo "Stopped with the build folder left on disk. To start over:" >&2
  echo "  rm -rf '$WORK'" >&2
}

check_preconditions() {
  step "Checking the starting point"
  local tool patch version
  for tool in git patch meson ninja glslangValidator x86_64-w64-mingw32-gcc x86_64-w64-mingw32-g++ sha256sum; do
    command -v "$tool" > /dev/null || die "missing program: $tool"
  done
  version="$(meson --version)"
  [ "$(printf '%s\n%s\n' "$MESON_MINIMUM" "$version" | sort -V | head -n 1)" = "$MESON_MINIMUM" ] || die "meson $version is older than $MESON_MINIMUM"
  for patch in "${PATCHES[@]}"; do
    [ -f "$patch" ] || die "missing patch $patch"
  done
  [ ! -e "$WORK" ] || die "$WORK already exists; remove it or set WORK to a new folder"
}

fetch_source() {
  step "Fetching DXVK $DXVK_COMMIT"
  mkdir -p "$DOWNLOADS"
  [ -d "$CHECKOUT/.git" ] || git clone -q "$DXVK_URL" "$CHECKOUT"
  git -C "$CHECKOUT" cat-file -e "$DXVK_COMMIT^{commit}" 2> /dev/null || git -C "$CHECKOUT" fetch -q origin
  git -C "$CHECKOUT" -c advice.detachedHead=false checkout -q --force "$DXVK_COMMIT"
  git -C "$CHECKOUT" submodule update -q --init --recursive
  git -C "$CHECKOUT" clean -q -ffdx
  [ "$(git -C "$CHECKOUT" rev-parse HEAD)" = "$DXVK_COMMIT" ] || die "$CHECKOUT is not at $DXVK_COMMIT"
  mkdir -p "$WORK"
  CREATED_WORK=1
  SOURCE="$WORK/dxvk"
  cp -a "$CHECKOUT" "$SOURCE"
  echo "dxvk $DXVK_COMMIT"
}

apply_patches() {
  step "Applying the patches"
  local patch
  cd "$SOURCE"
  for patch in "${PATCHES[@]}"; do
    echo "-- $(basename "$patch")"
    patch --batch --forward --fuzz=0 -p1 --dry-run -i "$patch" > /dev/null || die "$(basename "$patch") does not apply to $DXVK_COMMIT"
    patch --batch --forward --fuzz=0 -p1 -i "$patch"
  done
}

compile() {
  step "Compiling ${TARGETS[*]}"
  cd "$SOURCE"
  meson setup --cross-file build-win64.txt --buildtype release --strip "$WORK/objects" > "$WORK/meson.log" 2>&1 || die "meson setup failed; see $WORK/meson.log"
  ninja -C "$WORK/objects" -j"$JOBS" "${TARGETS[@]}" > "$WORK/ninja.log" 2>&1 || die "ninja failed; see $WORK/ninja.log"
}

write_stage() {
  step "Staging the build"
  local target
  STAGE="$WORK/stage"
  mkdir "$STAGE"
  for target in "${TARGETS[@]}"; do
    [ -f "$WORK/objects/$target" ] || die "ninja did not produce $target"
    install -m 0755 "$WORK/objects/$target" "$STAGE/$(basename "$target")"
  done
  write_record
  sha256sum "$STAGE"/*.dll
}

write_record() {
  local patch
  {
    echo "dxvk $DXVK_COMMIT"
    for patch in "${PATCHES[@]}"; do
      echo "patch $(sha256sum "$patch" | cut -d' ' -f1) $(basename "$patch")"
    done
  } > "$STAGE/BUILD-INFO"
}

run_checks() {
  step "Running the checks on the built DXVK"
  "$ROOT/checks/check-dxvk.sh" "$STAGE" | tee "$WORK/checks.log" || die "a check failed; the built DXVK is in $STAGE and $DEST did not change"
  grep -q '^All [0-9]* checks passed[.]$' "$WORK/checks.log" || die "some checks could not run here, so the build is unproven; the built DXVK is in $STAGE and $DEST did not change"
}

publish() {
  step "Installing into $DEST"
  if [ -e "$DEST" ]; then
    rm -rf "$PREVIOUS"
    mkdir -p "$(dirname "$PREVIOUS")"
    mv "$DEST" "$PREVIOUS"
    echo "previous build: $PREVIOUS"
  fi
  mkdir -p "$(dirname "$DEST")"
  mv "$STAGE" "$DEST"
  rm -rf "$WORK"
  CREATED_WORK=
  echo "done: $DEST"
}

case "${1:-}" in
  -h|--help) usage ;;
esac
[ $# -eq 0 ] || usage

trap explain_cleanup EXIT

check_preconditions
fetch_source
apply_patches
compile
write_stage
run_checks
publish
