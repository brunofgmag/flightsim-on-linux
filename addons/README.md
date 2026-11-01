# Add-on fixes

Each folder here is one add-on. The folder name is its id, in lower case with dashes, and the CLI and the GUI list the add-on under that id.

A folder holds `addon.json` and three executable scripts: `apply`, `check` and `undo`. It may also hold `install`, `launch` and `close`, and any other script that a menu entry or a watcher names. `msfs-autofps` is the complete example: copy it for a new add-on.

`addon.json` has these fields:

- `name`: what the reader sees.
- `tested_version`: the add-on version the fix was measured with.
- `tested_on`: the date of that measurement, like `2026-10-17`.
- `needs`: any of `proton-fs2024`, `simulator-closed` and `simulator-running`. `apply` and `undo` won't run until these hold.
- `touches_addon_files`: `true` when the fix changes the add-on's own files. The CLI says so before it runs the fix, and `undo` has to restore those files.
- `installed`: where to look for the add-on, as lists of relative globs under `prefix`, `base` (the folder that holds `UserCfg.opt`), `community`, `sim` (the simulator's install folder) or `data` (`${XDG_DATA_HOME:-~/.local/share}/flightsim-on-linux`, where a program with a prefix of its own lives). `command` lists programs looked up in `PATH`, for a native Linux program. The add-on counts as installed when any glob matches.
- `install` (optional): where the program comes from, as `{"from": "...", "paid": true, false or null, "kind": "..."}`. `from` is a page or a store, never a guessed download link. `paid` is `null` when nobody knows. `kind` is `installer` (a Windows installer you download), `store` (a store app that downloads the installer for you), `native` (a Linux program, or a single file the `install` script fetches and places itself) or `manual` (steps nobody can script).
- `shortcuts` (optional): application menu entries, as a list of objects. Each has a `title`, and may have `script` (default `launch`), `args` (a list), `comment` (English, default built from the title), `icon` (`{"place": "prefix", "glob": "..."}`, same places as `installed`; the icon is taken from the first `.exe` that matches), `close` (the name of a script, offered as the menu action "Close <title>") and `mime` (a list of MIME types the entry opens, for links such as `x-scheme-handler/navigraph-traffic-desktop`).
- `watcher` (optional): a background service that starts the program with the simulator, or, with `loop`, runs one script of the add-on for as long as it lives. See "Watchers" below.

`validate` refuses an `addon.json` that names a script that is not executable, a script name that is not a plain file name in the add-on's folder, an unknown field, or a wrong type.

## Scripts

The scripts run from their own folder. They get the machine in `FSOL_PREFIX`, `FSOL_COMPATDATA`, `FSOL_PROTON`, `FSOL_BASE`, `FSOL_PACKAGES`, `FSOL_COMMUNITY`, `FSOL_SIM_INSTALL`, `FSOL_STEAM` and `FSOL_SIM_RUNNING` (`1` or `0`). `FSOL_CLI` holds the path to `bin/flightsim-on-linux` for `"$FSOL_CLI" run`, and `FSOL_CACHE` the folder where downloads are kept (`$FSOL_CACHE/downloads`): the value of `FSOL_CACHE` when it is set, else `${XDG_CACHE_HOME:-~/.cache}/flightsim-on-linux`, the same folder `proton build` uses. A script that downloads a file checks its sha256 and keeps it there, so the next run does not download it again. A script must not hard-code a home folder or a Steam library path, and must not set `WAYLAND_DISPLAY`, `DISPLAY` or `XAUTHORITY`: anything that runs next to the simulator goes through `"$FSOL_CLI" run`, which copies them from the simulator's process. A program that cannot start in the simulator's container reads what it needs with `"$FSOL_CLI" sim-env NAME...` instead, which prints a `NAME=value` line for each variable the simulator's process has, and exits 2 when the simulator is not running.

`check` exits 0 when the fix works and with any other code when it doesn't. It prints one line for each thing it looked at, starting with `ok` or `FAIL`. It has to fail without the fix: revert the fix, run `check`, watch it fail, then apply the fix again.

`install`, `launch`, `close` and the scripts that menu entries and watchers name run without `NoDefaultCurrentDirectoryInExePath` in their environment: Claude Code sets it, and Wine installers fail with it. `install` also gets `FSOL_INSTALLER`, the absolute path of the file the reader passed to `addon install`.

## Commands

- `flightsim-on-linux addon apply <id>` runs `apply`, then `check`, and remembers the day if `check` passes. `addon check` forgets that day when the check fails later.
- `addon install <id> [file]`. Without a file it prints where to get the program, whether it is paid, what kind of installer it is, and the command to run next. With a file it runs `install` with `FSOL_INSTALLER` set, then prints `addon apply <id>` as the next step. `"own_prefix": true` in `install` says the installer works in a prefix of its own, so a busy simulator prefix does not stop it. It refuses while the simulator runs or any program is open in the simulator's prefix, and names those programs: installers run alone. A `native` add-on with an `install` script needs no file and runs at once.
- `addon launch <id> [script] [-- args]` runs one script of the add-on, by default `launch`, with the machine in `FSOL_*` and the terminal inherited. Menu entries call this. Give the script name before `--`.
- `addon shortcut <id>... [--remove]` writes the menu entries to `${XDG_DATA_HOME:-~/.local/share}/applications/flightsim-on-linux-<id>.desktop` (a second entry gets `-2`), the icon to `icons/hicolor/256x256/apps`, and refreshes the desktop database. No `7z`, no `magick`, no matching program or an `.AppImage` means no icon: the entry is written anyway and one line says why. `--remove` deletes only the files that carry this add-on's marker.
- `addon watcher <id>... [--off]` writes `flightsim-on-linux-<id>.service` to `${XDG_CONFIG_HOME:-~/.config}/systemd/user` and runs `systemctl --user enable --now` on it. `--off` disables it and deletes the file.
- `addon watch <id>` is the watcher loop in the foreground. The service runs it. It refuses an add-on whose watcher is a `loop`, and says so with exit code 2.
- `addon list` marks an add-on `[install]` when it has an `install` script, `[menu]` when its menu entries are written and `[watcher]` when its service file is there. `addon list --json` carries `can_install`, `shortcut` and `watcher` as booleans.

An unknown add-on, or one without shortcuts or a watcher, gets a clean message and exit code 2.

## Watchers

A watcher is data in `addon.json` plus the add-on's own `launch` script, run by one loop for every add-on. It exists because some programs cannot start from `exe.xml`: a child of the simulator inherits the simulator's environment, and some programs only open with settings that the environment overrides. The loop looks at `/proc` every `poll` seconds.

- `app` (required): a regular expression matched with `re.search` against each process's command line, arguments joined by spaces. Anchor it on the Windows path, like `^C:.*\\Program\.exe`. The loop never matches its own process.
- `script` (default `launch`) and `args`: what to run to start the program.
- `trigger`: `simulator` (default), `none` (never start it; the watcher only stops it or restarts it) or `{"aircraft": regex}` (start when the simulator loads an aircraft whose name matches, case ignored).
- `grace` (default 20) and `wait_for` (`{"process": regex, "timeout": seconds}`): with the `simulator` trigger, start only `grace` seconds after a new simulator process appears, then, when `wait_for` is set, only once a process matching `process` exists; after `timeout` it starts anyway and says so.
- `poll` (default 8): seconds between looks.
- `stop`: `never` (default), `with_simulator` (stop what runs when the simulator closes), `with_simulator_if_started` (the same, but only a program the watcher started for that simulator) or `on_other_aircraft` (stop it when another aircraft loads, and when the simulator closes if the watcher started it). Stopping runs the add-on's `close` script when it has one, else sends SIGTERM to the processes `app` matches. A watcher never sends SIGKILL to a program it stops (only its own script, if that hangs for 120 seconds): killing some programs in flight leaves the simulator in a changed state.
- `health`: `{"listening": port}` starts the program only when nothing listens on that port yet, or `{"connected_to": port, "lost_for": seconds, "restart": script}` runs the add-on's `restart` script when the program is running, the simulator listens on the port and the program has no connection to it for `lost_for` seconds.

- `loop`: the name of a script of the add-on, instead of every option above. The watcher is then a service that runs that script for as long as it lives, with `addon launch <id> <script>`, and not the loop that looks at `/proc`. It takes no other option, and `addon watch` refuses it. The unit has `Restart=on-failure` and the default `KillMode`, so stopping it reaches the script, which can trap SIGTERM to put things back.

The rules the loop keeps:

- With the `simulator` trigger the program is started once per simulator process, and never when `app` already matches. A program closed by hand stays closed until the next simulator.
- A simulator that dies during the grace or the wait starts nothing. A simulator replaced between two looks stops the old program (when `stop` says so) and gets its own start.
- With an `aircraft` trigger the program starts on each matching aircraft when `app` does not match, and the grace does not apply.
- `health.restart` runs at most three times per simulator process, `lost_for` seconds apart.
- It logs at most one line for each change of state, never one per look.
- The aircraft comes from `tools/aircraft-loaded.mjs`, run with Node.js and the `node-simconnect` that `simconnect-tcp` installs in `$FSOL_CACHE/simconnect`. If Node.js or the module is missing, the loop says so once and tries again every minute. A reader that dies is started again after 8 seconds.

The service uses `Restart=on-failure`, `WantedBy=default.target` and `KillMode=process`, so stopping the service leaves the program running whatever `stop` says. A `loop` service has no `KillMode=process`. A program dying from a signal can leave the simulator in a bad state: AutoFPS killed in flight leaves the terrain detail lowered.

## Menu entries

`Exec` is the absolute path of the CLI, quoted, then `addon launch <id> <script>`, then `--` and the entry's `args`, then `%u` when it has `mime`. The entry has `Categories=Game;`, `Terminal=false`, an English `Comment`, and `Actions=close;` when `close` is set. The icon comes from `7z x <exe> '.rsrc/*/ICON/*' '.rsrc/ICON/*'`, the largest file, and `magick -resize '256x256>'`.

## Helpers for the scripts

Source `tools/addon-launch.sh` from `launch`, `install` and `close`. It gives:

- `addon_say <title> <message>`: prints to stderr, and sends a desktop notice when no terminal is attached.
- `addon_need_simulator`: says to start the simulator first and exits 1 when it is closed.
- `addon_refuse_second <regex>`: says the program is already running and exits 0 when `pgrep -f` finds it.
- `addon_beside_simulator [NAME=value ...] <windows path> [args]`: runs the program in the simulator's container with `"$FSOL_CLI" run` when the simulator runs, else in the game's prefix. Put `EXTRA_WINEDLLOVERRIDES=d3d9=d` before the call to turn a DLL off for that program.
- `addon_alone [NAME=value ...] <file> [args]`: runs an installer in the game's prefix with Proton's Wine, waits for the prefix to be free, and returns the installer's exit code. It is what `install` scripts use.
- `addon_windows_path <path>`: `C:\...` for a path inside the prefix's `drive_c`, else `Z:\...`.
- `addon_log_file <id>`: creates `$FSOL_CACHE/logs/<id>.log`, readable only by the user, and prints its path. Write the program's output there, and never print a command line that holds a token.
- `addon_wait_gone <regex> [seconds]`: waits up to `seconds` (default `$FSOL_CLOSE_WAIT`, else 10) for the program to end.
- `addon_newest <glob>`: prints the match with the highest version (`sort -V`), or nothing, and never fails. Quote the glob.
- `addon_close_by_pattern <name> <regex>`: the `close` script of a program that ends on SIGTERM. It says `<name> is not running` when `pgrep -f` finds nothing, else sends SIGTERM, waits with `addon_wait_gone` and says so, exit 1, when the program stays.
- `addon_prefix_free_or_say <name>`: for a program that has to run alone. It exits 1 with a notice that names the programs open in the simulator's prefix, from `"$FSOL_CLI" machine --json`, and carries on when the prefix is free or the report cannot be read.
- `addon_stash <file> <backup folder>` and `addon_unstash <file> <backup folder>`: keep the original of a file under `<backup folder>/<file name>` before a fix changes it, or a `.absent` mark when it was not there, and put it back (or delete what the fix put there) on undo. The first stash wins, so apply can run twice.

`tools/registry.sh` sets, reads and restores a string value under `HKCU` of the game's prefix: `registry_get <key> <name>`, `registry_set <key> <name> <value>` (keeps the old value in `$REGISTRY_BACKUP` first, which the add-on sets) and `registry_restore <key> <name>`. `tools/community-packages.sh` has `community_packages_check <why a link is gone> <reinstall hint> <install hint> <package>...`, the `check` of an aircraft that a store puts in Community. `tools/exe-entry.py` looks for an `exe.xml` entry by the end of its Windows path (`state`, `check`), and `fsol/exe_xml.py` and `fsol/simconnect.py` have the helpers behind it and behind the `SimConnect.cfg` of clients that run in a prefix of their own.

`tools/addons-prefix.sh` ports the sibling prefix that some programs need to stay out of the simulator's wineserver. `addon_sibling_prefix` prints its folder, `${XDG_DATA_HOME:-~/.local/share}/flightsim-on-linux/addons-prefix`. `addon_sibling_prefix_create` copies the registry of the game's prefix once (set `FSOL_ADDONS_PREFIX_RESET=1` to copy it again), blocks Wine's update check, and links `drive_c` and the drive letters to the game's prefix; it refuses while a wineserver holds either prefix. `addon_sibling_prefix_wine <program> [args]` runs Proton's Wine in it with the X11 driver and the DXVK DLLs forced on. Choosing a GPU is left to the add-on.

`tools/shim.sh` builds small Windows programs that an add-on needs. `shim_path <name> [file]` prints `$FSOL_CACHE/shims/<file>` (default `<name>.dll`), running `shims/<name>/build.sh <output folder>` first when the file is missing or older than the files in `shims/<name>`. A shim folder holds `build.sh`, which names the missing compiler's package in one line, and its source. `tools/build-windows.sh` builds every shim too, with a `SHA256SUMS` file for releases.

## Base add-ons

`simconnect-tcp` makes the simulator listen for SimConnect clients on TCP. The simulator's `SimConnect.xml` declares a static IPv4 port that is 500 by default, and Linux does not let the simulator open ports below 1024. `apply` moves it to 5111, keeps the original under `$FSOL_COMPATDATA/flightsim-on-linux-backup/simconnect-tcp`, and installs `node-simconnect` 4.2.1 into `$FSOL_CACHE/simconnect` when npm exists. `check` reads the port and fails below 1024. Programs in other prefixes, native Linux programs and the aircraft trigger of a watcher all reach the simulator through it. `fsol/simconnect.py` has `port(found)` for scripts and watchers that need the number.

`dotnet48` puts Microsoft's .NET Framework 4.8 and its `mscoree.dll` in the simulator's prefix, with the four registry values that go with them (`mscoree` builtin, the two `InstallRoot` values and `gac=n` for wine-mono). It sets no override for any one program: an add-on that needs a program on Microsoft's .NET (`fenix-a320` for five of its programs, `maddog-md80` for MDClient, `sayintentions` for `si_telemetry.exe`) tells the reader to run `addon apply dotnet48` and sets `mscoree=native` for its own program. `fenix-a320` sources `addons/dotnet48/parts/` and runs `dotnet48_apply` itself. The prefix's Windows 11 build makes the 4.8 installer exit 0 and install nothing, so `apply` saves the version keys, switches to Windows 7 while it installs and puts the keys back. Backups go to `$FSOL_COMPATDATA/flightsim-on-linux-backup/dotnet48`, or stay in `fenix-a320` on a machine that already holds them there. `undo` gives the original `mscoree.dll` back and leaves 4.8 and the registry values.
