#!/usr/bin/env bash
# Platinum 3DS build driver (runs inside Linux / WSL2). The GUI calls it; you can also run it by hand:
#   p3ds_build.sh <step> [<step> ...]
# Steps (each can be run on its own, and each is safe to repeat):
#   check      show what is installed / staged / built
#   needs      print which one-time steps are still missing (##NEED:<step>); used by "Build and install"
#   setup      install tools (apt + devkitPro + pip) and link /workspace/platinum3ds  [needs root]
#   link       only create the /workspace/platinum3ds link the project scripts expect  [needs root]
#   stage      copy + verify your ROMs into baserom/ and roms/
#   pspoke     clone pspoke, check out the pinned commit, apply pspoke-patches/
#   full       from-scratch pspoke build (rebuild_all.sh) - the long one
#   patches    install work_patches/ into the generated tree (new, edited and deleted patches are all handled)
#   testpatches  dry run of 'patches': says whether every patch fits, changes nothing
#   relink     rebuild changed objects + link the .3dsx  (the quick one after you change code)
#   followers  build followers.bin from your SoulSilver ROM (optional)
#   cia        make the HOME Menu installable platinum3ds_full.cia (builds makerom/bannertool the first time)
#   deploy     copy .3dsx / ROM / followers.bin (and the .cia if 'cia' ran) to the SD card (backs up the save first)
#   all        check stage pspoke full patches relink followers [cia] deploy     ([cia] only when P3DS_CIA=1)
#   quick      patches relink [cia] deploy
# Environment: P3DS_CIA=1 also makes the .cia;  P3DS_ROOT (repo), P3DS_PLATINUM (path to .nds), P3DS_SOULSILVER (optional), P3DS_SD (SD root, optional)
#              P3DS_LEGACY=1 also applies the older patches/*.patch
set -uo pipefail

# The GUI passes its settings as base64-encoded "export ..." lines so no path ever needs shell quoting.
if [ "${1:-}" = "--env-b64" ]; then eval "$(printf %s "${2:-}" | base64 -d)"; shift 2; fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The GUI starts us with setsid; remember our process group so its Cancel button can stop the whole build.
[ "$(ps -o pgid= $$ 2>/dev/null | tr -d ' ')" = "$$" ] && echo $$ > "/tmp/p3ds_builder.$(id -u).pid" 2>/dev/null
ROOT="${P3DS_ROOT:-}"
[ -z "$ROOT" ] && [ -d "$HERE/../work_patches" ] && ROOT="$(cd "$HERE/.." && pwd)"   # script still lives inside the project
if [ -z "$ROOT" ] || [ ! -d "$ROOT/work_patches" ] || [ ! -d "$ROOT/pspoke-patches" ]; then
  echo "ERROR: P3DS_ROOT must point at your clone of 3ds-platinum (the folder containing work_patches/ and pspoke-patches/)." >&2
  echo "##HINT:The project folder is wrong. In Advanced, pick your 3ds-platinum folder (the one that has work_patches inside)."; exit 2
fi
LINK=/workspace/platinum3ds          # project scripts hard-code this path; it is just a symlink to $ROOT
DKP=/opt/devkitpro
PLAT_SHA1=0862ec35b24de5c7e2dcb88c9eea0873110d755c
BASE_ROM="$ROOT/baserom/platinum_us_rev1.nds"
SS_ROM="$ROOT/roms/soulsilver.nds"
OUT3DSX="$ROOT/platinum3ds/full/platinum3ds_full.3dsx"
T="$LINK/pspoke/.work/tree/test_out"
T_ROOT="$ROOT/pspoke/.work/tree/test_out"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  [ok]      %s\n' "$*"; }
miss() { printf '  [MISSING] %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; return 1; }
hint() { printf '##HINT:%s\n' "$*"; }   # one plain-English line the window shows in its result banner

# Patch-set fingerprints: when they change, the step that used them is redone automatically.
pspoke_fp() { cat "$ROOT/pspoke-patches/BASE_COMMIT" "$ROOT"/pspoke-patches/*.patch 2>/dev/null | tr -d '\r' | sha1sum | cut -c1-40; }
PSPOKE_FP_FILE="$ROOT/pspoke/.git/p3ds_pspoke_patches.sha1"
FULL_FP_FILE="$ROOT/pspoke/.work/p3ds_full_build.sha1"

# A Windows git checkout (core.autocrlf) turns every line ending into CRLF, which breaks bash scripts
# ("$'\r': command not found") and patches. Convert the project's text files back to LF; harmless otherwise.
fix_line_endings() {
  local list
  if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    list=$(cd "$ROOT" && git ls-files -z | xargs -0 -r grep -lIZ $'\r' 2>/dev/null | tr '\0' '\n')
  else
    list=$(cd "$ROOT" && find scripts toolchain-3ds work_patches patches pspoke-patches followers platinum3ds forwarder tools \
           -type f -print0 2>/dev/null | xargs -0 -r grep -lIZ $'\r' 2>/dev/null | tr '\0' '\n')
  fi
  [ -n "$list" ] || return 0
  say "Fixing Windows line endings in $(printf '%s\n' "$list" | wc -l) project file(s)..."
  ( cd "$ROOT" && printf '%s\n' "$list" | while IFS= read -r f; do [ -L "$f" ] || sed -i 's/\r$//' "$f"; done )
}

free_gb() { df -Pk "$1" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}'; }

# Plain-English description of a Platinum dump that is not the one we need.
rom_problem() {
  local f="$1" code rev size
  code=$(dd if="$f" bs=1 skip=12 count=4 2>/dev/null | tr -dc 'A-Z0-9')
  rev=$(od -An -tu1 -j30 -N1 "$f" 2>/dev/null | tr -d ' ')
  size=$(stat -c %s "$f" 2>/dev/null || echo 0)
  case "$code" in
    CPUE) ;;
    CPUP|CPUD|CPUF|CPUI|CPUS|CPUJ|CPUK) echo "That is Pokemon Platinum from another region (code $code). You need the US version (CPUE)."; return ;;
    IPGE|IPKE) echo "That is HeartGold/SoulSilver, not Platinum. Pick it in the SoulSilver box instead."; return ;;
    ADAE|APAE) echo "That is Pokemon Diamond/Pearl, not Platinum."; return ;;
    "") echo "That file is not a DS game (.nds)."; return ;;
    *) echo "That is not Pokemon Platinum (game code $code)."; return ;;
  esac
  [ "$rev" = 0 ] && { echo "That is US Platinum Rev 0. The build needs Rev 1 (the later printing), dumped from your own cartridge."; return; }
  [ "$size" -lt 134217728 ] && { echo "That Platinum file is trimmed ($((size/1048576)) MB). Use an untrimmed 128 MB dump."; return; }
  echo "That Platinum file has been changed (patched, or a bad dump). Use a clean, unmodified dump of your cartridge."
}

build_env() {
  export DEVKITPRO=$DKP DEVKITARM=$DKP/devkitARM PSPDEV=$LINK/toolchain-3ds
  export PATH="$LINK/toolchain-3ds/bin:$DKP/devkitARM/bin:$DKP/tools/bin:$PATH"
}

as_root() {   # run a command as root: directly, or via passwordless sudo
  if [ "$(id -u)" -eq 0 ]; then "$@"; elif sudo -n true 2>/dev/null; then sudo -n "$@"; else return 77; fi
}

ensure_link() {
  [ "$ROOT" = "$LINK" ] && return 0
  if [ -L "$LINK" ] && [ "$(readlink -f "$LINK")" = "$(readlink -f "$ROOT")" ]; then return 0; fi
  say "Linking $LINK -> $ROOT"
  as_root mkdir -p /workspace && as_root ln -sfn "$ROOT" "$LINK" && return 0
  die "cannot create $LINK (needs root). Run the 'Install tools' step, or: sudo mkdir -p /workspace && sudo ln -sfn '$ROOT' $LINK"
  hint "A one-time setup step is missing. Open Advanced, press Reinstall tools, then press the button again."
}

# ------------------------------------------------------------------------------------------------ check
step_check() {
  say "Project folder : $ROOT"
  g=$(free_gb "$ROOT"); [ -n "$g" ] && { [ "$g" -ge 10 ] && ok "free disk space ${g} GB" || miss "only ${g} GB free disk space (the full build needs about 10 GB)"; }
  command -v git >/dev/null && ok git || miss "git"
  command -v make >/dev/null && ok make || miss "make"
  command -v patch >/dev/null && ok patch || miss "patch"
  command -v python3 >/dev/null && ok "python3 $(python3 -V 2>&1 | cut -d' ' -f2)" || miss "python3"
  python3 -c 'import ndspy' 2>/dev/null && ok "python ndspy" || miss "python ndspy  (pip install ndspy)"
  python3 -c 'import PIL' 2>/dev/null && ok "python pillow" || miss "python pillow (pip install pillow)"
  [ -x "$DKP/devkitARM/bin/arm-none-eabi-gcc" ] && ok "devkitARM ($("$DKP"/devkitARM/bin/arm-none-eabi-gcc -dumpversion 2>/dev/null))" || miss "devkitARM"
  [ -f "$DKP/libctru/include/3ds.h" ] && ok libctru || miss libctru
  [ -f "$DKP/libctru/include/citro3d.h" ] && ok citro3d || miss citro3d
  [ -x "$DKP/tools/bin/3dsxtool" ] && ok 3dstools || miss "3dstools (3dsxtool)"
  [ -x "$DKP/tools/bin/picasso" ] && ok picasso || miss picasso
  if [ "$ROOT" = "$LINK" ] || { [ -L "$LINK" ] && [ "$(readlink -f "$LINK")" = "$(readlink -f "$ROOT")" ]; }; then ok "$LINK link"; else miss "$LINK link (Install tools creates it)"; fi
  if [ -f "$BASE_ROM" ]; then
    s=$(sha1sum "$BASE_ROM" | cut -d' ' -f1)
    [ "$s" = "$PLAT_SHA1" ] && ok "Platinum ROM staged (SHA1 matches)" || miss "staged Platinum ROM has wrong SHA1 ($s)"
  else miss "Platinum ROM not staged (baserom/platinum_us_rev1.nds)"; fi
  [ -f "$SS_ROM" ] && ok "SoulSilver ROM staged (optional)" || say "  [--]      SoulSilver ROM not staged (optional, only for following Pokemon)"
  if [ -d "$ROOT/pspoke/.git" ]; then
    b=$(git -C "$ROOT/pspoke" rev-parse --abbrev-ref HEAD 2>/dev/null)
    [ "$b" = platinum3ds ] && ok "pspoke on branch platinum3ds" || miss "pspoke is on '$b', expected platinum3ds"
  else miss "pspoke not cloned"; fi
  [ -d "$ROOT/pspoke/.work/tree/test_out" ] && ok "generated tree exists" || miss "generated tree (run Full build)"
  if [ -f "$OUT3DSX" ]; then ok "built: $(ls -l --time-style=long-iso "$OUT3DSX" | awk '{print $5" bytes, "$6" "$7}')"; else say "  [--]      no .3dsx built yet"; fi
  return 0
}

# Which one-time steps "Build and install" still has to run. Prints ##NEED:<step> lines.
step_needs() {
  local tools=ok
  for c in git make patch python3 sha1sum; do command -v $c >/dev/null || tools=missing; done
  python3 -c 'import ndspy, PIL' 2>/dev/null || tools=missing
  for f in "$DKP/devkitARM/bin/arm-none-eabi-gcc" "$DKP/libctru/include/3ds.h" "$DKP/libctru/include/citro3d.h" "$DKP/tools/bin/3dsxtool" "$DKP/tools/bin/picasso"; do
    [ -e "$f" ] || tools=missing
  done
  [ $tools = ok ] || echo "##NEED:setup"
  if [ "$ROOT" != "$LINK" ] && ! { [ -L "$LINK" ] && [ "$(readlink -f "$LINK")" = "$(readlink -f "$ROOT")" ]; }; then echo "##NEED:link"; fi
  # A tree built before this tool kept fingerprints is taken as up to date (no surprise from-scratch rebuild).
  if git -C "$ROOT/pspoke" rev-parse --verify -q platinum3ds >/dev/null 2>&1 && [ ! -f "$PSPOKE_FP_FILE" ]; then pspoke_fp > "$PSPOKE_FP_FILE"; fi
  if [ -d "$T_ROOT" ] && [ ! -f "$FULL_FP_FILE" ]; then pspoke_fp > "$FULL_FP_FILE"; fi
  if [ ! -d "$ROOT/pspoke/.git" ] || [ "$(cat "$PSPOKE_FP_FILE" 2>/dev/null)" != "$(pspoke_fp)" ]; then
    echo "##NEED:pspoke"; echo "##NEED:full"
  elif [ ! -d "$T_ROOT" ] || [ "$(cat "$FULL_FP_FILE" 2>/dev/null)" != "$(pspoke_fp)" ]; then
    echo "##NEED:full"
  fi
  return 0
}

# ------------------------------------------------------------------------------------------------ setup (root)
step_setup() {
  if [ "$(id -u)" -ne 0 ] && ! sudo -n true 2>/dev/null; then
    die "setup needs root. In WSL the GUI runs it as root automatically; on Linux run: sudo $0 setup"; return 1
  fi
  export DEBIAN_FRONTEND=noninteractive
  say "Installing system packages..."
  as_root apt-get update -qq || { hint "Could not download tools. Check that this computer is online, then press the button again."; return 1; }
  as_root apt-get install -y -qq make git patch python3 python3-pip build-essential rsync imagemagick curl wget xz-utils ca-certificates gnupg >/dev/null \
    || { die "apt-get install failed (output above)"; hint "Installing the Linux build tools failed. Check that this computer is online, wait a few minutes (another installer may be running), then press the button again."; return 1; }
  if [ ! -x "$DKP/devkitARM/bin/arm-none-eabi-gcc" ]; then
    say "Installing devkitPro (official installer)..."
    if ! command -v dkp-pacman >/dev/null; then
      tmp=$(mktemp -d); ( cd "$tmp" && wget -q https://apt.devkitpro.org/install-devkitpro-pacman && chmod +x install-devkitpro-pacman \
        && as_root ./install-devkitpro-pacman ) \
        || { die "devkitPro installer failed (output above)"; hint "Could not install devkitPro (the 3DS compiler). Check that this computer is online, then press the button again."; return 1; }
    fi
    as_root dkp-pacman -Sy --noconfirm 3ds-dev \
      || { die "dkp-pacman -S 3ds-dev failed (output above)"; hint "Could not download the 3DS compiler (devkitPro). Its servers may be busy; wait a few minutes and press the button again."; return 1; }
  else ok "devkitARM already installed"; fi
  say "Installing Python modules (ndspy, pillow)..."
  as_root python3 -m pip install -q --break-system-packages ndspy pillow 2>/dev/null || as_root python3 -m pip install -q ndspy pillow \
    || { die "pip install ndspy pillow failed (output above)"; hint "Could not install two small Python modules (ndspy, pillow). Check that this computer is online, then press the button again."; return 1; }
  ensure_link || return 1
  say "Setup done."
}

step_link() { ensure_link && ok "$LINK -> $ROOT"; }

# ------------------------------------------------------------------------------------------------ stage ROMs
step_stage() {
  src="${P3DS_PLATINUM:-}"
  if [ -z "$src" ] && [ -f "$BASE_ROM" ]; then src="$BASE_ROM"; fi
  [ -n "$src" ] && [ -f "$src" ] || { die "no Platinum ROM selected (set it in the builder)"; hint "Pick your Pokemon Platinum .nds file in box 1."; return 1; }
  s=$(sha1sum "$src" | cut -d' ' -f1)
  if [ "$s" != "$PLAT_SHA1" ]; then
    why=$(rom_problem "$src")
    die "Platinum ROM SHA1 is $s, expected $PLAT_SHA1 (US, Rev 1). $why"; hint "$why"; return 1
  fi
  mkdir -p "$ROOT/baserom" "$ROOT/roms"
  [ "$(readlink -f "$src")" = "$(readlink -f "$BASE_ROM")" ] || cp -f "$src" "$BASE_ROM"
  ok "Platinum ROM verified and staged"
  ss="${P3DS_SOULSILVER:-}"
  if [ -n "$ss" ]; then
    [ -f "$ss" ] || { die "SoulSilver file not found: $ss"; hint "The SoulSilver file you picked is gone (moved or renamed). Pick it again, or clear that box (it is optional)."; return 1; }
    code=$(dd if="$ss" bs=1 skip=12 count=4 2>/dev/null)
    [ "$code" = IPGE ] || { die "that is not a US SoulSilver ROM (game code '$code', expected IPGE)"; hint "The SoulSilver file is not US SoulSilver. Clear that box (it is optional) or pick the right file."; return 1; }
    [ "$(readlink -f "$ss")" = "$(readlink -f "$SS_ROM")" ] || cp -f "$ss" "$SS_ROM"
    ok "SoulSilver ROM staged"
  fi
}

# ------------------------------------------------------------------------------------------------ pspoke
step_pspoke() {
  fix_line_endings
  cd "$ROOT" || return 1
  if [ ! -d pspoke/.git ]; then
    rm -rf pspoke.partial
    git clone https://github.com/IbrahimIrfan/pspoke.git pspoke.partial && mv pspoke.partial pspoke \
      || { hint "Could not download pspoke. Check that this computer is online, then press the button again."; return 1; }
  fi
  cd pspoke || return 1
  fp=$(pspoke_fp)
  if git rev-parse --verify -q platinum3ds >/dev/null && [ ! -f "$PSPOKE_FP_FILE" ]; then echo "$fp" > "$PSPOKE_FP_FILE"; fi
  if git rev-parse --verify -q platinum3ds >/dev/null && [ "$(cat "$PSPOKE_FP_FILE" 2>/dev/null)" = "$fp" ]; then
    git checkout -q platinum3ds && ok "pspoke already prepared (branch platinum3ds)"; return 0
  fi
  base=$(tr -d '\r\n ' < ../pspoke-patches/BASE_COMMIT)
  git cat-file -e "$base^{commit}" 2>/dev/null || git fetch -q origin || { hint "Could not update pspoke. Check that this computer is online."; return 1; }
  if git rev-parse --verify -q platinum3ds >/dev/null; then
    say "Your pspoke-patches changed since the last build: re-preparing pspoke with the new set."
    # Anything changed by hand inside pspoke is kept in a git stash, never thrown away.
    git am --abort 2>/dev/null
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
      git stash push -q -m "p3ds-builder backup $(date +%Y%m%d_%H%M%S)" && say "  (saved local pspoke changes in: git -C pspoke stash list)"
    fi
    git checkout -q --detach "$base" && git branch -D platinum3ds >/dev/null || return 1
  fi
  rm -rf port/.p3ds_applied
  git checkout -q -b platinum3ds "$base" || return 1
  if ! git -c user.name=p3ds-builder -c user.email=builder@localhost am -q ../pspoke-patches/*.patch; then
    git am --abort 2>/dev/null; git checkout -q --detach "$base" 2>/dev/null; git branch -D platinum3ds >/dev/null 2>&1
    die "applying pspoke-patches failed (output above)"
    hint "One of your pspoke-patches does not fit pspoke $base. The log names it; re-make it with git format-patch."; return 1
  fi
  echo "$fp" > "$PSPOKE_FP_FILE"
  ok "pspoke checked out and patched ($(ls ../pspoke-patches/*.patch | wc -l) patches)"
}

# ------------------------------------------------------------------------------------------------ full build
step_full() {
  ensure_link || return 1
  [ -f "$BASE_ROM" ] || { die "Platinum ROM not staged - run the 'stage' step"; hint "Pick your Pokemon Platinum .nds file in box 1 and press the button again."; return 1; }
  [ -d "$ROOT/pspoke/.git" ] || { die "pspoke not prepared - run the 'pspoke' step"; hint "The build was started half-way. Press Build and install (it runs every step in order)."; return 1; }
  [ -x "$DKP/devkitARM/bin/arm-none-eabi-gcc" ] || { die "devkitARM missing - run 'setup'"; hint "The 3DS compiler is not installed. Open Advanced, press Reinstall tools, then press the button again."; return 1; }
  fix_line_endings
  g=$(free_gb "$ROOT"); [ -n "$g" ] && [ "$g" -lt 5 ] && { die "only ${g} GB free"; hint "The disk is almost full (${g} GB free). Free up about 10 GB and press the button again."; return 1; }
  build_env; cd "$LINK" || return 1
  say "Full from-scratch build. This takes a long time; the log keeps scrolling while it works."
  bash scripts/rebuild_all.sh || { die "rebuild_all.sh failed - the real error is higher up in the log"
    hint "The long first build stopped. Press the button again once (a dropped download is the usual cause); if it stops at the same place, press 'Copy details for help' and open a GitHub issue."; return 1; }
  pspoke_fp > "$FULL_FP_FILE"
  rm -rf "$LINK/pspoke/.work.bak"
  rm -f "$LINK/pspoke/.work/rebuild_pending.txt"
  ok "full build done (patches and final link follow)"
}

# ------------------------------------------------------------------------------------------------ patches
step_patches() {
  ensure_link || return 1
  [ -d "$ROOT/pspoke/.work/tree/test_out" ] || { die "no generated tree yet - run the 'full' step first"; hint "The game has not had its first full build yet. Press Build and install (not Quick rebuild)."; return 1; }
  args=("$ROOT")
  [ "${P3DS_LEGACY:-0}" = 1 ] && args+=(--legacy)
  fix_line_endings
  python3 -I "$HERE/apply_worktree.py" "${args[@]}"
  rc=$?
  [ $rc -eq 0 ] || { die "some patches could not be installed (list above). Nothing was half-applied."
    hint "A patch does not fit the game source (named in the log). Fix that patch file and press the button again."; return 1; }
}

step_testpatches() {
  [ -d "$ROOT/pspoke/.work/tree/test_out" ] || { die "no generated tree yet - run the full build first"; return 1; }
  args=("$ROOT" --check)
  [ "${P3DS_LEGACY:-0}" = 1 ] && args+=(--legacy)
  fix_line_endings
  python3 -I "$HERE/apply_worktree.py" "${args[@]}" || { hint "Some patches do not fit (named in the log). Nothing was changed."; return 1; }
}

# ------------------------------------------------------------------------------------------------ relink
step_relink() {
  ensure_link || return 1
  build_env
  O="$T/native-audio-app/overlays"
  [ -d "$O" ] || { die "no generated tree yet - run the 'full' step first"; hint "The game has not had its first full build yet. Press Build and install (not Quick rebuild)."; return 1; }
  pend="$LINK/pspoke/.work/rebuild_pending.txt"
  if [ -s "$pend" ]; then
    list=$(grep -v '^__ALL__$' "$pend" | tr '\n' ' ')
    say "Recompiling $(echo $list | wc -w) overlay source file(s)..."
    ( cd "$O" && python3 rebuild_obj.py $list ) || { die "overlay compile failed (error above)"; hint "A patched game file did not compile. The first 'error:' line in the details names the file; if you did not change any patch, open a GitHub issue with the details."; return 1; }
    if grep -q '^__ALL__$' "$pend"; then
      say "Headers changed: refreshing internal overlays..."
      ( cd "$O" && python3 internal.py && cp -f libplatinum-overlays.a libplatinum-overlays.a.base ) || { die "internal overlay step failed"; return 1; }
    fi
    rm -f "$pend"
  fi
  say "Rebuilding native objects..."
  ( cd "$T/native-audio-app" \
    && APP=$(sed -n 's/^APPOBJS:= *//p' "$LINK/platinum3ds/full/Makefile") \
    && make $APP && psp-objcopy --weaken-symbol=getlocalhost offline.o \
    && make -C ../native-render-opt native_gpu.o GPU2D_Soft.o \
    && make -C ../native-stack-render g3_handler.o frontend.o ) || { die "native object build failed (error above)"; hint "Part of the port did not compile. Press the button again once; if it fails the same way, press 'Copy details for help' and open a GitHub issue."; return 1; }
  say "Linking..."
  ( cd "$LINK/platinum3ds/full" && rm -f platinum3ds_full.elf && make ) || { die "link failed (error above)"; hint "The game did not link. The first red 'error' line in the details is the cause."; return 1; }
  [ -f "$OUT3DSX" ] || { die "no .3dsx produced"; return 1; }
  ok "built $(ls -l "$OUT3DSX" | awk '{print $5}') bytes  SHA1 $(sha1sum "$OUT3DSX" | cut -c1-12)"
}

# ------------------------------------------------------------------------------------------------ followers
step_followers() {
  [ -f "$SS_ROM" ] || { say "No SoulSilver ROM staged - skipping followers.bin (optional)."; return 0; }
  python3 -I "$ROOT/followers/extract.py" "$SS_ROM" "$ROOT/platinum3ds/full/followers.bin" || { die "followers extraction failed"
    hint "Making the following-Pokemon sprites from your SoulSilver file failed. Clear the SoulSilver box (it is optional) to build without them."; return 1; }
  ok "followers.bin created"
}

# ------------------------------------------------------------------------------------------------ cia
CIA_BUILT=0
step_cia() {
  ensure_link || return 1
  [ -f "$LINK/platinum3ds/full/platinum3ds_full.elf" ] || { die "nothing linked yet - run the 'relink' step first"; return 1; }
  build_env
  bash "$HERE/build_cia_tools.sh" "$ROOT/build/cia-tools" || { die "could not build makerom/bannertool"; hint "The tools for the HOME Menu (.cia) version could not be built. The .3dsx still works."; return 1; }
  ( cd "$LINK/platinum3ds/full" && make cia BT="$ROOT/build/cia-tools/bin/bannertool" MAKEROM="$ROOT/build/cia-tools/bin/makerom" ) \
    || { die "making the .cia failed (error above)"; hint "The HOME Menu (.cia) version failed. The .3dsx still works. Press 'Copy details for help'."; return 1; }
  [ -f "$ROOT/platinum3ds/full/platinum3ds_full.cia" ] || { die "no .cia produced"; return 1; }
  CIA_BUILT=1
  ok "built $(stat -c %s "$ROOT/platinum3ds/full/platinum3ds_full.cia") bytes platinum3ds_full.cia"
}

# ------------------------------------------------------------------------------------------------ deploy
step_deploy() {
  sd="${P3DS_SD:-}"
  [ -n "$sd" ] || { say "No SD card folder selected - skipping copy. Your build is at: $OUT3DSX"; return 0; }
  [ -d "$sd" ] || { die "SD card folder not found: $sd"; hint "The SD card is not plugged in (or its drive letter changed). Plug it in, pick it in box 2, press the button again."; return 1; }
  [ -f "$OUT3DSX" ] || { die "nothing built yet"; hint "There is no finished game to copy yet. Press Build and install."; return 1; }
  [ -d "$sd/Nintendo 3DS" ] || [ -d "$sd/3ds" ] || say "  [!]       This does not look like a 3DS SD card (no 'Nintendo 3DS' or '3ds' folder). Copying anyway."
  need=$(( $(stat -c %s "$OUT3DSX") + 140000000 )); have=$(df -Pk "$sd" 2>/dev/null | awk 'NR==2 {print $4*1024}')
  [ -n "$have" ] && [ "$have" -lt "$need" ] && { die "not enough space on the SD card"; hint "The SD card is full. Free up about 200 MB on it and press the button again."; return 1; }
  d="$sd/3ds/platinum3ds"
  mkdir -p "$d" 2>/dev/null && [ -w "$d" ] || { die "cannot write to $d"; hint "The SD card is write-protected. Slide the little lock switch on the card up, plug it back in, and try again."; return 1; }
  if [ -f "$d/platinum.sav" ]; then
    mkdir -p "$d/save_backups"; cp -f "$d/platinum.sav" "$d/save_backups/platinum_$(date +%Y%m%d_%H%M%S).sav" && ok "save backed up to save_backups/"
  fi
  cp -f "$OUT3DSX" "$d/platinum3ds_full.3dsx" && sync \
    && [ "$(sha1sum < "$OUT3DSX")" = "$(sha1sum < "$d/platinum3ds_full.3dsx")" ] \
    || { die "copy to the SD card failed or came out different"; hint "Copying to the SD card failed. Re-seat the card and try again; if it keeps failing the card may be damaged."; return 1; }
  [ -f "$BASE_ROM" ] && [ ! -f "$d/platinum.nds" ] && cp -f "$BASE_ROM" "$d/platinum.nds"
  [ -f "$ROOT/platinum3ds/full/followers.bin" ] && cp -f "$ROOT/platinum3ds/full/followers.bin" "$d/followers.bin"
  if [ "$CIA_BUILT" = 1 ]; then
    mkdir -p "$sd/cias" && cp -f "$ROOT/platinum3ds/full/platinum3ds_full.cia" "$sd/cias/platinum3ds.cia" && ok "HOME Menu installer copied to $sd/cias/platinum3ds.cia (install it with FBI)"
  fi
  ok "copied to $d"
  say "Launch it from the Homebrew Launcher on the New 3DS."
  echo "##INSTALLED:$d"
}

# ------------------------------------------------------------------------------------------------ driver
run_step() {
  name="$1"
  case "$name" in
    check|needs|setup|link|stage|pspoke|full|patches|testpatches|relink|followers|cia|deploy) ;;
    *) echo "unknown step: $name" >&2; return 2 ;;
  esac
  echo "##STEP:$name:START"
  if "step_$name"; then echo "##STEP:$name:OK"; return 0; else echo "##STEP:$name:FAIL"; return 1; fi
}

[ $# -gt 0 ] || { sed -n '2,20p' "$0"; exit 2; }
steps=()
for a in "$@"; do
  case "$a" in
    all)   steps+=(check stage pspoke full patches relink followers); [ "${P3DS_CIA:-0}" = 1 ] && steps+=(cia); steps+=(deploy) ;;
    quick) steps+=(patches relink); [ "${P3DS_CIA:-0}" = 1 ] && steps+=(cia); steps+=(deploy) ;;
    *)     steps+=("$a") ;;
  esac
done
for s in "${steps[@]}"; do
  run_step "$s" || { echo "##DONE:FAIL"; exit 1; }
done
echo "##DONE:OK"
