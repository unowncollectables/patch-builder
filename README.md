# Pokemon 3DS Builder

Turns **your own** Pokemon Platinum (or SoulSilver) cartridge dump into a game that runs natively on a **New Nintendo 3DS**.
No game files are in this repo or in the download. Everything happens on your computer, with your file.

> **Platinum status:** the Platinum project ([3ds-platinum](https://github.com/unowncollectables/3ds-platinum)) is **not public yet**.
> Until it is, the Platinum build stops at "Downloading the project" with a message saying so. SoulSilver works today.

## What you need

- A Windows 10 or 11 PC (about 15 GB free space, internet).
- A **New** 3DS / New 2DS XL with custom firmware (Luma3DS + Homebrew Launcher; FBI if you want a HOME Menu icon).
- Your own clean dump of **Pokemon Platinum US, Rev 1** (`.nds`, 128 MB). Please do not ask anyone for ROMs.

## Steps

1. Download **Pokemon3DSBuilder.exe** from the [Releases page](https://github.com/unowncollectables/patch-builder/releases) and double-click it.
   (Windows may say "Windows protected your PC": press **More info**, then **Run anyway**.)
2. Tick **Pokemon Platinum**.
   *Optional:* **following Pokemon** (your Pokemon walks behind you) need your **SoulSilver** game file too. Pick it in box 2b;
   you do not have to tick SoulSilver. Without it, Platinum builds fine, just without following Pokemon.
3. Press **Browse** and pick your `.nds` file. A **green tick** means it is the right one. A red cross tells you what is wrong.
4. **Output folder** (box 3): the finished `.cia` and `.3dsx` are always saved here, in `Platinum\` and `SoulSilver\`,
   with a `How to put this on your 3DS.txt`. Default: `Documents\Pokemon 3DS Builds`. **Open game folder** opens it.
5. Put your 3DS SD card in the computer and press **Find it**. (You can skip this; then the files are just saved on the PC.)
6. Press the big green **Build and install**. If Windows asks to install **WSL**, say yes and restart the PC when asked, then open the builder and press the button again.
7. Wait. **The first build takes an hour or more.** Later builds are quick.
8. When it says **Installed!**, put the SD card back in the 3DS, open the **Homebrew Launcher** and start **Platinum 3DS**.
   For a HOME Menu icon, open **FBI** > SD > `cias` > `platinum3ds.cia` > Install.

### Updating (you already have it installed)

- Press the blue **Update** button. It gets the newest version of the ticked game(s) from GitHub, shows what's new
  (or "Already up to date"), rebuilds and copies to the SD card the same way as **Build and install**.
  Your game files, the build cache and your save are never touched; the save is backed up before every copy.
- **Build and install** also checks GitHub for game updates each time. To stop that, tick **Advanced > Offline**.
- If the window says **"A newer builder is available"**, click it and download the new `Pokemon3DSBuilder.exe`.
- Your own edits to a project folder are kept with `git stash` before an update (power users: `git stash list`).

### Cleaning up

After a good copy the builder tidies its own leftovers (half-finished downloads, old copies of its own `.3dsx`/`.cia`).
On the SD card it keeps the current build plus one backup (`platinum3ds_full.3dsx.prev`). **Advanced > Clean up old builds**
lists what it would remove and asks first. Saves, `save_backups/`, your game files, settings, the toolchain and the build
cache are never removed, so rebuilds stay fast.

### Where the files go on the SD card

| File | Goes in |
|---|---|
| `platinum3ds_full.3dsx` (Homebrew Launcher) | `SD:/3ds/platinum3ds/` |
| your game file (copied once, as `platinum.nds`) | `SD:/3ds/platinum3ds/` |
| `platinum3ds.cia` (HOME Menu installer) | `SD:/cias/` |
| your save `platinum.sav` | `SD:/3ds/platinum3ds/` (never overwritten; backed up to `save_backups/` first) |

If you skipped the SD card, copy the files into those folders yourself.

## Troubleshooting

| You see | Do this |
|---|---|
| "Windows protected your PC" (blue SmartScreen box) | Normal for a new download. Press **More info**, then **Run anyway**. |
| "No module named 'ndspy'" or "built without following Pokemon" | Advanced > **Reinstall tools**, then press **Build and install** again. Platinum still installs without following Pokemon either way. |
| "The Platinum project is not public yet" | Not your fault. Wait for the public release, then press the button again. |
| Red cross: "another region" / "Rev 0" / "trimmed" / "has been changed" | You need an untouched US Rev 1 dump, 128 MB. Dump your cartridge again (for example with GodMode9). |
| "WSL has no Linux installed" | Open PowerShell and run `wsl --install -d Ubuntu`, restart, try again. |
| "Check that this computer is online" | Connect to the internet and press the button again. |
| "The disk is full" | Free about 15 GB and try again. |
| "ran out of memory" | Close other programs and try again. |
| "write-protected" | Slide the SD card's lock switch up and plug it in again. |
| The game is not in the Homebrew Launcher | Check the `.3dsx` is in `SD:/3ds/platinum3ds/`. It needs a **New** 3DS. |
| The HOME Menu icon is flat, not the animated 3D banner (Platinum) | The 3D banner needs Python 3.12+ in WSL (Ubuntu 24.04). The log says why; press **Copy details for help**. |
| Anything else | Press **Copy details for help** and paste it into a [GitHub issue](https://github.com/unowncollectables/patch-builder/issues). Never attach game files. |

SoulSilver works the same way: tick **Pokemon SoulSilver** and pick your US SoulSilver `.nds`.
Giving a SoulSilver file while building Platinum also adds following Pokemon to Platinum.

## Make the exe yourself (optional)

Install [Python 3](https://www.python.org/downloads/) (tick *Add python to PATH*) and double-click `build_exe.bat`.
Or run without an exe: `python p3ds_builder.py`. Maintainers: pushing a `v*` tag builds the exe and attaches it to a release (copy `ci/release-exe.yml` into `.github/workflows/` once; a token without workflow scope could not add it there).

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
`ss_build.sh` check / needs / stage steps are tested. The new two-game window was driven headless (virtual display, stand-in build scripts) through download, needs, tool setup, both games' steps and the `.cia` option. **Not yet tested: the long SoulSilver compile through this builder, the real `.cia` steps for both games, and the Windows-only parts (WSL, drive detection, the `.exe`).** The commands it
runs are the same ones the SoulSilver repo documents (`scripts/ss3ds_build.sh`, then `make` in `soulsilver3ds/full`).

## For AI helpers

Read [AGENTS.md](AGENTS.md).
