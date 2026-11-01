#!/usr/bin/env bash
RESHADE_FILES=(dxgi.dll ReShade.ini ReShadePreset.ini)
RESHADE_CACHE_PATH='C:\users\steamuser\AppData\Local\Temp\ReShade'

reshade_sim() {
  echo "${FSOL_SIM_INSTALL:?no simulator install folder found; run: flightsim-on-linux machine}"
}

reshade_backup() {
  echo "${FSOL_COMPATDATA:?FSOL_COMPATDATA is not set}/flightsim-on-linux-backup/reshade"
}

reshade_source() {
  local given="$1"
  if [ -d "$given" ]; then
    echo "$given"
  else
    dirname "$given"
  fi
}

reshade_write_ini() {
  local source="$1" target="$2" value="${RESHADE_CACHE_PATH//\\/\\\\}"
  sed -E "s/^IntermediateCachePath=[^\r]*(\r?)\$/IntermediateCachePath=$value\1/" "$source" > "$target"
}

reshade_install() {
  local source sim backup file
  source="$(reshade_source "${1:?no ReShade folder given}")"
  sim="$(reshade_sim)"
  for file in "${RESHADE_FILES[@]}"; do
    [ -f "$source/$file" ] || { echo "$source/$file does not exist: give the dxgi.dll of a Windows ReShade install, with ReShade.ini and ReShadePreset.ini beside it" >&2; return 1; }
  done
  [ "${FSOL_SIM_RUNNING:-0}" != 1 ] || { echo "close the simulator before you install ReShade" >&2; return 1; }
  if [ -e "$sim/dxgi.dll" ] && ! cmp -s "$source/dxgi.dll" "$sim/dxgi.dll"; then
    echo "$sim/dxgi.dll already exists and is not this ReShade; move it away first" >&2
    return 1
  fi
  backup="$(reshade_backup)"
  mkdir -p "$backup"
  [ ! -f "$sim/ReShade.ini" ] || [ -e "$backup/ReShade.ini.previous" ] || cp -p "$sim/ReShade.ini" "$backup/ReShade.ini.previous"
  cp "$source/dxgi.dll" "$sim/dxgi.dll"
  reshade_write_ini "$source/ReShade.ini" "$sim/ReShade.ini"
  if [ -e "$sim/ReShadePreset.ini" ]; then
    echo "ReShadePreset.ini in the simulator folder stays: ReShade saves its own there"
  else
    cp "$source/ReShadePreset.ini" "$sim/ReShadePreset.ini"
  fi
  echo "dxgi.dll, ReShade.ini and ReShadePreset.ini are in $sim; no shaders were copied"
}

reshade_check() {
  local sim ini status=0 path
  sim="$(reshade_sim)" || return 1
  if [ -f "$sim/dxgi.dll" ] && grep -qa ReShade "$sim/dxgi.dll"; then
    echo "ok dxgi.dll in the simulator folder is ReShade's"
  else
    echo "FAIL dxgi.dll in the simulator folder is missing or is not ReShade's; run: flightsim-on-linux addon install reshade <dxgi.dll of a Windows ReShade install>"
    status=1
  fi
  ini="$sim/ReShade.ini"
  if [ ! -f "$ini" ]; then
    echo "FAIL ReShade.ini is missing from the simulator folder"
    return 1
  fi
  path="$(sed -n 's/^IntermediateCachePath=\(.*[^[:space:]]\)[[:space:]]*$/\1/p' "$ini" | head -n 1)"
  if [ -z "$path" ] || [ "$path" = "$RESHADE_CACHE_PATH" ]; then
    echo "ok ReShade.ini keeps its shader cache inside the prefix"
  else
    echo "FAIL IntermediateCachePath in ReShade.ini is $path, a folder from the Windows install; run the install again"
    status=1
  fi
  if [ -f "$sim/ReShade.log" ] && grep -qa "loaded from" "$sim/ReShade.log"; then
    echo "ok ReShade.log says ReShade loaded as dxgi.dll"
  fi
  return "$status"
}

reshade_free_name() {
  local folder="$1" name="$2" stamp candidate count=0
  stamp="$(date +%Y%m%d-%H%M%S)"
  candidate="$name.$stamp"
  while [ -e "$folder/$candidate" ]; do
    count=$((count + 1))
    candidate="$name.$stamp-$count"
  done
  echo "$candidate"
}

reshade_undo() {
  local sim root backup file kept previous
  sim="$(reshade_sim)"
  root="$(reshade_backup)"
  backup="$root/removed"
  previous="$root/ReShade.ini.previous"
  mkdir -p "$backup"
  for file in "${RESHADE_FILES[@]}"; do
    if [ -f "$sim/$file" ]; then
      kept="$(reshade_free_name "$backup" "$file")"
      mv "$sim/$file" "$backup/$kept"
      echo "$file moved to $backup/$kept"
    fi
  done
  if [ -f "$previous" ]; then
    mv "$previous" "$sim/ReShade.ini"
    echo "the ReShade.ini that was in the simulator folder before the install is back"
  fi
}
