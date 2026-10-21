#!/usr/bin/env bash
FSL_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
FSOL_ROOT="$(cd "$FSL_PARTS/../../.." && pwd)"
FSL_BACKUP="$FSOL_COMPATDATA/flightsim-on-linux-backup/fslabs-a321"
FSL_WPF_MARK="$FSL_BACKUP/wpf-before"
FSL_WPF_KEY='HKCU\Software\Microsoft\Avalon.Graphics'
FSL_WPF_VALUE=DisableHWAcceleration
export FSL_PARTS FSOL_ROOT FSL_BACKUP

# shellcheck source=/dev/null
. "$FSOL_ROOT/tools/proton-runner.sh"

die() {
  echo "fslabs-a321: $*" >&2
  exit 2
}

fsl_py() {
  python3 -I "$FSL_PARTS/fslabs.py" "$@"
}

fsl_wpf() {
  fsl_py wpf "$FSOL_PREFIX"
}

fsl_reg() {
  addon_prefix_run "$FSOL_PREFIX" wine reg "$@" > /dev/null
  addon_prefix_run "$FSOL_PREFIX" wineserver -k > /dev/null 2>&1 || true
}

fsl_wpf_set() {
  fsl_reg add "$FSL_WPF_KEY" /v "$FSL_WPF_VALUE" /t REG_DWORD /d "$1" /f
}

fsl_wpf_apply() {
  local before
  before="$(fsl_wpf)"
  if [ "$before" = 1 ]; then
    echo "already WPF programs draw in software"
    return 0
  fi
  [[ "$before" =~ ^(absent|[0-9]+)$ ]] || die "$FSL_WPF_VALUE in $FSL_WPF_KEY holds $before, which is not a number; set it to the DWORD 1 yourself"
  mkdir -p "$FSL_BACKUP"
  [ -f "$FSL_WPF_MARK" ] || echo "$before" > "$FSL_WPF_MARK"
  fsl_wpf_set 1
  [ "$(fsl_wpf)" = 1 ] || die "$FSL_WPF_VALUE is still not 1 in $FSL_WPF_KEY after reg add"
  echo "changed WPF programs draw in software: $FSL_WPF_VALUE is 1 (it was $before)"
}

fsl_wpf_undo() {
  local before
  if [ ! -f "$FSL_WPF_MARK" ]; then
    echo "already $FSL_WPF_VALUE was not set by this add-on"
    return 0
  fi
  before="$(cat "$FSL_WPF_MARK")"
  if [ "$before" = absent ]; then
    fsl_reg delete "$FSL_WPF_KEY" /v "$FSL_WPF_VALUE" /f
  else
    fsl_wpf_set "$before"
  fi
  [ "$(fsl_wpf)" = "$before" ] || die "$FSL_WPF_VALUE did not go back to $before; the value before apply is in $FSL_WPF_MARK"
  rm -f "$FSL_WPF_MARK"
  echo "removed $FSL_WPF_VALUE set by apply; it is $before again"
}

fsl_exe_xml_report() {
  local state
  state="$(fsl_py control-center "$FSOL_BASE")" || die "exe.xml in $FSOL_BASE cannot be read"
  case "$state" in
    enabled) echo "already exe.xml starts the Control Center" ;;
    disabled) echo "left exe.xml as it is: its Control Center entry has Disabled true" ;;
    absent | no-file) echo "left exe.xml as it is: it has no Control Center entry; start the Control Center, which writes its own" ;;
    *) die "fslabs.py control-center printed $state" ;;
  esac
}
