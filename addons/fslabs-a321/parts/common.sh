#!/usr/bin/env bash
FSL_PARTS="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

die() {
  echo "fslabs-a321: $*" >&2
  exit 2
}

fsl_exe_xml_report() {
  local state
  state="$(python3 -I "$FSL_PARTS/fslabs.py" control-center "$FSOL_BASE")" || die "exe.xml in $FSOL_BASE cannot be read"
  case "$state" in
    enabled) echo "already exe.xml starts the Control Center" ;;
    disabled) echo "left exe.xml as it is: its Control Center entry has Disabled true" ;;
    absent | no-file) echo "left exe.xml as it is: it has no Control Center entry; start the Control Center, which writes its own" ;;
    *) die "fslabs.py control-center printed $state" ;;
  esac
}
