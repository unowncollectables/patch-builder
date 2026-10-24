#!/usr/bin/env bash
# Platinum 3DS build driver (runs inside Linux / WSL2). The GUI calls it; you can also run it by hand:
#   p3ds_build.sh <step> [<step> ...]
# Steps (each can be run on its own, and each is safe to repeat):
#   check      show what is installed / staged / built
#   setup      install tools (apt + devkitPro + pip) and link /workspace/platinum3ds  [needs root]
#   link       only create the /workspace/platinum3ds link the project scripts expect  [needs root]
#   stage      copy + verify your ROMs into baserom/ and roms/
#   pspoke     clone pspoke, check out the pinned commit, apply pspoke-patches/
#   full       from-scratch pspoke build (rebuild_all.sh) - the long one
#   patches    apply work_patches/ to the generated tree (idempotent)
#   relink     rebuild changed objects + link the .3dsx  (the quick one after you change code)
#   followers  build followers.bin from your SoulSilver ROM (optional)
#   deploy     copy .3dsx / ROM / followers.bin to the SD card folder (backs up the save first)
#   all        check stage pspoke full patches relink followers deploy
#   quick      patches relink deploy
# Environment: P3DS_ROOT (repo), P3DS_PLATINUM (path to .nds), P3DS_SOULSILVER (optional), P3DS_SD (SD root, optional)
#              P3DS_LEGACY=1 also applies the older patches/*.patch
set -uo pipefail

# The GUI passes its settings as base64-encoded "export ..." lines so no path ever needs shell quoting.
if [ "${1:-}" = "--env-b64" ]; then eval "$(printf %s "${2:-}" | base64 -d)"; shift 2; fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${P3DS_ROOT:-}"
[ -z "$ROOT" ] && [ -d "$HERE/../work_patches" ] && ROOT="$(cd "$HERE/.." && pwd)"   # script still lives inside the project
if [ -z "$ROOT" ] || [ ! -d "$ROOT/work_patches" ] || [ ! -d "$ROOT/pspoke-patches" ]; then
  echo "ERROR: P3DS_ROOT must point at your clone of 3ds-platinum (the folder containing work_patches/ and pspoke-patches/)." >&2; exit 2
fi
LINK=/workspace/platinum3ds          # project scripts hard-code this path; it is just a symlink to $ROOT
DKP=/opt/devkitpro
PLAT_SHA1=0862ec35b24de5c7e2dcb88c9eea0873110d755c
BASE_ROM="$ROOT/baserom/platinum_us_rev1.nds"
SS_ROM="$ROOT/roms/soulsilver.nds"
OUT3DSX="$ROOT/platinum3ds/full/platinum3ds_full.3dsx"
T="$LINK/pspoke/.work/tree/test_out"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  [ok]      %s\n' "$*"; }
miss() { printf '  [MISSING] %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; return 1; }

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
}

# ------------------------------------------------------------------------------------------------ check
step_check() {
  say "Project folder : $ROOT"
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

# ------------------------------------------------------------------------------------------------ setup (root)
step_setup() {
  if [ "$(id -u)" -ne 0 ] && ! sudo -n true 2>/dev/null; then
    die "setup needs root. In WSL the GUI runs it as root automatically; on Linux run: sudo $0 setup"; return 1
  fi
  export DEBIAN_FRONTEND=noninteractive
  say "Installing system packages..."
  as_root apt-get update -qq || return 1
  as_root apt-get install -y -qq make git patch python3 python3-pip imagemagick curl wget xz-utils ca-certificates gnupg >/dev/null || return 1
  if [ ! -x "$DKP/devkitARM/bin/arm-none-eabi-gcc" ]; then
    say "Installing devkitPro (official installer)..."
    if ! command -v dkp-pacman >/dev/null; then
      tmp=$(mktemp -d); ( cd "$tmp" && wget -q https://apt.devkitpro.org/install-devkitpro-pacman && chmod +x install-devkitpro-pacman \
        && as_root ./install-devkitpro-pacman ) || return 1
    fi
    as_root dkp-pacman -Sy --noconfirm 3ds-dev || return 1
  else ok "devkitARM already installed"; fi
  say "Installing Python modules (ndspy, pillow)..."
  as_root python3 -m pip install -q --break-system-packages ndspy pillow 2>/dev/null || as_root python3 -m pip install -q ndspy pillow || return 1
  ensure_link || return 1
  say "Setup done."
}

step_link() { ensure_link && ok "$LINK -> $ROOT"; }

# ------------------------------------------------------------------------------------------------ stage ROMs
step_stage() {
  src="${P3DS_PLATINUM:-}"
  if [ -z "$src" ] && [ -f "$BASE_ROM" ]; then src="$BASE_ROM"; fi
  [ -n "$src" ] && [ -f "$src" ] || { die "no Platinum ROM selected (set it in the builder)"; return 1; }
  s=$(sha1sum "$src" | cut -d' ' -f1)
  if [ "$s" != "$PLAT_SHA1" ]; then
    die "Platinum ROM SHA1 is $s, expected $PLAT_SHA1 (US, Rev 1). The build only works with that exact dump."; return 1
  fi
  mkdir -p "$ROOT/baserom" "$ROOT/roms"
  [ "$(readlink -f "$src")" = "$(readlink -f "$BASE_ROM")" ] || cp -f "$src" "$BASE_ROM"
  ok "Platinum ROM verified and staged"
  ss="${P3DS_SOULSILVER:-}"
  if [ -n "$ss" ]; then
    [ -f "$ss" ] || { die "SoulSilver file not found: $ss"; return 1; }
    code=$(dd if="$ss" bs=1 skip=12 count=4 2>/dev/null)
    [ "$code" = IPGE ] || { die "that is not a US SoulSilver ROM (game code '$code', expected IPGE)"; return 1; }
    [ "$(readlink -f "$ss")" = "$(readlink -f "$SS_ROM")" ] || cp -f "$ss" "$SS_ROM"
    ok "SoulSilver ROM staged"
  fi
}

# ------------------------------------------------------------------------------------------------ pspoke
step_pspoke() {
  cd "$ROOT" || return 1
  [ -d pspoke/.git ] || git clone https://github.com/IbrahimIrfan/pspoke.git pspoke || return 1
  cd pspoke || return 1
  if git rev-parse --verify -q platinum3ds >/dev/null; then
    git checkout -q platinum3ds && ok "pspoke already prepared (branch platinum3ds)"; return 0
  fi
  git checkout -q -b platinum3ds "$(cat ../pspoke-patches/BASE_COMMIT)" || return 1
  if ! git -c user.name=p3ds-builder -c user.email=builder@localhost am ../pspoke-patches/*.patch; then
    git am --abort 2>/dev/null; git checkout -q - 2>/dev/null; git branch -D platinum3ds 2>/dev/null
    die "applying pspoke-patches failed (output above)"; return 1
  fi
  ok "pspoke checked out and patched"
}

# ------------------------------------------------------------------------------------------------ full build
step_full() {
  ensure_link || return 1
  [ -f "$BASE_ROM" ] || { die "Platinum ROM not staged - run the 'stage' step"; return 1; }
  [ -d "$ROOT/pspoke/.git" ] || { die "pspoke not prepared - run the 'pspoke' step"; return 1; }
  [ -x "$DKP/devkitARM/bin/arm-none-eabi-gcc" ] || { die "devkitARM missing - run 'setup'"; return 1; }
  build_env; cd "$LINK" || return 1
  say "Full from-scratch build. This takes a long time; the log keeps scrolling while it works."
  bash scripts/rebuild_all.sh || { die "rebuild_all.sh failed - the real error is higher up in the log"; return 1; }
  rm -rf "$LINK/pspoke/.work.bak"
  rm -f "$LINK/pspoke/.work/rebuild_pending.txt"
  ok "full build done (patches and final link follow)"
}

# ------------------------------------------------------------------------------------------------ patches
step_patches() {
  ensure_link || return 1
  [ -d "$ROOT/pspoke/.work/tree/test_out" ] || { die "no generated tree yet - run the 'full' step first"; return 1; }
  args=("$ROOT")
  [ "${P3DS_LEGACY:-0}" = 1 ] && args+=(--legacy)
  python3 -I "$HERE/apply_worktree.py" "${args[@]}"
  rc=$?
  [ $rc -eq 0 ] || { die "some patches did not apply cleanly (list above). Compare with PROGRESS.md."; return 1; }
}

# ------------------------------------------------------------------------------------------------ relink
step_relink() {
  ensure_link || return 1
  build_env
  O="$T/native-audio-app/overlays"
  [ -d "$O" ] || { die "no generated tree yet - run the 'full' step first"; return 1; }
  pend="$LINK/pspoke/.work/rebuild_pending.txt"
  if [ -s "$pend" ]; then
    list=$(grep -v '^__ALL__$' "$pend" | tr '\n' ' ')
    say "Recompiling $(echo $list | wc -w) overlay source file(s)..."
    ( cd "$O" && python3 rebuild_obj.py $list ) || { die "overlay compile failed (error above)"; return 1; }
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
    && make -C ../native-stack-render g3_handler.o frontend.o ) || { die "native object build failed (error above)"; return 1; }
  say "Linking..."
  ( cd "$LINK/platinum3ds/full" && rm -f platinum3ds_full.elf && make ) || { die "link failed (error above)"; return 1; }
  [ -f "$OUT3DSX" ] || { die "no .3dsx produced"; return 1; }
  ok "built $(ls -l "$OUT3DSX" | awk '{print $5}') bytes  SHA1 $(sha1sum "$OUT3DSX" | cut -c1-12)"
}

# ------------------------------------------------------------------------------------------------ followers
step_followers() {
  [ -f "$SS_ROM" ] || { say "No SoulSilver ROM staged - skipping followers.bin (optional)."; return 0; }
  python3 -I "$ROOT/followers/extract.py" "$SS_ROM" "$ROOT/platinum3ds/full/followers.bin" || { die "followers extraction failed"; return 1; }
  ok "followers.bin created"
}

# ------------------------------------------------------------------------------------------------ deploy
step_deploy() {
  sd="${P3DS_SD:-}"
  [ -n "$sd" ] || { say "No SD card folder selected - skipping copy. Your build is at: $OUT3DSX"; return 0; }
  [ -d "$sd" ] || { die "SD card folder not found: $sd"; return 1; }
  [ -f "$OUT3DSX" ] || { die "nothing built yet"; return 1; }
  d="$sd/3ds/platinum3ds"; mkdir -p "$d" || return 1
  if [ -f "$d/platinum.sav" ]; then
    mkdir -p "$d/save_backups"; cp -f "$d/platinum.sav" "$d/save_backups/platinum_$(date +%Y%m%d_%H%M%S).sav" && ok "save backed up to save_backups/"
  fi
  cp -f "$OUT3DSX" "$d/platinum3ds_full.3dsx" || return 1
  [ -f "$BASE_ROM" ] && [ ! -f "$d/platinum.nds" ] && cp -f "$BASE_ROM" "$d/platinum.nds"
  [ -f "$ROOT/platinum3ds/full/followers.bin" ] && cp -f "$ROOT/platinum3ds/full/followers.bin" "$d/followers.bin"
  ok "copied to $d"
  say "Launch it from the Homebrew Launcher on the New 3DS."
}

# ------------------------------------------------------------------------------------------------ driver
run_step() {
  name="$1"
  case "$name" in
    check|setup|link|stage|pspoke|full|patches|relink|followers|deploy) ;;
    *) echo "unknown step: $name" >&2; return 2 ;;
  esac
  echo "##STEP:$name:START"
  if "step_$name"; then echo "##STEP:$name:OK"; return 0; else echo "##STEP:$name:FAIL"; return 1; fi
}

[ $# -gt 0 ] || { sed -n '2,20p' "$0"; exit 2; }
steps=()
for a in "$@"; do
  case "$a" in
    all)   steps+=(check stage pspoke full patches relink followers deploy) ;;
    quick) steps+=(patches relink deploy) ;;
    *)     steps+=("$a") ;;
  esac
done
for s in "${steps[@]}"; do
  run_step "$s" || { echo "##DONE:FAIL"; exit 1; }
done
echo "##DONE:OK"
