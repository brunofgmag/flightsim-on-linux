# Add-on fixes

Each folder here is one add-on. The folder name is its id, in lower case with dashes, and the CLI and the GUI list the add-on under that id.

A folder holds `addon.json` and three executable scripts: `apply`, `check` and `undo`.

`addon.json` has these fields:

- `name`: what the reader sees.
- `tested_version`: the add-on version the fix was measured with.
- `tested_on`: the date of that measurement, like `2026-10-17`.
- `needs`: any of `proton-fs2024`, `simulator-closed` and `simulator-running`. `apply` and `undo` won't run until these hold.
- `touches_addon_files`: `true` when the fix changes the add-on's own files. The CLI says so before it runs the fix, and `undo` has to restore those files.
- `installed`: where to look for the add-on, as lists of relative globs under `prefix`, `base` (the folder that holds `UserCfg.opt`), `community` or `sim` (the simulator's install folder). The add-on counts as installed when any glob matches.

The scripts run from their own folder. They get the machine in `FSOL_PREFIX`, `FSOL_COMPATDATA`, `FSOL_PROTON`, `FSOL_BASE`, `FSOL_PACKAGES`, `FSOL_COMMUNITY`, `FSOL_SIM_INSTALL`, `FSOL_STEAM` and `FSOL_SIM_RUNNING` (`1` or `0`), and `FSOL_CLI` holds the path to `bin/flightsim-on-linux` for `"$FSOL_CLI" run`. A script must not hard-code a home folder or a Steam library path, and must not set `WAYLAND_DISPLAY`, `DISPLAY` or `XAUTHORITY`: anything that runs next to the simulator goes through `"$FSOL_CLI" run`, which copies them from the simulator's process.

`check` exits 0 when the fix works and with any other code when it doesn't. It prints one line for each thing it looked at, starting with `ok` or `FAIL`. It has to fail without the fix: revert the fix, run `check`, watch it fail, then apply the fix again.

`flightsim-on-linux addon apply <id>` runs `apply`, then `check`, and remembers the day if `check` passes. `addon check` forgets that day when the check fails later.
