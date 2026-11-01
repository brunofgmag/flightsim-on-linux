#!/usr/bin/env bash
POLICY_KEY='Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments'
POLICY_NAME='weathercreator.exe'
POLICY_VALUE='--in-process-gpu --disable-gpu --disable-gpu-compositing --disable-software-rasterizer'
REGISTRY_BACKUP="${FSOL_COMPATDATA:+$FSOL_COMPATDATA/flightsim-on-linux-backup/rex-atmos-core}"
export POLICY_KEY POLICY_NAME POLICY_VALUE REGISTRY_BACKUP
