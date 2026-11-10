# Pokemon 3DS Builder

Turns **your own** Pokemon Platinum, SoulSilver or HeartGold cartridge dump into a game that runs natively on a **New Nintendo 3DS**.
No game files are in this repo or in the download. Everything happens on your computer, with your file.

## What this installs and what you need

**The builder installs everything by itself.** The first time you press the green button it sets up:
- **WSL + Ubuntu 24.04** (a small Linux inside Windows, from Microsoft). Windows shows a permission box (click **Yes**) and asks
  you to **restart once**. After the restart, open the builder yourself (it never starts by itself at boot, and the WSL install is only ever run once). An Ubuntu window may ask you to make a
  user name and password once: pick anything you will remember.
- **Build tools inside Ubuntu**: git, make, compilers, devkitPro (3DS toolchain), and the Python modules ndspy, pillow, numpy.
  It may ask for permission once more. Nothing is installed in Windows itself except WSL.
- **The game projects** (downloaded from GitHub, and updated on every build).

**What you still need:**
- Windows 10 (version 2004 or newer) or Windows 11, with **internet** and about **15 GB free**.
- **Virtualization turned on** in the PC's BIOS. It is on for most PCs; if not, the builder tells you.
- Time: the **first build takes about 1-2 hours** (mostly downloading and compiling). Later builds take minutes.
- A **New** 3DS / New 2DS XL with custom firmware, and **your own** game dump.

## Build and install

1. Download **Pokemon3DSBuilder.exe** from the [latest release](https://github.com/unowncollectables/patch-builder/releases/latest) and open it.
2. Click **Choose game files** and select one or more `.nds` files. The builder recognizes and checks each game for you. Use **Remove** to leave a game out.
3. To install to your SD card, plug it in and click **Find SD card**. If more than one card is found, use **Browse**. Leave the field empty to save to your PC.
4. Click **Build and install to SD** or **Build and save to PC**. If Windows needs to install WSL, follow the prompts, then reopen the builder after restarting. Your selections are saved.
5. Open the finished game in Homebrew Launcher on your New 3DS. If you made a HOME Menu installer, use FBI to install its `.cia` from the card’s `cias` folder.

The first build can take an hour or more. Finished files also go to **Documents → Pokemon 3DS Builds** by default. Use **Open output folder** to find the files and instructions. **Build options** shows the output location and HOME Menu installer toggle. **Advanced** has manual game selection, maintenance tools, and the optional SoulSilver file for Platinum followers.

The build button also checks for new game code. **Advanced → Offline** skips that check, and **Advanced → Update and build** checks even when Offline is selected. If a newer builder is available, the window links to the download.

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
| "cannot see SD card F:" (v1.0.7 and older) | Use v1.0.8 or newer: it copies to the SD card from Windows itself. |
| "Windows protected your PC" (blue SmartScreen box) | Normal for a new download. Press **More info**, then **Run anyway**. |
| "No module named 'ndspy'" or "built without following Pokemon" | Advanced > **Check and repair tools**, then press the build button again. Platinum still installs without following Pokemon either way. |
| Red cross: "another region" / "Rev 0" / "trimmed" / "has been changed" | You need an untouched US Rev 1 dump, 128 MB. Dump your cartridge again (for example with GodMode9). |
| "WSL cannot start" / "virtualization is not enabled" / restart loop | Windows cannot run WSL2. The builder explains this and offers **Turn on** for the two Windows settings it needs (Virtual Machine Platform and the hypervisor); then restart once. Common on PCs set up for VirtualBox or VMware (update them afterwards if they complain). If it still fails, turn on Virtualization (Intel VT-x / AMD SVM) in the PC's BIOS/UEFI. Advanced > **Check setup** shows a plain report of this PC. |
| Asked to install WSL again and again | Update to v1.1.5 or newer. The builder installs WSL at most once and starts nothing at boot. Advanced > **Clean up boot entries** removes anything older versions left behind. |
| "WSL has no Linux installed" | Open PowerShell and run `wsl --install -d Ubuntu`, restart, try again. |
| "Check that this computer is online" | Connect to the internet and press the button again. |
| "The disk is full" | Free about 15 GB and try again. |
| "ran out of memory" | Close other programs and try again. |
| "write-protected" | Slide the SD card's lock switch up and plug it in again. |
| The game is not in the Homebrew Launcher | Check the `.3dsx` is in `SD:/3ds/platinum3ds/`. It needs a **New** 3DS. |
| The HOME Menu icon is flat, not the animated 3D banner (Platinum) | The 3D banner needs Python 3.12+ in WSL (Ubuntu 24.04). The log says why; press **Copy details for help**. |
| Anything else | Press **Copy details for help** and paste it into a [GitHub issue](https://github.com/unowncollectables/patch-builder/issues). Never attach game files. |

SoulSilver works the same way: pick your US SoulSilver `.nds` using **Choose game files**.
Giving a SoulSilver file while building Platinum also adds following Pokemon to Platinum.

### HeartGold (experimental)

Pick your US HeartGold `.nds` using **Choose game files**. HeartGold is built from the SoulSilver project
but is a completely separate game: its own download (`~/Pokemon-HeartGold-New3ds`), its own output folder (`HeartGold\`), its
own SD folder and save, and its own HOME Menu icon (with Ho-Oh). Building or installing it never touches SoulSilver or its save,
and you can have both installed. A SoulSilver file in the HeartGold box (or the other way round) gets a red cross.

| File | Goes in |
|---|---|
| `heartgold3ds.3dsx` (Homebrew Launcher) | `SD:/3ds/heartgold3ds/` |
| your game file (copied once, as `heartgold.nds`) | `SD:/3ds/heartgold3ds/` |
| `heartgold3ds.cia` (HOME Menu installer) | `SD:/cias/` |
| your save `heartgold.sav` | `SD:/3ds/heartgold3ds/` (never overwritten; backed up first) |

**Experimental:** HeartGold compiles and packages, but it has not been played on a console yet. Expect surprises, and
please report them.

## Make the exe yourself (optional)

Install [Python 3](https://www.python.org/downloads/) (tick *Add python to PATH*) and double-click `build_exe.bat`.
Or run without an exe: `python p3ds_builder.py`. Maintainers: pushing a `v*` tag builds the exe and attaches it to a release (copy `ci/release-exe.yml` into `.github/workflows/` once; a token without workflow scope could not add it there).

## Linux and the command line

Linux: `python3 p3ds_builder.py` (needs `python3-tk`). Without the window, per game:

```sh
P3DS_ROOT=~/3ds-platinum P3DS_PLATINUM=~/platinum.nds P3DS_SD=/media/me/SD [P3DS_CIA=1] bash p3ds_build.sh check | all | quick
P3DS_ROOT=~/Pokemon-Soulsilver-New3ds P3DS_SOULSILVER=~/soulsilver.nds P3DS_SD=/media/me/SD [P3DS_CIA=1] bash ss_build.sh check | all | quick
P3DS_ROOT=~/Pokemon-HeartGold-New3ds P3DS_HEARTGOLD=~/heartgold.nds P3DS_SD=/media/me/SD [P3DS_CIA=1] bash hg_build.sh check | all | quick
bash p3ds_get.sh platinum | soulsilver | heartgold   # download the project into ~/3ds-platinum, ~/Pokemon-Soulsilver-New3ds or ~/Pokemon-HeartGold-New3ds
```

`p3ds_build.sh` steps: `check needs setup link stage pspoke full patches testpatches relink followers cia deploy`.
`ss_build.sh` steps: `check needs setup stage build link cia deploy`. Every step is safe to repeat.
Needs root once for `setup` (apt + devkitPro); the GUI does that for you (WSL runs it as root, Linux asks via `pkexec`/`sudo`).

The builder installs the same tools for Platinum, SoulSilver and HeartGold. On a fresh setup it downloads and verifies the 3DS tools from devkitPro’s official container image. Docker does not need to be installed. If setup fails, open the build details to see which download or install failed. For a manual devkitPro setup on Ubuntu:

```sh
wget -U "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0" https://apt.devkitpro.org/install-devkitpro-pacman
chmod +x install-devkitpro-pacman
sudo ./install-devkitpro-pacman
sudo dkp-pacman -Sy 3ds-dev
```

```sh
sudo python3 -m pip install --break-system-packages ndspy pillow numpy
```

## Advanced (in the window)

Project folders (leave empty and they are downloaded into your Linux/WSL home, and fast-forwarded to the latest version on each
build if you have no local edits), Check my setup, Test my patches (Platinum), Quick rebuild, Reinstall tools, Rebuild everything, Delete build files, Delete old builds.

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

HeartGold (experimental): the ROM checks, the game list and the script's syntax are tested, and the commands `hg_build.sh`
runs (`scripts/hg_build.sh`, then `make` and `make cia` in `heartgold3ds/full`) produced a `.3dsx` and a `.cia` in a Linux
container. **Not yet tested: the window with the HeartGold box on a real screen, the build through this builder on
Windows/WSL, and the game itself on a console.**

## For AI helpers

Read [AGENTS.md](AGENTS.md).
