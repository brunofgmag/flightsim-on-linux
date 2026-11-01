#!/usr/bin/env bash
AAO_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
FSOL_ROOT="$(cd "$AAO_PARTS/../../.." && pwd)"
AAO_BACKUP="${FSOL_COMPATDATA:?FSOL_COMPATDATA is not set}/flightsim-on-linux-backup/axis-and-ohs"
AAO_ASM_OVERRIDE_ADDED="$AAO_BACKUP/asm-override-added"
export AAO_PARTS FSOL_ROOT AAO_BACKUP AAO_ASM_OVERRIDE_ADDED

# shellcheck source=tools/addon-launch.sh
. "$FSOL_ROOT/tools/addon-launch.sh"
# shellcheck source=tools/registry.sh
. "$FSOL_ROOT/tools/registry.sh"
# shellcheck source=tools/addons-prefix.sh
. "$FSOL_ROOT/tools/addons-prefix.sh"
# shellcheck source=tools/shim.sh
. "$FSOL_ROOT/tools/shim.sh"
# shellcheck source=addons/gsx-pro/parts/mono-copy.sh
. "$FSOL_ROOT/addons/gsx-pro/parts/mono-copy.sh"

aao_die() {
  echo "axis-and-ohs: $*" >&2
  exit 2
}
