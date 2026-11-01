#!/usr/bin/env bash
SI_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
FSOL_ROOT="$(cd "$SI_PARTS/../../.." && pwd)"
SI_BACKUP="${FSOL_COMPATDATA:?FSOL_COMPATDATA is not set}/flightsim-on-linux-backup/sayintentions"
SI_APP="${FSOL_PREFIX:?FSOL_PREFIX is not set}/drive_c/Program Files/SayIntentions.AI/SayIntentionsAI"
SI_HUB="${FSOL_PREFIX}/drive_c/Program Files/SayIntentions.AI/SayIntentionsUpdater3.exe"
SI_HUB_PATTERN='^[A-Za-z]:.*\\SayIntentionsUpdater3\.exe'
SI_PROGRAM_PATTERN='^[A-Za-z]:.*\\SayIntentions(Updater3|AI)\.exe'
SI_SIMULATOR_PATTERN='^[A-Za-z]:.*FlightSimulator2024[.]exe'
export SI_PARTS FSOL_ROOT SI_BACKUP SI_APP SI_HUB SI_HUB_PATTERN SI_PROGRAM_PATTERN SI_SIMULATOR_PATTERN

# shellcheck source=tools/addon-launch.sh
. "$FSOL_ROOT/tools/addon-launch.sh"

si_die() {
  echo "sayintentions: $*" >&2
  exit 2
}
