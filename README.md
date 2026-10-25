# Pokemon 3DS Builder

One window that builds the **Platinum 3DS** port, the **SoulSilver 3DS** port, or both, and puts them on your 3DS SD card.
You bring your own game files; nothing is uploaded and no game data is in this repo.

| Port | Project it builds |
|---|---|
| Pokemon Platinum (US, Rev 1) | [3ds-platinum](https://github.com/unowncollectables/3ds-platinum) |
| Pokemon SoulSilver (US) | [Pokemon-Soulsilver-New3ds](https://github.com/unowncollectables/Pokemon-Soulsilver-New3ds) |

You need a **New Nintendo 3DS / New 2DS XL with custom firmware** (Luma3DS), and **your own legal dump** of each game you want
(`.nds`, from your own cartridge). Please do not ask anyone for ROMs.

## Install in 5 steps (Windows)

1. **Get the builder.** Download this repo (green **Code** button, **Download ZIP**) and unzip it. Install
   [Python 3](https://www.python.org/downloads/) (tick *Add python to PATH*), then double-click `build_exe.bat` to make
   `Pokemon3DSBuilder.exe`. (Or skip the exe: `python p3ds_builder.py`.)
2. **Open it** and tick **Pokemon Platinum**, **Pokemon SoulSilver**, or both.
3. **Pick your game file(s)**: press **Browse** next to each ticked game and choose your `.nds`. A green tick means it is the
   right file. A red cross says exactly what is wrong (wrong region, wrong game, Rev 0, trimmed...).
4. **Pick your SD card** (press **Find it**), plugged into the computer. Optional: without it the games are built but not copied.
5. Press the big green **Build and install**.

The first time it installs everything it needs (it asks Windows to install WSL, a Linux that runs inside Windows; restart
once if it asks), downloads the two projects, and compiles. **The first build takes a long time (an hour or more per game).**
Later builds only redo what changed. If something goes wrong a red box says what to do in plain words, and
**Copy details for help** copies the full log so you can paste it to an AI helper or into a GitHub issue.

Tick **Also make the installer for the HOME Menu (.cia)** if you want a HOME Menu icon instead of launching from the
Homebrew Launcher.

### What ends up on the SD card

| Game | `.3dsx` (Homebrew Launcher) | your game file | save | HOME Menu installer (optional) |
|---|---|---|---|---|
| Platinum | `3ds/platinum3ds/platinum3ds_full.3dsx` | `3ds/platinum3ds/platinum.nds` | `platinum.sav`, backed up to `save_backups/` before every install | `cias/platinum3ds.cia` |
| SoulSilver | `3ds/soulsilver3ds/soulsilver3ds.3dsx` | `3ds/soulsilver3ds/soulsilver.nds` | `soulsilver.sav`, backed up to `save_backups/` before every install | `cias/soulsilver3ds.cia` |

Install a `.cia` with FBI. The `.nds` is copied only if the card does not have one already, and **a save on the card is never overwritten**.
Saves are plain `.sav` files, so they can be moved between the port and a real cartridge or flashcart (keep your own backup too).
Launch from the Homebrew Launcher (or HOME Menu) on the New 3DS.

### Platinum with following Pokemon

If you build Platinum and also give a SoulSilver file, Platinum gets HGSS-style following Pokemon (`followers.bin`).
Building SoulSilver on its own does not need Platinum, and the other way round.

## Linux and the command line

Linux: `python3 p3ds_builder.py` (needs `python3-tk`). Without the window, per game:

```sh
P3DS_ROOT=~/3ds-platinum P3DS_PLATINUM=~/platinum.nds P3DS_SD=/media/me/SD [P3DS_CIA=1] bash p3ds_build.sh check | all | quick
P3DS_ROOT=~/Pokemon-Soulsilver-New3ds P3DS_SOULSILVER=~/soulsilver.nds P3DS_SD=/media/me/SD [P3DS_CIA=1] bash ss_build.sh check | all | quick
bash p3ds_get.sh platinum | soulsilver     # download the project into ~/3ds-platinum or ~/Pokemon-Soulsilver-New3ds
```

`p3ds_build.sh` steps: `check needs setup link stage pspoke full patches testpatches relink followers cia deploy`.
`ss_build.sh` steps: `check needs setup stage build link cia deploy`. Every step is safe to repeat.
Needs root once for `setup` (apt + devkitPro); the GUI does that for you (WSL runs it as root, Linux asks via `pkexec`/`sudo`).

## Advanced (in the window)

Project folders (leave empty and they are downloaded into your Linux/WSL home, and fast-forwarded to the latest version on each
build if you have no local edits), Check setup, Test my patches (Platinum), Quick rebuild, Reinstall tools, Rebuild everything.

## Your patches

The builder installs everything in your `3ds-platinum` project:

| Put the patch in | Format |
|---|---|
| `work_patches/overlays/<folder>__<file>.c.patch` | game source (pokeplatinum). `overlay005/fieldmap.c` is written `overlay005__fieldmap.c`; diff headers `--- a/overlay005__fieldmap.c` / `+++ b/...` |
| `work_patches/overlays/include_<name>.patch` | headers, git-style (`a/include/...`), applied in `native-probe/pokeplatinum` |
| `work_patches/native/<anything>.patch` | pspoke `port/` code, git-style (`a/native-audio-app/platform.c`) |
| `pspoke-patches/NNNN-*.patch` | commits on pspoke (`git format-patch`); a change here triggers a fresh full build |

You can keep editing them. On every build:
- a **new** patch is applied,
- an **edited** patch has its old version taken out and the new one put in,
- a **deleted** patch is taken out again,
- an unchanged patch is skipped.

Before anything changes, every patch is tried with a dry run. If one does not fit, **nothing** is changed and the
log names the patch, so the source is never left half-patched. Windows line endings (from a Windows git checkout)
are fixed automatically. Use **Advanced > Test my patches** to check a patch without building.

## Windows details

- WSL: the builder offers to install it (or run `wsl --install -d Ubuntu` in an admin PowerShell), then restart.
- Projects live best inside WSL (`~/3ds-platinum`, `~/Pokemon-Soulsilver-New3ds`), which builds much faster; the builder puts them there itself. It also finds clones in your Windows home, Documents, Desktop and Downloads.
- The exe carries its own copy of the scripts, so you can move it anywhere. Run `build_exe.bat` again after the scripts change.

## Status

Platinum: tested here against the real pspoke (pinned commit + your pspoke-patches) and pokeplatinum `47c28d53` sources
(new / edited / deleted / CRLF patches, all-or-nothing on conflict), ROM and SD checks, the pspoke re-prepare, and the window
itself (Linux, virtual display) with a stand-in build script, including Cancel.

SoulSilver and the two-game window (added later): the ROM checks, project finder, step plans, project download and the
`ss_build.sh` check / needs / stage steps are tested. **Not yet tested: the new two-game window itself (Tk was not available where it was written), the long SoulSilver compile through this builder, the new `.cia` steps for both games, and the Windows-only parts (WSL, drive detection, the `.exe`).** The commands it
runs are the same ones the SoulSilver repo documents (`scripts/ss3ds_build.sh`, then `make` in `soulsilver3ds/full`).

## For AI helpers

Read [AGENTS.md](AGENTS.md).
