#!/usr/bin/env bash
FENIX_PROTON_PATCH="windows-storage-applicationdata-no-package.patch"
FENIX_PROTON_MODULE="lib/wine/x86_64-windows/windows.storage.applicationdata.dll"

proton_check() {
  python3 -I "$FSOL_ROOT/fsol/proton_build.py" check "${FSOL_PROTON:-}" "$FENIX_PROTON_PATCH" "$FENIX_PROTON_MODULE" \
    "Proton's windows.storage.applicationdata.dll lets the Fenix Installer open its package database"
}
