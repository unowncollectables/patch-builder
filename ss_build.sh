#!/usr/bin/env bash
# SoulSilver 3DS build driver (runs inside Linux / WSL2). The GUI calls it; you can also run it by hand:
#   ss_build.sh <step> [<step> ...]
# Steps (each can be run on its own, and each is safe to repeat):
#   check     show what is installed / built
#   needs     print which one-time steps are still missing (##NEED:<step>); used by "Build and install"
#   setup     install tools (apt + devkitPro + pip)  [needs root]
#   stage     check your SoulSilver ROM (it is read in place, never copied into the project)
#   build     compile the game: scripts/ss3ds_build.sh (pspoke at upstream.lock + pspoke-patches/)  - the long one
#   link      link soulsilver3ds.3dsx
#   cia       make the HOME Menu installable soulsilver3ds.cia (builds makerom/bannertool the first time)
#   deploy    copy to the SD card (backs up the save first)
#   all       check stage build link [cia] deploy        quick   link [cia] deploy
# Environment: P3DS_ROOT (Pokemon-Soulsilver-New3ds clone), P3DS_SOULSILVER (path to .nds), P3DS_SD (SD root, optional),
#              P3DS_CIA=1 (also make the .cia)
set -uo pipefail
if [ "${1:-}" = "--env-b64" ]; then eval "$(printf %s "${2:-}" | base64 -d)"; shift 2; fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ "$(ps -o pgid= $$ 2>/dev/null | tr -d ' ')" = "$$" ] && echo $$ > "/tmp/p3ds_builder.$(id -u).pid" 2>/dev/null
ROOT="${P3DS_ROOT:-}"
if [ -z "$ROOT" ] || [ ! -f "$ROOT/upstream.lock" ] || [ ! -d "$ROOT/pspoke-patches" ]; then
  echo "ERROR: P3DS_ROOT must point at your clone of Pokemon-Soulsilver-New3ds (the folder containing upstream.lock)." >&2
  echo "##HINT:The SoulSilver project folder is wrong. In Advanced, pick the Pokemon-Soulsilver-New3ds folder (the one with upstream.lock inside)."; exit 2
fi
DKP=/opt/devkitpro
WANT_SHA1=$(sed -n 's/^soulsilver_sha1=//p' "$ROOT/upstream.lock")
ROM="${P3DS_SOULSILVER:-}"
FULLDIR="$ROOT/soulsilver3ds/full"
OUT3DSX="$FULLDIR/soulsilver3ds.3dsx"
OUTCIA="$FULLDIR/soulsilver3ds.cia"
TREE="$ROOT/pspoke/.work/tree/test_out"
CIA_TOOLS="$ROOT/build/cia-tools"
CIA_BUILT=0

say()  { printf '%s\n' "$*"; }
ok()   { printf '  [ok]      %s\n' "$*"; }
miss() { printf '  [MISSING] %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; return 1; }
hint() { printf '##HINT:%s\n' "$*"; }
free_gb() { df -Pk "$1" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}'; }
as_root() { if [ "$(id -u)" -eq 0 ]; then "$@"; elif sudo -n true 2>/dev/null; then sudo -n "$@"; else return 77; fi; }
build_env() {
  export DEVKITPRO=$DKP DEVKITARM=$DKP/devkitARM
  export PATH="$ROOT/toolchain-3ds/bin:$DKP/devkitARM/bin:$DKP/tools/bin:$PATH"
}

fix_line_endings() {   # a Windows git checkout turns every line ending into CRLF, which breaks bash scripts and patches
  local list
  list=$(cd "$ROOT" && git ls-files -z 2>/dev/null | xargs -0 -r grep -lIZ $'\r' 2>/dev/null | tr '\0' '\n')
  [ -n "$list" ] || return 0
  say "Fixing Windows line endings in $(printf '%s\n' "$list" | wc -l) project file(s)..."
  ( cd "$ROOT" && printf '%s\n' "$list" | while IFS= read -r f; do [ -L "$f" ] || sed -i 's/\r$//' "$f"; done )
}

rom_problem() {
  local code
  code=$(dd if="$1" bs=1 skip=12 count=4 2>/dev/null | tr -dc 'A-Z0-9')
  case "$code" in
    IPGE) echo "" ;;
    IPKE) echo "That is HeartGold, not SoulSilver. This port is SoulSilver (US)." ;;
    IPK*|IPG*) echo "That is HeartGold/SoulSilver from another region (code $code). You need US SoulSilver (IPGE)." ;;
    CPUE|CPU*) echo "That is Pokemon Platinum, not SoulSilver. Put it in the Platinum box." ;;
    "") echo "That file is not a DS game (.nds)." ;;
    *) echo "That is not Pokemon SoulSilver (game code $code)." ;;
  esac
}

# ------------------------------------------------------------------------------------------------ check
step_check() {
  say "SoulSilver project folder : $ROOT"
  g=$(free_gb "$ROOT"); [ -n "$g" ] && { [ "$g" -ge 8 ] && ok "free disk space ${g} GB" || miss "only ${g} GB free disk space (the build needs about 8 GB)"; }
  for c in git make patch rsync curl python3; do command -v $c >/dev/null && ok $c || miss $c; done
  python3 -I -c 'import ndspy' 2>/dev/null && ok "python ndspy" || miss "python ndspy  (pip install ndspy)"
  [ -x "$DKP/devkitARM/bin/arm-none-eabi-gcc" ] && ok "devkitARM" || miss "devkitARM"
  [ -f "$DKP/libctru/include/3ds.h" ] && ok libctru || miss libctru
  [ -f "$DKP/libctru/include/citro3d.h" ] && ok citro3d || miss citro3d
  [ -x "$DKP/tools/bin/3dsxtool" ] && ok 3dstools || miss "3dstools (3dsxtool)"
  [ -x "$DKP/tools/bin/picasso" ] && ok picasso || miss picasso
  if [ -n "$ROM" ] && [ -f "$ROM" ]; then
    s=$(sha1sum "$ROM" | cut -d' ' -f1)
    [ "$s" = "$WANT_SHA1" ] && ok "SoulSilver ROM (SHA1 matches the tested dump)" || say "  [!]       ROM SHA1 $s is not the tested dump ($WANT_SHA1); the build may fail"
  else miss "SoulSilver ROM not selected"; fi
  [ -d "$ROOT/pspoke/.git" ] && ok "pspoke prepared" || say "  [--]      pspoke not downloaded yet (first build does it)"
  [ -d "$TREE" ] && ok "generated tree exists" || say "  [--]      generated tree not built yet (first build is long)"
  if [ -f "$OUT3DSX" ]; then ok "built: $(ls -l --time-style=long-iso "$OUT3DSX" | awk '{print $5" bytes, "$6" "$7}')"; else say "  [--]      no .3dsx built yet"; fi
  return 0
}

step_needs() {
  local tools=ok
  for c in git make patch rsync curl python3 sha1sum; do command -v $c >/dev/null || tools=missing; done
  python3 -I -c 'import ndspy, PIL, numpy' 2>/dev/null || tools=missing
  for f in "$DKP/devkitARM/bin/arm-none-eabi-gcc" "$DKP/libctru/include/3ds.h" "$DKP/libctru/include/citro3d.h" "$DKP/tools/bin/3dsxtool" "$DKP/tools/bin/picasso"; do
    [ -e "$f" ] || tools=missing
  done
  [ $tools = ok ] || echo "##NEED:setup"
  [ -f "$TREE/soulsilver-native-core/nitromain-perf/Makefile" ] || echo "##NEED:full"
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
  as_root apt-get install -y -qq make git patch rsync python3 python3-pip build-essential imagemagick curl wget xz-utils ca-certificates gnupg >/dev/null || return 1
  if [ ! -x "$DKP/devkitARM/bin/arm-none-eabi-gcc" ]; then
    say "Installing devkitPro (official installer)..."
    if ! command -v dkp-pacman >/dev/null; then
      tmp=$(mktemp -d); ( cd "$tmp" && wget -q https://apt.devkitpro.org/install-devkitpro-pacman && chmod +x install-devkitpro-pacman \
        && as_root ./install-devkitpro-pacman ) || return 1
    fi
    as_root dkp-pacman -Sy --noconfirm 3ds-dev || return 1
  else ok "devkitARM already installed"; fi
  say "Installing Python modules (ndspy, pillow, numpy)..."
  as_root python3 -m pip install -q --break-system-packages ndspy pillow numpy 2>/dev/null || as_root python3 -m pip install -q ndspy pillow numpy || return 1
  say "Setup done."
}

# ------------------------------------------------------------------------------------------------ stage
step_stage() {
  [ -n "$ROM" ] && [ -f "$ROM" ] || { die "no SoulSilver ROM selected"; hint "Pick your Pokemon SoulSilver .nds file in the SoulSilver box."; return 1; }
  why=$(rom_problem "$ROM")
  [ -z "$why" ] || { die "$why"; hint "$why"; return 1; }
  s=$(sha1sum "$ROM" | cut -d' ' -f1)
  if [ "$s" = "$WANT_SHA1" ]; then ok "SoulSilver ROM verified (US, SHA1 matches)"
  else say "  [!]       This SoulSilver dump differs from the one the port is tested with (SHA1 $s). Continuing; if a data step fails, use a clean dump of your cartridge."; fi
}

# ------------------------------------------------------------------------------------------------ build / link / cia
step_build() {
  [ -f "$ROM" ] || { die "SoulSilver ROM not selected"; return 1; }
  [ -x "$DKP/devkitARM/bin/arm-none-eabi-gcc" ] || { die "devkitARM missing - run 'setup'"; return 1; }
  fix_line_endings
  g=$(free_gb "$ROOT"); [ -n "$g" ] && [ "$g" -lt 4 ] && { die "only ${g} GB free"; hint "The disk is almost full (${g} GB free). Free up about 8 GB and press the button again."; return 1; }
  build_env; cd "$ROOT" || return 1
  say "Compiling SoulSilver. The first time this takes a long time (it downloads the decompilation); later builds are quick."
  bash scripts/ss3ds_build.sh "$ROM" || { die "scripts/ss3ds_build.sh failed - the real error is higher up in the log"; return 1; }
  ok "game compiled"
}

step_link() {
  [ -d "$TREE" ] || { die "the game is not compiled yet - run the 'build' step first"; return 1; }
  build_env
  ( cd "$FULLDIR" && make check && make soulsilver3ds.3dsx ) || { die "link failed (error above)"; hint "The game did not link. The first red 'error' line in the details is the cause."; return 1; }
  [ -f "$OUT3DSX" ] || { die "no .3dsx produced"; return 1; }
  ok "built $(stat -c %s "$OUT3DSX") bytes  SHA1 $(sha1sum "$OUT3DSX" | cut -c1-12)"
}

step_cia() {
  [ -f "$FULLDIR/soulsilver3ds.elf" ] || { die "nothing linked yet - run the 'link' step first"; return 1; }
  build_env
  ( cd "$FULLDIR" && make cia ) || { die "making the .cia failed (error above)"; hint "The HOME Menu (.cia) version failed. The .3dsx still works. Press 'Copy details for help'."; return 1; }
  [ -f "$OUTCIA" ] || { die "no .cia produced"; return 1; }
  CIA_BUILT=1
  ok "built $(stat -c %s "$OUTCIA") bytes soulsilver3ds.cia"
}

# ------------------------------------------------------------------------------------------------ deploy
step_deploy() {
  sd="${P3DS_SD:-}"
  [ -n "$sd" ] || { say "No SD card folder selected - skipping copy. Your build is at: $OUT3DSX"; return 0; }
  if [ ! -d "$sd" ] || { case "$sd" in /mnt/[a-z]|/mnt/[a-z]/) true ;; *) false ;; esac && [ -z "$(ls -A "$sd" 2>/dev/null)" ]; }; then
    case "$sd" in
      /mnt/[a-z]|/mnt/[a-z]/) L=$(printf %s "$sd" | cut -c6 | tr a-z A-Z)
        die "WSL cannot see drive $L: at $sd (Windows may still see it)"
        hint "Your game is built, but the Linux part of the builder cannot see SD card $L: yet. Open PowerShell and run:  wsl -u root mount -t drvfs $L: /mnt/$(printf %s "$L" | tr A-Z a-z)   then press the button again. (Or restart the PC with the card plugged in.)" ;;
      *) die "SD card folder not found: $sd"
        hint "The SD card folder was not found. Plug the card in, pick it in the SD card box again, press the button again." ;;
    esac
    return 1
  fi
  [ -f "$OUT3DSX" ] || { die "nothing built yet"; return 1; }
  [ -d "$sd/Nintendo 3DS" ] || [ -d "$sd/3ds" ] || say "  [!]       This does not look like a 3DS SD card (no 'Nintendo 3DS' or '3ds' folder). Copying anyway."
  need=$(( $(stat -c %s "$OUT3DSX") + 140000000 )); have=$(df -Pk "$sd" 2>/dev/null | awk 'NR==2 {print $4*1024}')
  [ -n "$have" ] && [ "$have" -lt "$need" ] && { die "not enough space on the SD card"; hint "The SD card is full. Free up about 200 MB on it and press the button again."; return 1; }
  d="$sd/3ds/soulsilver3ds"
  mkdir -p "$d" 2>/dev/null && [ -w "$d" ] || { die "cannot write to $d"; hint "The SD card is write-protected. Slide the little lock switch up, plug it back in, and try again."; return 1; }
  if [ -f "$d/soulsilver.sav" ]; then
    mkdir -p "$d/save_backups"; cp -f "$d/soulsilver.sav" "$d/save_backups/soulsilver_$(date +%Y%m%d_%H%M%S).sav" && ok "save backed up to save_backups/"
  fi
  cp -f "$OUT3DSX" "$d/soulsilver3ds.3dsx" && sync \
    && [ "$(sha1sum < "$OUT3DSX")" = "$(sha1sum < "$d/soulsilver3ds.3dsx")" ] \
    || { die "copy to the SD card failed or came out different"; hint "Copying to the SD card failed. Re-seat the card and try again; if it keeps failing the card may be damaged."; return 1; }
  # Your own ROM, only if there is none on the card yet. The save (soulsilver.sav) is never touched.
  [ -f "$ROM" ] && [ ! -f "$d/soulsilver.nds" ] && cp -f "$ROM" "$d/soulsilver.nds"
  if [ "$CIA_BUILT" = 1 ] && [ -f "$OUTCIA" ]; then
    mkdir -p "$sd/cias" && cp -f "$OUTCIA" "$sd/cias/soulsilver3ds.cia" && ok "HOME Menu installer copied to $sd/cias/soulsilver3ds.cia (install it with FBI)"
  fi
  ok "copied to $d"
  say "Launch it from the Homebrew Launcher on the New 3DS."
  echo "##INSTALLED:$d"
}

# ------------------------------------------------------------------------------------------------ driver
run_step() {
  name="$1"
  case "$name" in
    check|needs|setup|stage|build|link|cia|deploy) ;;
    *) echo "unknown step: $name" >&2; return 2 ;;
  esac
  echo "##STEP:$name:START"
  if "step_$name"; then echo "##STEP:$name:OK"; return 0; else echo "##STEP:$name:FAIL"; return 1; fi
}

[ $# -gt 0 ] || { sed -n '2,18p' "$0"; exit 2; }
steps=()
for a in "$@"; do
  case "$a" in
    all)   steps+=(check stage build link); [ "${P3DS_CIA:-0}" = 1 ] && steps+=(cia); steps+=(deploy) ;;
    quick) steps+=(link); [ "${P3DS_CIA:-0}" = 1 ] && steps+=(cia); steps+=(deploy) ;;
    *)     steps+=("$a") ;;
  esac
done
for s in "${steps[@]}"; do
  run_step "$s" || { echo "##DONE:FAIL"; exit 1; }
done
echo "##DONE:OK"
