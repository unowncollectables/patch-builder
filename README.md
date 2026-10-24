# Platinum 3DS Builder

A small window (and optional `.exe`) around the manual build in the main README.
Your ROMs stay on your computer; nothing is uploaded.

## Windows
1. One-time: `wsl --install` in an admin PowerShell (Ubuntu), then restart.
2. Clone your **3ds-platinum** project **inside WSL** for speed (`git clone ... ~/3ds-platinum`), or anywhere on a Windows drive (slower). This repo is only the tool; it points at that clone.
3. Run `python p3ds_builder.py`, or make an exe: install Python 3 from python.org, double-click `build_exe.bat`,
   then run `dist\Platinum3DSBuilder.exe`.
4. Pick the project folder, your Platinum ROM (US Rev 1), and optionally SoulSilver and your SD card.
5. Buttons, in order: **Check setup**, **Install tools** (once), **Full build** (first time, long), **Quick rebuild** (after code changes).

## Linux
`python3 p3ds_builder.py` (needs `python3-tk`), or use the script directly: `P3DS_ROOT=~/3ds-platinum bash p3ds_build.sh check` / `all` / `quick`.

## What it does
`p3ds_build.sh` splits the README steps into repeatable stages (check, setup, stage, pspoke, full, patches, relink, followers, deploy).
`apply_worktree.py` applies `work_patches/` to the generated tree and skips patches that are already applied.
Deploy backs up `platinum.sav` into `save_backups/` before copying the new `.3dsx`.

Status: patch application and the check/stage/pspoke steps were tested against the real upstream sources.
The long compile steps and the window itself have not been run end to end yet; report the first error line from the log.
