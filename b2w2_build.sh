#!/usr/bin/env bash
# Black 2 / White 2 3DS build driver (runs inside Linux / WSL2). The GUI calls it; by hand:
#   P3DS_B2W2_GAME=b2|w2 b2w2_build.sh <step> [<step> ...]
# Steps (each can be run on its own, and each is safe to repeat):
#   check     show what is installed / built
#   needs     print which one-time steps are still missing (##NEED:<step>); used by "Build and install"
#   setup     install tools (apt + devkitPro + ninja)  [needs root]
#   stage     check your Black 2 or White 2 ROM (it is read in place, never copied into the project)
#   build     extract NitroFS/overlays from the ROM, then PBW2_LINK_GAME=1 make -C n3ds b2|w2
#   cia       make the HOME Menu installer with the port's New 3DS RSF (804 MHz, L2, core 2)
#   deploy    copy the .3dsx, nitrofs/ and overlays/ (and the .cia if built) to the SD card
#   all       check stage build [cia] deploy        quick   build [cia] deploy
# Environment: P3DS_ROOT (Pokemon-Black2-White2-New3ds clone), P3DS_B2W2_GAME=b2|w2,
#              P3DS_BLACK2 / P3DS_WHITE2 (path to the matching .nds), P3DS_SD (optional), P3DS_CIA=1.
#              P3DS_FAN_PACK (optional): the fan overworld sprite pack (.rar/.zip/folder) for the
#              credited fan-tier follower sprites; the build step converts it (allowlisted sheets
#              only, see fan_sprites.py) into followers_fan.bin + FOLLOWER_CREDITS.txt in the staged
#              game folder. Without it, any stale fan files are removed and the build is unchanged.
# Black 2 and White 2 are two outputs of the same source tree. The ROM is never embedded in the
# installer or copied to the card; the game reads staged nitrofs/ and overlays/. No .sav is created.
set -uo pipefail
if [ "${1:-}" = "--env-b64" ]; then eval "$(printf %s "${2:-}" | base64 -d)"; shift 2; fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/setup_build_tools.sh" || {
  echo "##HINT:The build tools setup script is missing. Download the builder again and retry."
  exit 1
}
[ "$(ps -o pgid= $$ 2>/dev/null | tr -d ' ')" = "$$" ] && echo $$ > "/tmp/p3ds_builder.$(id -u).pid" 2>/dev/null
ROOT="${P3DS_ROOT:-}"
if [ -z "$ROOT" ] || [ ! -f "$ROOT/upstream.lock" ] || [ ! -f "$ROOT/n3ds/Makefile" ] || [ ! -f "$ROOT/scripts/n3ds_build.sh" ]; then
  echo "ERROR: P3DS_ROOT must point at your clone of Pokemon-Black2-White2-New3ds (the folder containing n3ds/Makefile)." >&2
  echo "##HINT:The Black 2 / White 2 project folder is wrong. In Advanced, pick the Pokemon-Black2-White2-New3ds folder (the one with n3ds and upstream.lock inside)."; exit 2
fi

case "${P3DS_B2W2_GAME:-}" in
  b2|black2)  SLOT=b2; TITLE="Black 2"; CODE=IREO; ROM="${P3DS_BLACK2:-}"; TARGET=black2_3ds; MAKE_T=b2; LOCK_KEY=black2_us_sha1; BASEROM=baserom_b2_us.nds; RSF=black2.rsf; FOLDER=black2_3ds; SAV=black2.sav; OUT_NAME="Black 2"; TITLE_ID=000400000F7D4200 ;;
  w2|white2)  SLOT=w2; TITLE="White 2"; CODE=IRDO; ROM="${P3DS_WHITE2:-}"; TARGET=white2_3ds; MAKE_T=w2; LOCK_KEY=white2_us_sha1; BASEROM=baserom_w2_us.nds; RSF=white2.rsf; FOLDER=white2_3ds; SAV=white2.sav; OUT_NAME="White 2"; TITLE_ID=000400000F7D4300 ;;
  *) echo "ERROR: set P3DS_B2W2_GAME=b2 or w2." >&2
     echo "##HINT:This build script needs to know whether to build Black 2 or White 2. In the builder, tick one of those games and pick its .nds file."; exit 2 ;;
esac

DKP=/opt/devkitpro
WANT_SHA1=$(sed -n "s/^${LOCK_KEY}=//p" "$ROOT/upstream.lock")
[ -n "$WANT_SHA1" ] || case "$SLOT" in
  b2) WANT_SHA1=e51e6dfb8678a3d19dcd2a10691b96a569ca0abb ;;
  w2) WANT_SHA1=b5d7490be7b415b8f1e672a53e978a9cc667e56a ;;
esac
EXTRACT="$ROOT/extract/${SLOT}_us"
OUT3DSX="$ROOT/n3ds/build/$SLOT/${TARGET}.3dsx"
OUTELF="$ROOT/n3ds/build/$SLOT/${TARGET}.elf"
OUTCIA="$ROOT/n3ds/build/$SLOT/${TARGET}.cia"
CIA_TOOLS="$ROOT/build/cia-tools"
# Staged nitrofs/overlays live outside the git worktree (the port script refuses to write inside it).
STAGE_ROOT="${P3DS_STAGE:-$HOME/pbw2-sd}"
STAGED="$STAGE_ROOT/3ds/$FOLDER"
CIA_BUILT=0

say()  { printf '%s\n' "$*"; }
ok()   { printf '  [ok]      %s\n' "$*"; }
miss() { printf '  [MISSING] %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; return 1; }
hint() { printf '##HINT:%s\n' "$*"; }
free_gb() { df -Pk "$1" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}'; }
as_root() { if [ "$(id -u)" -eq 0 ]; then "$@"; elif sudo -n true 2>/dev/null; then sudo -n "$@"; else return 77; fi; }
have_ninja() { command -v ninja >/dev/null; }

build_env() {
  export DEVKITPRO=$DKP DEVKITARM=$DKP/devkitARM
  export PATH="$DKP/devkitARM/bin:$DKP/tools/bin:$PATH"
  if [ -z "${MAKEFLAGS:-}" ]; then
    local j="${P3DS_JOBS:-}" m
    if [ -z "$j" ]; then
      j=$(nproc 2>/dev/null || echo 2)
      m=$(awk '/MemAvailable/ {printf "%d", $2/1048576}' /proc/meminfo 2>/dev/null)
      [ -n "$m" ] && [ "$m" -ge 1 ] && [ "$m" -lt "$j" ] && j=$m
      [ "$j" -gt 12 ] && j=12
    fi
    [ "$j" -ge 1 ] 2>/dev/null || j=1
    export MAKEFLAGS="-j$j"
  fi
}

rom_sha1() {
  local f="$1" key c="$ROOT/build/.rom_sha1_$SLOT"
  key="$(stat -c '%s %Y' "$f" 2>/dev/null)|$f"
  if [ -f "$c" ] && [ "$(sed -n 1p "$c")" = "$key" ]; then sed -n 2p "$c"; return; fi
  local s; s=$(sha1sum "$f" | cut -d' ' -f1)
  mkdir -p "$ROOT/build" 2>/dev/null && printf '%s\n%s\n' "$key" "$s" > "$c" 2>/dev/null
  printf '%s\n' "$s"
}

fix_line_endings() {
  local list head stamp="$ROOT/build/.eol_checked"
  head=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null)
  if [ -n "$head" ] && [ "$(cat "$stamp" 2>/dev/null)" = "$head" ] && [ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no 2>/dev/null | head -1)" ]; then return 0; fi
  list=$(cd "$ROOT" && git ls-files -z 2>/dev/null | xargs -0 -r grep -lIZ $'\r' 2>/dev/null | tr '\0' '\n')
  if [ -z "$list" ]; then
    [ -n "$head" ] && mkdir -p "$ROOT/build" 2>/dev/null && printf '%s\n' "$head" > "$stamp" 2>/dev/null
    return 0
  fi
  say "Fixing Windows line endings in $(printf '%s\n' "$list" | wc -l) project file(s)..."
  ( cd "$ROOT" && printf '%s\n' "$list" | while IFS= read -r f; do [ -L "$f" ] || sed -i 's/\r$//' "$f"; done )
}

rom_problem() {
  local code
  code=$(dd if="$1" bs=1 skip=12 count=4 2>/dev/null | tr -dc 'A-Z0-9')
  case "$SLOT" in
    b2)
      case "$code" in
        IREO) echo "" ;;
        IRDO) echo "That is White 2, not Black 2. Put it in the White 2 box." ;;
        IRE*) echo "That is Pokemon Black 2 from another region (code $code). You need US Black 2 (IREO)." ;;
        IRD*) echo "That is Pokemon White 2 from another region (code $code). Put a US White 2 file in the White 2 box, or a US Black 2 file here." ;;
        IRBO|IRBJ|IRBP|IRBK) echo "That is Pokemon Black, not Black 2." ;;
        IRAO|IRAJ|IRAP|IRAK) echo "That is Pokemon White, not Black 2." ;;
        CPUE|CPU*) echo "That is Pokemon Platinum, not Black 2. Put it in the Platinum box." ;;
        IPGE|IPG*|IPKE|IPK*) echo "That is HeartGold/SoulSilver, not Black 2. Put it in the SoulSilver or HeartGold box." ;;
        "") echo "That file is not a DS game (.nds)." ;;
        *) echo "That is not Pokemon Black 2 (game code $code)." ;;
      esac ;;
    w2)
      case "$code" in
        IRDO) echo "" ;;
        IREO) echo "That is Black 2, not White 2. Put it in the Black 2 box." ;;
        IRD*) echo "That is Pokemon White 2 from another region (code $code). You need US White 2 (IRDO)." ;;
        IRE*) echo "That is Pokemon Black 2 from another region (code $code). Put a US Black 2 file in the Black 2 box, or a US White 2 file here." ;;
        IRBO|IRBJ|IRBP|IRBK) echo "That is Pokemon Black, not White 2." ;;
        IRAO|IRAJ|IRAP|IRAK) echo "That is Pokemon White, not White 2." ;;
        CPUE|CPU*) echo "That is Pokemon Platinum, not White 2. Put it in the Platinum box." ;;
        IPGE|IPG*|IPKE|IPK*) echo "That is HeartGold/SoulSilver, not White 2. Put it in the SoulSilver or HeartGold box." ;;
        "") echo "That file is not a DS game (.nds)." ;;
        *) echo "That is not Pokemon White 2 (game code $code)." ;;
      esac ;;
  esac
}

place_rom() {
  # Point orig/baserom_*.nds at the user's file without baking a ROM into the project.
  # orig/*.nds is gitignored. Prefer a symlink; copy only if the filesystem cannot link.
  mkdir -p "$ROOT/orig"
  local dest="$ROOT/orig/$BASEROM"
  if [ -L "$dest" ] || [ -f "$dest" ]; then
    local cur
    cur=$(readlink -f "$dest" 2>/dev/null || true)
    local want
    want=$(readlink -f "$ROM" 2>/dev/null || true)
    if [ -n "$cur" ] && [ "$cur" = "$want" ]; then return 0; fi
    rm -f "$dest"
  fi
  if ln -s "$ROM" "$dest" 2>/dev/null; then
    ok "using your $TITLE file in place (not copied into the project)"
  else
    say "  [--]      copying the ROM into orig/ (this folder is gitignored; the file is not committed)"
    cp -f "$ROM" "$dest" || { die "could not place the ROM for extract"; return 1; }
  fi
}

extract_and_stage() {
  # Extract only (not the full matching MWCC rebuild), then stage nitrofs + overlays outside the repo.
  place_rom || return 1
  command -v ninja >/dev/null || { die "ninja is missing - run 'setup'"; hint "The extract tool (ninja) is missing. Open Troubleshooting and choose Repair tools, then press the button again."; return 1; }
  say "Extracting $TITLE data from your ROM (the ROM stays where you picked it; nothing is committed)..."
  ( cd "$ROOT" && python3 configure.py "$SLOT"_us ) || { die "configure.py failed (error above)"; hint "The $TITLE project could not prepare a ROM extract. Check the details; if it mentions a missing tool, open Troubleshooting and choose Repair tools."; return 1; }
  ( cd "$ROOT" && ninja "extract/${SLOT}_us/config.yaml" ) || { die "extracting the ROM failed (error above)"; hint "Could not read the data out of your $TITLE file. Use a clean US dump and press the button again."; return 1; }
  [ -d "$EXTRACT/files/a" ] || { die "extract produced no NitroFS files"; return 1; }
  [ -d "$EXTRACT/arm9_overlays" ] || { die "extract produced no overlays"; return 1; }
  bash "$ROOT/scripts/n3ds_stage_nitrofs.sh" "$SLOT" "$EXTRACT/files" "$STAGE_ROOT" || {
    die "staging NitroFS failed"
    hint "Could not stage the game data for the SD card. Check free disk space and press the button again."
    return 1
  }
  mkdir -p "$STAGED/overlays"
  cp -f "$EXTRACT/arm9_overlays"/ov*.bin "$STAGED/overlays/" || { die "could not stage overlays"; return 1; }
  python3 - "$ROM" "$STAGED" <<'PY'
import struct, sys
rom, staged = sys.argv[1], sys.argv[2]
with open(rom, "rb") as f:
    data = f.read()
head = data[:0x200]
open(staged + "/rom_header.bin", "wb").write(head)
off, size = struct.unpack_from("<II", head, 0x50)
if not (0 <= off <= off + size <= len(data)):
    sys.exit("ROM overlay table is out of range")
open(staged + "/overlays/arm9_ovt.bin", "wb").write(data[off:off + size])
print("arm9_ovt.bin %d bytes, %d overlays" % (size, len(__import__("glob").glob(staged + "/overlays/ov*.bin"))))
PY
  [ -f "$STAGED/overlays/arm9_ovt.bin" ] && [ -f "$STAGED/overlays/ov012.bin" ] || {
    die "overlays were not staged (need arm9_ovt.bin and ov012.bin at least)"
    return 1
  }
  ok "staged nitrofs and overlays at $STAGED"
}

# ------------------------------------------------------------------------------------------------ check
step_check() {
  say "$TITLE project folder : $ROOT"
  g=$(free_gb "$ROOT"); [ -n "$g" ] && { [ "$g" -ge 8 ] && ok "free disk space ${g} GB" || miss "only ${g} GB free disk space (the build needs about 8 GB)"; }
  for c in git make python3 sha1sum ninja; do command -v $c >/dev/null && ok $c || miss $c; done
  [ -x "$DKP/devkitARM/bin/arm-none-eabi-gcc" ] && ok "devkitARM" || miss "devkitARM"
  [ -f "$DKP/libctru/include/3ds.h" ] && ok libctru || miss libctru
  [ -f "$DKP/libctru/include/citro3d.h" ] && ok citro3d || miss citro3d
  [ -x "$DKP/tools/bin/3dsxtool" ] && ok 3dstools || miss "3dstools (3dsxtool)"
  if [ -n "$ROM" ] && [ -f "$ROM" ]; then
    s=$(rom_sha1 "$ROM")
    [ "$s" = "$WANT_SHA1" ] && ok "$TITLE ROM (SHA1 matches the tested US dump)" || say "  [!]       ROM SHA1 $s is not the tested dump ($WANT_SHA1); the build will stop"
  else miss "$TITLE ROM not selected"; fi
  if [ -d "$STAGED/nitrofs/a" ] && [ -f "$STAGED/overlays/arm9_ovt.bin" ]; then ok "staged data: $STAGED"; else say "  [--]      game data not staged yet (first build does it)"; fi
  if [ -f "$OUT3DSX" ]; then ok "built: $(ls -l --time-style=long-iso "$OUT3DSX" | awk '{print $5" bytes, "$6" "$7}')"; else say "  [--]      no .3dsx built yet"; fi
  return 0
}

step_needs() {
  local tools=ok
  for c in git make python3 sha1sum ninja clang llvm-objcopy; do command -v $c >/dev/null || tools=missing; done
  devkitpro_ready "$DKP" || tools=missing
  [ $tools = ok ] || echo "##NEED:setup"
  return 0
}

# ------------------------------------------------------------------------------------------------ setup (root)
step_setup() {
  setup_build_tools || return 1
  if ! have_ninja; then
    say "Installing ninja..."
    as_root env DEBIAN_FRONTEND=noninteractive apt-get update -qq &&
      as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ninja-build >/dev/null || {
      die "Could not install ninja (see the apt output above)."
      hint "Check your internet connection, then retry setup."
      return 1
    }
  fi
  have_ninja && ok "ninja" || { die "ninja is still missing after setup"; return 1; }
  # A RAR reader is only needed for the optional follower sprite pack; best effort.
  if ! command -v unar >/dev/null && ! command -v unrar >/dev/null && ! command -v 7z >/dev/null; then
    say "Installing unar (only needed if you use the follower sprite pack)..."
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq unar >/dev/null 2>&1 \
      && ok "unar" || say "  [--]      unar not installed; the follower sprite pack can still be supplied as a .zip or a folder"
  fi
  say "Setup done."
}

# ------------------------------------------------------------------------------------------------ stage
step_stage() {
  [ -n "$ROM" ] && [ -f "$ROM" ] || { die "no $TITLE ROM selected"; hint "Pick your Pokemon $TITLE .nds file in the $TITLE box."; return 1; }
  why=$(rom_problem "$ROM")
  [ -z "$why" ] || { die "$why"; hint "$why"; return 1; }
  s=$(rom_sha1 "$ROM")
  if [ "$s" != "$WANT_SHA1" ]; then
    die "$TITLE ROM SHA1 is $s, expected $WANT_SHA1 (US)."
    hint "This $TITLE file does not match the clean US dump the build needs. Dump your cartridge again."
    return 1
  fi
  ok "$TITLE ROM verified (US, SHA1 matches)"
}

# ------------------------------------------------------------------------------------------------ fan sprites
# Optional credited fan-tier follower sprites (fan_sprites.py). With P3DS_FAN_PACK set, convert the
# user's pack into the staged game folder; without it, remove any stale fan files so the staged
# output is exactly what a build before this feature produced.
fansprites_convert() {
  local bin="$STAGED/followers_fan.bin" credits="$STAGED/FOLLOWER_CREDITS.txt"
  if [ -n "${P3DS_FAN_PACK:-}" ]; then
    [ -e "$P3DS_FAN_PACK" ] || { die "follower sprite pack not found: $P3DS_FAN_PACK"; hint "The follower sprite pack file could not be found. Pick it again in Advanced, or clear the field to build without it."; return 1; }
    say "Making the credited fan-tier follower sprites from your sprite pack (only the sheets the artists allow are used)..."
    python3 "$HERE/fan_sprites.py" --pack "$P3DS_FAN_PACK" --out "$STAGED" || {
      die "the follower sprite pack could not be used (the message above says why)"
      hint "The follower sprite pack could not be converted. Check the message above it; clear the pack field in Advanced to build without the fan-tier sprites."
      return 1
    }
    ok "fan-tier follower sprites staged (full credits in FOLLOWER_CREDITS.txt)"
  else
    rm -f "$bin" "$credits"
  fi
}

# ------------------------------------------------------------------------------------------------ build / cia
step_build() {
  [ -f "$ROM" ] || { die "$TITLE ROM not selected"; return 1; }
  [ -x "$DKP/devkitARM/bin/arm-none-eabi-gcc" ] || { die "devkitARM missing - run 'setup'"; return 1; }
  have_ninja || { die "ninja is missing - run 'setup'"; hint "The extract tool (ninja) is missing. Open Troubleshooting and choose Repair tools, then press the button again."; return 1; }
  fix_line_endings
  g=$(free_gb "$ROOT"); [ -n "$g" ] && [ "$g" -lt 4 ] && { die "only ${g} GB free"; hint "The disk is almost full (${g} GB free). Free up about 8 GB and press the button again."; return 1; }
  extract_and_stage || return 1
  fansprites_convert || return 1
  build_env
  say "Compiling the $TITLE New 3DS port (LINK_GAME). The first time this can take a while; later builds are quicker."
  say "Project version: $(git -C "$ROOT" log -1 --format='commit %h (%cs): %s' 2>/dev/null)"
  say "This port currently reaches the title screen on a New 3DS. PRESS START text and 3D Kyurem are still unfinished."
  rm -f "$OUT3DSX" "$OUTELF" "$OUTCIA"
  ( cd "$ROOT" && PBW2_LINK_GAME=1 make -C n3ds "$MAKE_T" ) || {
    die "make -C n3ds $MAKE_T failed - the real error is higher up in the log"
    hint "The $TITLE port did not compile. The first red 'error' line in the details is the cause."
    return 1
  }
  [ -f "$OUT3DSX" ] || { die "no .3dsx produced"; return 1; }
  ok "built $(stat -c %s "$OUT3DSX") bytes  SHA1 $(sha1sum "$OUT3DSX" | cut -c1-12)"
}

step_cia() {
  [ -f "$OUTELF" ] || { die "nothing built yet - run the 'build' step first"; return 1; }
  [ -f "$ROOT/n3ds/cia/$RSF" ] || { die "missing $ROOT/n3ds/cia/$RSF"; return 1; }
  bash "$HERE/build_cia_tools.sh" "$CIA_TOOLS" || {
    die "could not build makerom and bannertool"
    hint "The HOME Menu tools could not be built. Uncheck the HOME Menu installer to build the Homebrew Launcher version."
    return 1
  }
  local makerom bannertool art
  makerom="$CIA_TOOLS/bin/makerom"
  [ -x "$makerom" ] || makerom="$CIA_TOOLS/bin/makerom.exe"
  [ -x "$makerom" ] || { die "makerom is missing after the tools build"; return 1; }
  bannertool="$CIA_TOOLS/bin/bannertool"
  [ -x "$bannertool" ] || bannertool="$CIA_TOOLS/bin/bannertool.exe"
  [ -x "$bannertool" ] || { die "bannertool is missing after the tools build"; return 1; }
  art="$ROOT/n3ds/build/$SLOT/home-menu"
  rm -f "$OUTCIA"
  python3 "$ROOT/scripts/n3ds_home_menu_art.py" "$ROM" "$art" --game "$TITLE" --bannertool "$bannertool" || {
    die "could not prepare the HOME Menu icon and banner"
    return 1
  }
  # The port's RSF already requests 804 MHz, L2, and core 2. No ROM is packed into the CIA; data stays on the SD card.
  "$makerom" -f cia -o "$OUTCIA" -elf "$OUTELF" -rsf "$ROOT/n3ds/cia/$RSF" \
    -icon "$art/icon.smdh" -banner "$art/banner.bnr" -target t -exefslogo || {
    die "making the .cia failed (error above)"
    hint "The HOME Menu (.cia) version failed. The .3dsx still works. Uncheck the HOME Menu installer, or press 'Copy details for help'."
    rm -f "$OUTCIA"
    return 1
  }
  [ -f "$OUTCIA" ] || { die "no .cia produced"; return 1; }
  CIA_BUILT=1
  ok "built $(stat -c %s "$OUTCIA") bytes ${TARGET}.cia  (title $TITLE_ID, New 3DS 804 MHz / L2 / core 2)"
}

# ------------------------------------------------------------------------------------------------ export
# Copies the finished .3dsx / .cia plus staged nitrofs/overlays to a normal folder
# (P3DS_OUT, e.g. Documents\Pokemon 3DS Builds\Black 2\). No ROM or save is copied there.
step_export() {
  [ -n "${P3DS_OUT:-}" ] || return 0
  [ -f "$OUT3DSX" ] || return 0
  local o="$P3DS_OUT/$OUT_NAME"
  mkdir -p "$o" 2>/dev/null && [ -w "$o" ] || { die "cannot write to the output folder $o"; hint "Could not save the finished game in your output folder. Pick another folder in 'Output folder' and press the button again."; return 1; }
  cp -f "$OUT3DSX" "$o/${TARGET}.3dsx" || { die "copy to the output folder failed"; hint "Could not save the finished game in your output folder. Check there is free space, then try again."; return 1; }
  if [ -f "$OUTCIA" ] && [ "${P3DS_CIA:-0}" = 1 ]; then
    cp -f "$OUTCIA" "$o/${TARGET}.cia" || {
      die "could not save the HOME Menu installer"
      hint "Check the free space and permissions on the output folder, then retry the build."
      return 1
    }
  fi
  if [ -d "$STAGED/nitrofs" ]; then
    mkdir -p "$o/nitrofs" "$o/overlays"
    cp -a "$STAGED/nitrofs"/. "$o/nitrofs/" || { die "could not save staged nitrofs"; return 1; }
    cp -a "$STAGED/overlays"/. "$o/overlays/" || { die "could not save staged overlays"; return 1; }
    [ -f "$STAGED/rom_header.bin" ] && cp -f "$STAGED/rom_header.bin" "$o/rom_header.bin"
    [ -f "$STAGED/followers_fan.bin" ] && cp -f "$STAGED/followers_fan.bin" "$o/followers_fan.bin"
    [ -f "$STAGED/FOLLOWER_CREDITS.txt" ] && cp -f "$STAGED/FOLLOWER_CREDITS.txt" "$o/FOLLOWER_CREDITS.txt"
  fi
  cat > "$o/How to put this on your 3DS.txt" <<TXT
How to put this on your 3DS
===========================
Pokemon $TITLE for New Nintendo 3DS (title $TITLE_ID).
This is an early port: it currently reaches the title screen. PRESS START text
and 3D Kyurem are still unfinished. It will not run on an original 3DS.

1. Put your New 3DS SD card in this computer.
2. Homebrew Launcher version: copy ${TARGET}.3dsx, the nitrofs folder, and the
   overlays folder to  SD:\\3ds\\${FOLDER}\\
   so the card has:
     SD:\\3ds\\${FOLDER}\\${TARGET}.3dsx
     SD:\\3ds\\${FOLDER}\\nitrofs\\
     SD:\\3ds\\${FOLDER}\\overlays\\
   Start it from the Homebrew Launcher. Do not put your .nds file on the card;
   the game reads nitrofs and overlays instead. This tool never writes a save.
3. HOME Menu version (optional): copy ${TARGET}.cia to  SD:\\cias\\  then on the
   3DS open FBI > SD > cias > ${TARGET}.cia > Install. The CIA still needs
   nitrofs and overlays in SD:\\3ds\\${FOLDER}\\.
TXT
  printf '\nBuilt version: %s\n' "$(git -C "$ROOT" log -1 --format='commit %h (%cs): %s' 2>/dev/null)" >> "$o/How to put this on your 3DS.txt"
  if [ -f "$o/followers_fan.bin" ]; then
    printf 'This build also has followers_fan.bin: copy it into the same SD:\\3ds\\%s\\ folder.\nIt holds the credited fan-made follower sprites; FOLLOWER_CREDITS.txt lists their artists.\n' "$FOLDER" >> "$o/How to put this on your 3DS.txt"
  fi
  sed -i 's/$/\r/' "$o/How to put this on your 3DS.txt" 2>/dev/null
  ok "saved to your output folder ($OUT_NAME)"
  echo "##OUTPUT:$OUT_NAME"
}

# ------------------------------------------------------------------------------------------------ deploy
step_deploy() {
  step_export || return 1
  sd="${P3DS_SD:-}"
  [ -n "$sd" ] || [ "${P3DS_SD_WANTED:-}" != 1 ] || { die "an SD card was picked but its path did not reach the build"; hint "The SD card you picked could not be passed to the builder. Pick it again with Browse and press the button again."; return 1; }
  [ -n "$sd" ] || { [ -n "${P3DS_OUT:-}" ] || say "No SD card picked, so nothing was copied to a card."; return 0; }
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
  [ -d "$STAGED/nitrofs/a" ] || { die "game data was not staged"; hint "The $TITLE data folders were not prepared. Press the button again so the build can extract them from your ROM."; return 1; }
  [ -d "$sd/Nintendo 3DS" ] || [ -d "$sd/3ds" ] || say "  [!]       This does not look like a 3DS SD card (no 'Nintendo 3DS' or '3ds' folder). Copying anyway."
  local d="$sd/3ds/$FOLDER"
  mkdir -p "$d"
  # Back up a save if one is already there. Never create or overwrite a .sav.
  if [ -f "$d/$SAV" ]; then
    mkdir -p "$d/save_backups"; cp -f "$d/$SAV" "$d/save_backups/${SAV%.sav}_$(date +%Y%m%d_%H%M%S).sav" && ok "save backed up to save_backups/"
  fi
  [ -f "$d/${TARGET}.3dsx" ] && cp -f "$d/${TARGET}.3dsx" "$d/${TARGET}.3dsx.prev"
  cp -f "$OUT3DSX" "$d/${TARGET}.3dsx" && sync \
    && [ "$(sha1sum < "$OUT3DSX")" = "$(sha1sum < "$d/${TARGET}.3dsx")" ] \
    || { die "copy to the SD card failed or came out different"; hint "Copying to the SD card failed. Re-seat the card and try again; if it keeps failing the card may be damaged."; return 1; }
  mkdir -p "$d/nitrofs" "$d/overlays"
  cp -a "$STAGED/nitrofs"/. "$d/nitrofs/" || { die "could not copy nitrofs to the SD card"; return 1; }
  cp -a "$STAGED/overlays"/. "$d/overlays/" || { die "could not copy overlays to the SD card"; return 1; }
  [ -f "$STAGED/rom_header.bin" ] && cp -f "$STAGED/rom_header.bin" "$d/rom_header.bin"
  [ -f "$STAGED/followers_fan.bin" ] && cp -f "$STAGED/followers_fan.bin" "$d/followers_fan.bin"
  [ -f "$STAGED/FOLLOWER_CREDITS.txt" ] && cp -f "$STAGED/FOLLOWER_CREDITS.txt" "$d/FOLLOWER_CREDITS.txt"
  if [ "$CIA_BUILT" = 1 ] && [ -f "$OUTCIA" ]; then
    mkdir -p "$sd/cias" && cp -f "$OUTCIA" "$sd/cias/${TARGET}.cia" && ok "HOME Menu installer copied to $sd/cias/${TARGET}.cia (install it with FBI)"
  fi
  ok "copied to $d"
  say "Launch it from the Homebrew Launcher on the New 3DS. This port currently reaches the title screen."
  echo "##INSTALLED:$d"
  step_clean >/dev/null 2>&1 || true
}

step_cleantree() {
  # Generated extract and 3DS objects only. orig/ (the ROM link), saves and the SD card are never touched.
  local removed=0
  if [ -d "$ROOT/n3ds/build/$SLOT" ]; then
    rm -rf "$ROOT/n3ds/build/$SLOT" && ok "removed n3ds/build/$SLOT"; removed=1
  fi
  if [ -d "$EXTRACT" ]; then
    rm -rf "$EXTRACT" && ok "removed extract/${SLOT}_us"; removed=1
  fi
  [ "$removed" = 1 ] || ok "no build tree to remove"
}

step_clean() {
  local list=() f sd="${P3DS_SD:-}"
  for f in /tmp/p3ds_builder.$(id -u).pid; do [ -f "$f" ] && ! kill -0 "$(cat "$f" 2>/dev/null)" 2>/dev/null && list+=("$f"); done
  for f in "$HOME"/*.partial "$ROOT".partial; do [ -e "$f" ] && list+=("$f"); done
  if [ -n "$sd" ] && [ -d "$sd" ]; then
    for f in "$sd"/cias/${TARGET}*.cia; do [ -f "$f" ] && [ "$f" != "$sd/cias/${TARGET}.cia" ] && list+=("$f"); done
    for f in "$sd/3ds/$FOLDER"/${TARGET}*.3dsx "$sd/3ds/$FOLDER"/${TARGET}*.3dsx.*; do
      case "$f" in "$sd/3ds/$FOLDER/${TARGET}.3dsx"|"$sd/3ds/$FOLDER/${TARGET}.3dsx.prev") continue ;; esac
      [ -f "$f" ] && list+=("$f")
    done
  fi
  if [ ${#list[@]} = 0 ]; then ok "nothing to clean up"; return 0; fi
  for f in "${list[@]}"; do
    case "$f" in *.sav|*.sav.*|*/save_backups/*|*.nds|*.ini|*.cfg|*.json) continue ;; esac
    if [ "${P3DS_CLEAN_DRY:-}" = 1 ]; then echo "##CLEAN:$f"; else rm -rf -- "$f" && echo "  removed $f"; fi
  done
  return 0
}

run_step() {
  name="$1"
  case "$name" in
    check|needs|setup|stage|build|cia|deploy|clean|cleantree) ;;
    link) echo "##STEP:link:START"; ok "no separate link step ($TITLE build already produces the .3dsx)"; echo "##STEP:link:OK"; return 0 ;;
    *) echo "unknown step: $name" >&2; return 2 ;;
  esac
  echo "##STEP:$name:START"
  if "step_$name"; then echo "##STEP:$name:OK"; return 0; else echo "##STEP:$name:FAIL"; return 1; fi
}

[ $# -gt 0 ] || { sed -n '2,18p' "$0"; exit 2; }
steps=()
for a in "$@"; do
  case "$a" in
    all)   steps+=(check stage build); [ "${P3DS_CIA:-0}" = 1 ] && steps+=(cia); steps+=(deploy) ;;
    quick) steps+=(build); [ "${P3DS_CIA:-0}" = 1 ] && steps+=(cia); steps+=(deploy) ;;
    *)     steps+=("$a") ;;
  esac
done
for s in "${steps[@]}"; do
  run_step "$s" || { echo "##DONE:FAIL"; exit 1; }
done
echo "##DONE:OK"
