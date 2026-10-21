#!/usr/bin/env bash
FENIX_PROTON_FIXES=(
  windows-storage-applicationdata-no-package.patch lib/wine/x86_64-windows/windows.storage.applicationdata.dll
  "Proton's windows.storage.applicationdata.dll lets the Fenix Installer open its package database"
  wintrust-create-indirect-data.patch lib/wine/x86_64-windows/wintrust.dll
  "Proton's wintrust.dll lets the Fenix 3 runner check its own signature"
  ntdll-globalroot-device-paths.patch lib/wine/x86_64-unix/ntdll.so
  "Proton's ntdll.so opens the device paths the Fenix 3 runner starts its programs from"
  ntdll-device-path-current-directory.patch lib/wine/x86_64-windows/ntdll.dll
  "Proton's ntdll.dll keeps the device path FenixSystem 3 starts in as its current folder"
  bcrypt-pbkdf2-pseudo-handle.patch lib/wine/x86_64-windows/bcrypt.dll
  "Proton's bcrypt.dll gives FenixSystem 3 the PBKDF2 handle its authorization uses"
  ncrypt-ecdh-secret-agreement.patch lib/wine/x86_64-windows/ncrypt.dll
  "Proton's ncrypt.dll lets FenixSystem 3 and the runner agree on a session key"
  ntdll-fenix-gateway-loader-case.patch lib/wine/x86_64-windows/ntdll.dll
  "Proton's ntdll.dll gives the Fenix 3 EFB gateway the Windows spelling of KERNEL32.DLL"
)

proton_check() {
  python3 -I "$FSOL_ROOT/fsol/proton_build.py" check "${FSOL_PROTON:-}" "${FENIX_PROTON_FIXES[@]}"
}
