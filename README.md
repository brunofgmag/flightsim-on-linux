# Flightsim on Linux

Guides and tools for running Windows add-ons for Microsoft Flight Simulator 2024 (Steam) under Proton on Linux: Fenix, FSLabs, PMDG, GSX, ChasePlane, SayIntentions, Active Sky and about twenty more.

The site is at https://brunofgmag.github.io/flightsim-on-linux/. Start there if you just want your add-ons working.

## What is in here

| Path | What it is |
|---|---|
| `bin/flightsim-on-linux` | Command-line helper: `machine`, `doctor`, `proton build`, `proton check`, `dxvk build`, `dxvk check`, `run`, `addon list`, `addon install`, `addon apply`, `addon check`, `addon undo`, `addon launch`, `addon shortcut`, `addon watcher`, `addon watch`, `sim-env` |
| `bin/flightsim-on-linux-gui` | The main commands in zenity or kdialog dialogs: fix add-ons, install one, add menu shortcuts, start add-ons with the simulator, check and build Proton |
| `proton/build-proton-fs2024.sh` | Builds Proton FS2024 from a Proton-CachyOS release, checks it and swaps it in |
| `dxvk/build-dxvk-fenix.sh` | Builds the DXVK the Fenix displays need in GPU mode, checks it and keeps it in the cache |
| `checks/` | Tests a Proton or that DXVK in a throwaway prefix |
| `addons/` | One folder per add-on: scripts to apply, check and undo its fix, plus install, launch and close scripts, menu entries and a watcher where the add-on has them. `addons/README.md` has the format |
| `shims/` | Sources of small Windows programs that some add-ons need. Your machine builds them |
| `tools/` | Helper scripts for the add-on scripts, the shim builder and `build_site.py` |
| `patches/wine/` | The Wine patches this project adds |
| `patches/dxvk/` | The DXVK patches the Fenix displays need |
| `patches/vkd3d-proton/` | The vkd3d-proton patches the Glass Cockpit DLSS Fix needs |
| `site/` | Source of the website; `tools/build_site.py` writes it to `docs/` |
| `AGENTS.md` | Instructions for AI coding agents working on a user's machine |

## Quick start

```
git clone https://github.com/brunofgmag/flightsim-on-linux.git ~/flightsim-on-linux
~/flightsim-on-linux/bin/flightsim-on-linux doctor
~/flightsim-on-linux/bin/flightsim-on-linux proton build
~/flightsim-on-linux/bin/flightsim-on-linux proton check
```

`doctor` needs only Python 3. The build needs git, curl, a C toolchain with autoconf, flex, bison, perl, the MinGW-w64 C and C++ cross compilers, meson, ninja, glslang and widl; `doctor` lists what is missing. See [Start here](https://brunofgmag.github.io/flightsim-on-linux/start.html) for the full walk-through.

Once Proton FS2024 is in place, each add-on goes in with `addon install <id> <installer>` and `addon apply <id>`. `addon shortcut <id>` adds its menu entry, and `addon watcher <id>` starts it with the simulator. The site has a page on [installing add-ons](https://brunofgmag.github.io/flightsim-on-linux/installers.html), one on [shortcuts and watchers](https://brunofgmag.github.io/flightsim-on-linux/shortcuts-and-watchers.html) and one for each add-on.

## Working on it

```
python3 -m unittest discover -s tests -t .
python3 tools/build_site.py
python3 -m http.server -d docs 8000
```

The last command serves the site at http://127.0.0.1:8000/.

Fixes were found and measured on one machine: CachyOS, KDE Plasma on Wayland, an NVIDIA RTX 4080, October 2026. If you try it on another machine, open an issue with the output of `bin/flightsim-on-linux doctor`, after removing licence keys and account names.

## Credits

Proton FS2024 is built on the [fenix-a320-linux-patch](https://github.com/marselnenaj/fenix-a320-linux-patch) overlay by its authors, on [Proton-CachyOS](https://github.com/CachyOS/proton-cachyos), on [Wine](https://www.winehq.org/) and on [vkd3d-proton](https://github.com/HansKristian-Work/vkd3d-proton). This project downloads them from their own releases and doesn't redistribute them.

Not affiliated with Microsoft, Valve, CodeWeavers, the Wine project or any add-on developer.

## Licences

- Code (`bin/`, `fsol/`, `proton/`, `checks/`, `addons/`, `shims/`, `tools/`, `tests/`): MIT, in `LICENSE`.
- Text of the site and the guides (`site/`, `docs/`, the Markdown files): CC BY 4.0, in `LICENSE-docs`.
- Wine patches (`patches/wine/`): LGPL-2.1-or-later, like Wine itself, in `patches/wine/COPYING`.
- vkd3d-proton patches (`patches/vkd3d-proton/`): LGPL-2.1, like vkd3d-proton itself, in `patches/vkd3d-proton/COPYING`.
- OpenDeck patches (`addons/opendeck/patches/`): GPL-3.0, like OpenDeck itself, in `addons/opendeck/patches/COPYING`.
