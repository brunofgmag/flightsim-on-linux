#!/usr/bin/env bash
ACTIVE_SKY_PATTERN='^[A-Za-z]:.*\\HiFi\\AS_FS\\AS_FS\.exe'
ACTIVE_SKY_EXE='drive_c/Program Files/HiFi/AS_FS/AS_FS.exe'
ACTIVE_SKY_KEY='Software\Wine\Fonts\Replacements'
REGISTRY_BACKUP="${FSOL_COMPATDATA:+$FSOL_COMPATDATA/flightsim-on-linux-backup/active-sky}"
export ACTIVE_SKY_PATTERN ACTIVE_SKY_EXE ACTIVE_SKY_KEY REGISTRY_BACKUP
