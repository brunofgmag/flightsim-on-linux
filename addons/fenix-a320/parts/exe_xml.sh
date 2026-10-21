#!/usr/bin/env bash
FENIX_BOOTSTRAPPER='C:\Program Files\FenixSim A320\deps\FenixBootstrapper.exe'
FENIX_ENTRY_NAME="FenixA320"
FENIX_EXE_XML_MARK="$FENIX_BACKUP/exe-xml-entry"

exe_xml_py() {
  local command="$1"
  shift
  python3 -I "$FSOL_ROOT/fsol/exe_xml.py" "$command" "$@" "$FSOL_BASE/exe.xml" "$FENIX_BOOTSTRAPPER"
}

exe_xml_status() {
  local errors status=0
  errors="$(mktemp)"
  EXE_XML_STATUS="$(exe_xml_py status 2> "$errors")" || status=$?
  EXE_XML_ERROR="$(tr '\n' ' ' < "$errors" | sed 's/ *$//')"
  rm -f "$errors"
  if [ "$status" -eq 0 ] && [ -n "$EXE_XML_STATUS" ]; then
    return 0
  fi
  EXE_XML_ERROR="${EXE_XML_ERROR:-exe_xml.py status exited with $status and printed nothing}"
  return 1
}

exe_xml_check() {
  exe_xml_status || fail "exe.xml cannot be read: $EXE_XML_ERROR" || return 1
  case "$EXE_XML_STATUS" in
    enabled) ok "exe.xml starts the Fenix bootstrapper" ;;
    disabled) fail "exe.xml has the Fenix bootstrapper entry with Disabled true" ;;
    absent) fail "exe.xml has no entry for the Fenix bootstrapper" ;;
    no-file) fail "exe.xml does not exist in $FSOL_BASE" ;;
    *) fail "exe.xml cannot be read: exe_xml.py status printed $EXE_XML_STATUS" ;;
  esac
}

exe_xml_apply() {
  local result
  exe_xml_status || die "exe.xml cannot be read: $EXE_XML_ERROR"
  case "$EXE_XML_STATUS" in
    enabled) echo "already the Fenix bootstrapper entry in exe.xml" ;;
    disabled) echo "already the Fenix bootstrapper entry in exe.xml, disabled; left as it is" ;;
    *)
      mkdir -p "$FENIX_BACKUP"
      [ ! -f "$FSOL_BASE/exe.xml" ] || cp -p "$FSOL_BASE/exe.xml" "$FENIX_BACKUP/exe.xml"
      result="$(exe_xml_py add --name "$FENIX_ENTRY_NAME")"
      echo "$result" > "$FENIX_EXE_XML_MARK"
      echo "changed exe.xml: $result the Fenix bootstrapper entry (previous file in $FENIX_BACKUP/exe.xml)"
      ;;
  esac
}

exe_xml_undo() {
  local added
  if [ ! -f "$FENIX_EXE_XML_MARK" ]; then
    echo "already no entry of ours in exe.xml; an entry the Fenix installer wrote stays"
    return 0
  fi
  added="$(cat "$FENIX_EXE_XML_MARK")"
  if [ "$added" = created ]; then
    exe_xml_py remove --delete-if-empty > /dev/null
  else
    exe_xml_py remove > /dev/null
  fi
  rm -f "$FENIX_EXE_XML_MARK"
  echo "removed the Fenix bootstrapper entry that apply added to exe.xml"
}
