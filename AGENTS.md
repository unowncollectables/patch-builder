# AGENTS.md - handoff for any AI (Claude, Grok, Codex...)

**What this is:** `patch-builder` is Nathan's (`unowncollectables`) one-button Windows/Linux builder + installer ("Platinum 3DS Builder") for the `3ds-platinum` port. The user picks their own Platinum `.nds` (US Rev 1) and SD card; it installs build tools (WSL/devkitPro), applies the user's patches, builds, backs up `platinum.sav` (verified copy) and installs to `3ds/platinum3ds`. Nothing leaves the PC.

**Files:** `p3ds_builder.py` (Tk GUI), `p3ds_build.sh` (steps: `check needs setup link stage pspoke full patches testpatches relink followers deploy`; env `P3DS_ROOT`, `P3DS_PLATINUM`, `P3DS_SD`), `apply_worktree.py` (all-or-nothing patch installer with dry run; handles new/edited/deleted patches and CRLF), `build_exe.bat` (PyInstaller exe).

**Known gap (important):** it is **Platinum-only**. It does not build or install the SoulSilver port (`Pokemon-Soulsilver-New3ds`) and has no ROM check for SoulSilver; SoulSilver is only an optional input for Platinum's follower sprites. Wanted: a SoulSilver mode (verify the ROM against that repo's `upstream.lock`, run its `scripts/bootstrap.sh` and `scripts/ss3ds_build.sh`, link, back up `soulsilver.sav`, install to `3ds/soulsilver3ds`), a "both ports" option, and plain-language instructions for other people. Keep the one-button, red-box-says-what-to-do style.

**Rules:** never commit ROMs, saves or builds; never overwrite a user save without a verified backup; keep dry-run-before-change behaviour. Per README, untested: the long compile on real devkitPro, WSL/drive detection on Windows, and the `.exe`.

See the SoulSilver repo's AGENTS.md for project-wide rules (save safety, one-build rule, toggle rule).
