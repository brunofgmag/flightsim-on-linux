#!/usr/bin/env bash
ACTIVE_SKY_PATTERN='^[A-Za-z]:.*\\HiFi\\AS_FS\\AS_FS\.exe'
ACTIVE_SKY_EXE='drive_c/Program Files/HiFi/AS_FS/AS_FS.exe'
ACTIVE_SKY_UTILS_PATTERN='^[A-Za-z]:.*\\AddonFolder\\ActiveSkyUtils\.exe'
ACTIVE_SKY_UTILS_GLOB='drive_c/users/*/AppData/Roaming/HiFi/AS_FS/AddonFolder/ActiveSkyUtils.exe'
ACTIVE_SKY_UTILS_ENTRY='\AddonFolder\ActiveSkyUtils.exe'
ACTIVE_SKY_KEY='Software\Wine\Fonts\Replacements'
REGISTRY_BACKUP="${FSOL_COMPATDATA:+$FSOL_COMPATDATA/flightsim-on-linux-backup/active-sky}"
ACTIVE_SKY_EXE_XML="${FSOL_BASE:+$FSOL_BASE/exe.xml}"
ACTIVE_SKY_UTILS_MARK="$REGISTRY_BACKUP/utils-entry-disabled"
export ACTIVE_SKY_PATTERN ACTIVE_SKY_EXE ACTIVE_SKY_UTILS_PATTERN ACTIVE_SKY_UTILS_GLOB ACTIVE_SKY_UTILS_ENTRY ACTIVE_SKY_KEY REGISTRY_BACKUP ACTIVE_SKY_EXE_XML ACTIVE_SKY_UTILS_MARK

active_sky_utils_cfg() {
  local exe
  exe="$(compgen -G "${FSOL_PREFIX:-}/$ACTIVE_SKY_UTILS_GLOB" | head -n 1 || true)"
  [ -z "$exe" ] || echo "$(dirname "$exe")/SimConnect.cfg"
}
