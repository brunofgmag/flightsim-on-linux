#!/usr/bin/env bash
VOLANTA_PATTERN='^[A-Za-z]:.*\\Volanta\\Volanta\.exe'
export VOLANTA_PATTERN

volanta_exe() {
  compgen -G "$FSOL_PREFIX/drive_c/users/*/AppData/Local/Programs/Volanta/Volanta.exe" | head -n 1 || true
}
