# Flightsim on Linux

Guides and tools for running Windows add-ons for Microsoft Flight Simulator 2024 (Steam) under Proton on Linux: Fenix, FSLabs, PMDG, GSX, ChasePlane, SayIntentions, Active Sky and about twenty more.

The site is at **https://brunofgmag.github.io/flightsim-on-linux/**. Start there if you just want your add-ons working.

## What is in here

| Path | What it is |
|---|---|
| `bin/flightsim-on-linux` | Command-line helper: `machine`, `doctor`, `proton build`, `proton check` |
| `bin/flightsim-on-linux-gui` | The same, through zenity or kdialog dialogs |
| `proton/build-proton-fenix.sh` | Builds Proton Fenix A320 from a Proton-CachyOS release |
| `checks/` | Tests a Proton in a throwaway prefix |
| `patches/wine/` | The two Wine patches this project adds |
| `site/` | Source of the website; `tools/build_site.py` writes it to `docs/` |
| `AGENTS.md` | Instructions for AI coding agents working on a user's machine |

## Quick start

```
git clone https://github.com/brunofgmag/flightsim-on-linux.git ~/flightsim-on-linux
~/flightsim-on-linux/bin/flightsim-on-linux doctor
~/flightsim-on-linux/bin/flightsim-on-linux proton build
~/flightsim-on-linux/bin/flightsim-on-linux proton check
```

`doctor` needs only Python 3. The build needs git, curl, a C toolchain with autoconf, flex, bison, perl and the MinGW-w64 cross compiler; `doctor` lists what is missing. See [Start here](https://brunofgmag.github.io/flightsim-on-linux/start.html) for the full walk-through.

## Working on it

```
python3 -m unittest discover -s tests -t .
python3 tools/build_site.py
python3 -m http.server -d docs 8000
```

The last command serves the site at http://127.0.0.1:8000/.

Fixes were found and measured on one machine: CachyOS, KDE Plasma on Wayland, an NVIDIA RTX 4080, October 2026. Reports from other machines are welcome as issues; include the output of `bin/flightsim-on-linux doctor` with licence keys and account names removed.

## Credits

Proton Fenix A320 is built on the [fenix-a320-linux-patch](https://github.com/marselnenaj/fenix-a320-linux-patch) overlay by its authors, on [Proton-CachyOS](https://github.com/CachyOS/proton-cachyos) and on [Wine](https://www.winehq.org/). This project downloads them from their own releases and doesn't redistribute them.

Not affiliated with Microsoft, Valve, CodeWeavers, the Wine project or any add-on developer.

## Licences

- Code (`bin/`, `fsol/`, `proton/`, `checks/`, `tools/`, `tests/`): MIT, in `LICENSE`.
- Text of the site and the guides (`site/`, `docs/`, the Markdown files): CC BY 4.0, in `LICENSE-docs`.
- Wine patches (`patches/wine/`): LGPL-2.1-or-later, like Wine itself, in `patches/wine/COPYING`.
