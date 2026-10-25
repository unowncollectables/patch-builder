# Platinum 3DS Builder

A one-button window (and optional `.exe`) that builds the Platinum 3DS port and installs it on your SD card.
Your game files stay on your computer; nothing is uploaded.

## Using it

1. Pick your **Pokemon Platinum .nds** (US, Rev 1, your own clean dump). A green tick means it is the right file;
   a red cross says exactly what is wrong (wrong region, Rev 0, trimmed, patched...).
2. Pick your **3DS SD card** (or press **Find it**). The game goes in `3ds/platinum3ds`; your `platinum.sav` is backed
   up to `save_backups/` first, and the copy is checked after it is written.
3. Optional: your **SoulSilver .nds** for following Pokemon.
4. Press **Build and install**.

The button works out what is still needed and only does that: install the build tools (first time), prepare pspoke,
the long full build (first time, or when `pspoke-patches/` changed), then your patches, compile, and copy to the card.
Pressing it again after a change is always safe.

If something goes wrong, a red box says what to do in plain words. **Copy details for help** copies the full log
(it is also saved to `p3ds_builder_last_log.txt` in your home folder).

**Advanced** has the project folder (found automatically when possible) and single steps: Check setup,
Test my patches (changes nothing), Quick rebuild, Reinstall tools, Rebuild everything.

## The HOME Menu version (.cia)

Every build also makes `platinum3ds_full.cia` (the installed version gets the New 3DS's 804 MHz, L2 cache and 124 MB
mode) and copies it to `cias/platinum3ds.cia` on the card. On the 3DS: **FBI > SD > cias > platinum3ds.cia >
Install and delete CIA**, then start it from the HOME Menu. makerom and bannertool are built from source the first time
(`build_cia_tools.sh`, from GabeSud0/pokeplatinum-3ds, GPL-3.0; needs gcc/g++). If the .cia step fails, the .3dsx is
still installed and the log says why.

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

## Windows

1. Install WSL once: the builder offers to do it (or run `wsl --install` in an admin PowerShell), then restart.
2. Clone your **3ds-platinum** project, ideally inside WSL (`git clone ... ~/3ds-platinum`), which builds much faster.
   The builder looks for it in WSL `~/3ds-platinum` and in your Windows home, Documents, Desktop and Downloads.
3. Run `python p3ds_builder.py`, or make the exe: install Python 3 from python.org, double-click `build_exe.bat`,
   then run `Platinum3DSBuilder.exe` (it carries its own copy of the scripts, so you can move it anywhere).

## Linux

`python3 p3ds_builder.py` (needs `python3-tk`), or the script directly:
`P3DS_ROOT=~/3ds-platinum P3DS_PLATINUM=~/platinum.nds P3DS_SD=/media/me/SD bash p3ds_build.sh check | all | quick`.
Steps: `check needs setup link stage pspoke full patches testpatches relink cia followers deploy`.

## Status

Tested here: the patch installer against the real pspoke (pinned commit + your pspoke-patches) and pokeplatinum
`47c28d53` sources (new / edited / deleted / CRLF patches, all-or-nothing on conflict), ROM and SD checks, the
pspoke re-prepare, and the window itself (Linux, virtual display) with a stand-in build script, including Cancel.
Not tested: the long compile on real devkitPro, the Windows-only parts (WSL, drive detection) and the `.exe`.
