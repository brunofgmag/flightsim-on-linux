# Instructions for AI agents

You are helping someone run Windows add-ons for Microsoft Flight Simulator 2024 (Steam app 2537590) under Proton on Linux, on their own machine, with this repository checked out. The person may not know Wine. Work in small steps, say what each command will change before you run it, and check every fix you apply.

## 1. Learn the machine before you suggest anything

Run these first and read the output. Don't guess paths: Steam libraries, the prefix and the Community folder differ on every machine.

```
bin/flightsim-on-linux machine --json
bin/flightsim-on-linux doctor --json
```

`machine` reports the Steam folder, every library, where the simulator is installed, its compatdata and prefix, the Proton it last ran with (the third line of `compatdata/2537590/config_info`), the compatibility tool Steam has selected, the launch options per Steam user, the packages and Community folders (from `InstalledPackagesPath` in `UserCfg.opt`) and whether the simulator is running. `doctor` turns that into ok, warn, fail or info lines, each with a fix.

Add-on fixes live in `addons/<id>/`, in the format `addons/README.md` describes. `bin/flightsim-on-linux addon list --json` says which add-ons are installed and fixed, and `addon check <id>` proves a fix still works.

Also worth knowing, read-only:

- Proton in use: `cat "$(bin/flightsim-on-linux machine --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["proton"])')/user_settings.py"`.
- Which Wine display driver the simulator uses: Wayland if the launch options have `PROTON_ENABLE_WAYLAND=1`, X11 otherwise. It changes how add-on windows and tray icons behave.
- Simulator log of loaded Community packages, after it reaches the menu: `grep -o -E 'communityfolderfs2[04]="[0-9/]+"' "<base folder>/AsoboReport-RunningSession.txt"`, where the base folder holds `UserCfg.opt`.

## 2. Rules that keep the person's setup safe

- Fix the environment, not the add-on: Proton, the prefix registry, DLL overrides, launch options, environment variables, small stand-in DLLs. Analysing an add-on to understand a failure is fine. Editing or replacing an add-on's own files is a last resort: say so plainly first, back up the original, and give the command that restores it.
- Never touch licensing or activation. Never run the FSDT universal installer again on a machine where GSX is already activated; it spends an activation.
- Never read out, print or send licence keys, account tokens or logs that contain them. The FSLabs `CCLog.txt` stores the activation key in plain text. If a log is needed, read only the lines you need and redact keys.
- Installers and updaters run with the simulator and every other program of the prefix closed, one at a time. Programs that talk to the simulator are started inside the simulator's own container with `bin/flightsim-on-linux run <program.exe>`, not with a fresh `protontricks-launch` while it runs.
- Don't force-quit the simulator during its loading screen: it can switch off the whole Community folder (see `site/pages/rules.html`).
- In scripts, `pgrep -f` and `pkill -f` also match the shell that runs them. Write patterns like `'Fenix[.]exe'`.

## 3. How to diagnose a failing add-on

Go from cheap to expensive, and name a cause only after an observation that tells it apart from the alternatives.

1. Reproduce once, with a timestamped trace from the very start of the program, not from the click that fails:
   `WINEDEBUG=+timestamp,+pid,+seh,+loaddll protontricks-launch --no-term --appid 2537590 <program.exe> 2> trace.log`
2. For .NET Framework programs running on wine-mono, add `WINE_MONO_TRACE=E:all` and read the first exception that isn't routine. On the test machine, the FSDT updater whose Install button "did nothing" had already failed at startup, with a `NotImplementedException` from wine-mono, long before the click.
3. For windows that never appear or appear empty, compare the X11 and Wayland drivers, and try `WINEDLLOVERRIDES=d3d9=d` for WPF programs.
4. For Electron and Chromium-based apps, try `--disable-gpu-sandbox`, then `--disable-gpu`.
5. If the simulator itself freezes in the menu or while loading an aircraft, check ntsync before anything else. `doctor` reports it; with the simulator running it counts the `/dev/ntsync` handles of the simulator's wineserver, which opens the device only when `PROTON_NO_NTSYNC` is unset or zero. The remedy is `PROTON_NO_NTSYNC=1` before `%command%` in the launch options. `PROTON_USE_NTSYNC=0` is ignored.
6. Watch a process for about 30 seconds, sampling at 10, 20 and 30 s (alive, CPU, memory, windows). Extend only if something is still changing.

## 4. How to prove a fix

A fix is proven when its check passes with the fix and fails without it. Run the check, revert the fix, run the check again and see it fail, then restore the fix. A check that stays green either way proves nothing.

For Proton Fenix A320 the check already exists: `bin/flightsim-on-linux proton check [<Proton folder>]` runs four tests in a throwaway prefix. A stock Proton-CachyOS fails all four. A tray check that reports "skipped" means no X11 display was available, not a failure.

## 5. Where the knowledge lives

- `site/pages/*.html`: the guides, the ground rules and the add-on list, written for people.
- `proton/build-proton-fenix.sh`: builds Proton Fenix A320 from a Proton-CachyOS release, the fenix-a320-linux-patch overlay patch and the two patches in `patches/wine/`.
- `checks/`: the Proton checks and their C sources.
- `fsol/`: the discovery and doctor code behind the CLI.
