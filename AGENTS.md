# AGENTS.md - handoff for any AI (Claude, Grok, Codex...)

**What this is:** the friendly one-window builder for Nathan's (`unowncollectables`) native New 3DS ports:
Platinum (`3ds-platinum`), SoulSilver (`Pokemon-Soulsilver-New3ds`), experimental HeartGold (same project as SoulSilver, built as a separate game) and experimental Pokemon Pinball: Ruby & Sapphire (`Pokemon-Pinball-New3ds`, a GBA game). It does not contain either port. It downloads the
projects, installs the build tools, compiles, and copies the result to the SD card. GPL-3.0, personal and experimental.

**Rules:** keep the dry-run-before-change behaviour of the patch installer (all-or-nothing); never commit ROMs, saves, extracted assets or builds (the user picks their own `.nds`; it is read from where it is
or copied only onto their SD card). A save on the SD card is backed up before every install and never overwritten. Work in a
branch, PR to main, small diffs. Keep the window plain-English: every failure should end in one sentence telling a non-developer
what to do (`##HINT:` lines from the scripts, `HINTS` in `p3ds_builder.py`).

**Files**
| File | Role |
|---|---|
| `p3ds_builder.py` | the Tk window (standard library only). Ticks for the two games, ROM checks (`rom_report`), SD card finder, project finder, step planning (`plan_for`), runs the scripts through `wsl.exe` on Windows. |
| `p3ds_build.sh` | Platinum driver. Steps `check needs setup link stage pspoke full patches testpatches relink followers cia deploy`. |
| `hg_build.sh` | HeartGold driver (experimental). Same steps as `ss_build.sh`; wraps the SoulSilver repo's `scripts/hg_build.sh` and `make` in `heartgold3ds/full`. |
| `pinball_build.sh` | Pinball driver (experimental). Steps `check needs setup stage build cia deploy`; no `link` step. Wraps the Pinball repo's `scripts/build.sh` and `scripts/make_cia.sh`. |
| `ss_build.sh` | SoulSilver driver. Steps `check needs setup stage build link cia deploy`. Wraps the SoulSilver repo's own `scripts/ss3ds_build.sh` and `make` in `soulsilver3ds/full`. |
| `p3ds_get.sh` | clones / fast-forwards a project into `~/3ds-platinum` or `~/Pokemon-Soulsilver-New3ds`; `ensure-git` installs git as root. |
| `apply_worktree.py` | Platinum only: installs the user's `work_patches/` into the generated tree, all-or-nothing. |
| `build_cia_tools.sh` | copy of the SoulSilver repo's script (makerom + bannertool from source) for the optional `.cia`. |
| `build_exe.bat` | PyInstaller; bundles all of the above into `Pokemon3DSBuilder.exe`. Add any new script to its `--add-data` list. |

**Script protocol (GUI <-> scripts):** one call runs steps in order; each prints `##STEP:<name>:START|OK|FAIL`; `##NEED:<step>`
(from `needs`), `##HINT:<sentence>`, `##PROJECT:<folder>`, `##INSTALLED:<sd folder>`, `##DONE:OK|FAIL`. Settings arrive as env vars
(`P3DS_ROOT`, `P3DS_PLATINUM`, `P3DS_SOULSILVER`, `P3DS_HEARTGOLD`, `P3DS_PINBALL`, `P3DS_SD`, `P3DS_CIA=1`). Exit code 3 from `p3ds_get.sh` means git is missing.
**HeartGold (experimental)** is a third game in `GAMES`, driven by `hg_build.sh` (the twin of `ss_build.sh`, same steps; keep them in line). It is built from the SoulSilver project but shares nothing with SoulSilver at build or install time: its own clone (`p3ds_get.sh heartgold` -> `~/Pokemon-HeartGold-New3ds`, settings field `hgrepo`), its own tree (`pspoke-hg`, `build-hg`), `P3DS_HEARTGOLD` for the ROM (US `IPKE`; the exact dump, or one whose data files and overlays all match it: `HG_FILES_SHA1` in the window, the repo's `scripts/hg_romcheck.py` in the script), SD folder `3ds/heartgold3ds` with `heartgold.nds`/`heartgold.sav`, and `heartgold3ds.cia`. `p3ds_get.sh heartgold` takes branch `main` (`P3DS_HG_BRANCH` overrides, for testing).
**Pinball (experimental)** is the one GBA game: `.gba` file (US `BPPE`, `PINBALL_SHA1`), `P3DS_PINBALL`, settings field `pbrepo`, clone `~/Pokemon-Pinball-New3ds` (`P3DS_PINBALL_BRANCH` overrides the branch, for testing). Its project downloads pret/pokepinballrs and agbcc at the commits in its `upstream.lock`, rebuilds the game, requires the result to equal the user's ROM, then compiles it with the port (needs `libpng-dev`, installed by `setup`). The whole game ends up inside the `.3dsx`/`.cia`, so no ROM is copied to the card (`SD_LAYOUT` nds name `None`); SD folder `3ds/pinball-rs` with `pinball.sav`, `log.txt`, `crash.txt`; output folder `Pinball`.
Adding a game = a new entry in `GAMES` (p3ds_builder.py), a `<game>_build.sh` using the same protocol, and a `plan_for` branch.

**SD layout it writes:** `3ds/<game>3ds/` with the `.3dsx`, the user's `.nds`, `save_backups/`; `.cia` files in `cias/`.

**Status:** Platinum path was tested with stand-in builds. SoulSilver path and the two-game window are new: step logic and ROM checks
are tested, the window was driven headless with stub scripts; the long compile, real `.cia` steps and Windows/WSL parts are not (see README Status). The ports' own
AGENTS.md files (in `3ds-platinum` and `Pokemon-Soulsilver-New3ds`) explain the games' code; start there for anything about the games themselves.

For project-wide rules (save safety, one-build rule, toggle rule) see the SoulSilver repo's AGENTS.md.
