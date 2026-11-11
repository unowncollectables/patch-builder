#!/usr/bin/env bash
# Pokemon Pinball: Ruby & Sapphire 3DS build driver (runs inside Linux / WSL2). The GUI calls it; by hand:
#   pinball_build.sh <step> [<step> ...]
# Steps (each can be run on its own, and each is safe to repeat):
#   check     show what is installed / built
#   needs     print which one-time steps are still missing (##NEED:<step>); used by "Build and install"
#   setup     install tools (apt + devkitPro)  [needs root]
#   stage     check your Pinball ROM (it is read in place, never copied into the project)
#   build     the project's scripts/build.sh: fetch the decompilation at the pinned commits, rebuild the game from it,
#             require the result to be byte for byte your ROM, then compile it with the port -> pinball-rs.3dsx
#   cia       make the HOME Menu installable pinball-rs.cia (builds makerom/bannertool the first time)
#   deploy    copy to the SD card (backs up the save first)
#   all       check stage build [cia] deploy        quick   build [cia] deploy
# Environment: P3DS_ROOT (Pokemon-Pinball-New3ds clone), P3DS_PINBALL (path to .gba), P3DS_SD (SD root, optional),
#              P3DS_CIA=1 (also make the .cia)
# Unlike the DS games, the finished program holds the whole game, so no ROM is copied to the SD card.
set -uo pipefail
if [ "${1:-}" = "--env-b64" ]; then eval "$(printf %s "${2:-}" | base64 -d)"; shift 2; fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The .exe bundles the common setup and tool checks next to the drivers.
source "$HERE/setup_build_tools.sh" || {
  echo "##HINT:The build tools setup script is missing. Download the builder again and retry."
  exit 1
}
[ "$(ps -o pgid= $$ 2>/dev/null | tr -d ' ')" = "$$" ] && echo $$ > "/tmp/p3ds_builder.$(id -u).pid" 2>/dev/null
ROOT="${P3DS_ROOT:-}"
if [ -z "$ROOT" ] || [ ! -f "$ROOT/upstream.lock" ] || [ ! -f "$ROOT/scripts/build.sh" ] || [ ! -f "$ROOT/game.mk" ]; then
  echo "ERROR: P3DS_ROOT must point at your clone of Pokemon-Pinball-New3ds (the folder containing game.mk)." >&2
  echo "##HINT:The Pinball project folder is wrong. In Advanced, pick the Pokemon-Pinball-New3ds folder (the one with game.mk inside)."; exit 2
fi
DKP=/opt/devkitpro
WANT_SHA1=$(sed -n 's/^rom_sha1=//p' "$ROOT/upstream.lock")
ROM="${P3DS_PINBALL:-}"
OUT3DSX="$ROOT/build/pinball-rs.3dsx"
OUTELF="$ROOT/build/pinball3ds.elf"
OUTCIA="$ROOT/build/pinball-rs.cia"
CIA_TOOLS="$ROOT/build/cia-tools"
SDNAME=pinball-rs          # SD folder; the program keeps pinball.sav, log.txt and crash.txt there
CIA_BUILT=0

say()  { printf '%s\n' "$*"; }
ok()   { printf '  [ok]      %s\n' "$*"; }
miss() { printf '  [MISSING] %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; return 1; }
hint() { printf '##HINT:%s\n' "$*"; }
free_gb() { df -Pk "$1" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}'; }
as_root() { if [ "$(id -u)" -eq 0 ]; then "$@"; elif sudo -n true 2>/dev/null; then sudo -n "$@"; else return 77; fi; }
have_png() { [ -f /usr/include/png.h ] || [ -f /usr/local/include/png.h ]; }
jobs_n() {   # one job per CPU, at most one per GB of free RAM and 12; P3DS_JOBS overrides
  local j="${P3DS_JOBS:-}" m
  if [ -z "$j" ]; then
    j=$(nproc 2>/dev/null || echo 2)
    m=$(awk '/MemAvailable/ {printf "%d", $2/1048576}' /proc/meminfo 2>/dev/null)
    [ -n "$m" ] && [ "$m" -ge 1 ] && [ "$m" -lt "$j" ] && j=$m
    [ "$j" -gt 12 ] && j=12
  fi
  [ "$j" -ge 1 ] 2>/dev/null || j=1
  printf %s "$j"
}

fix_line_endings() {   # a Windows git checkout turns every line ending into CRLF, which breaks bash scripts
  local list
  list=$(cd "$ROOT" && git ls-files -z 2>/dev/null | xargs -0 -r grep -lIZ $'\r' 2>/dev/null | tr '\0' '\n')
  [ -n "$list" ] || return 0
  say "Fixing Windows line endings in $(printf '%s\n' "$list" | wc -l) project file(s)..."
  ( cd "$ROOT" && printf '%s\n' "$list" | while IFS= read -r f; do [ -L "$f" ] || sed -i 's/\r$//' "$f"; done )
}

rom_problem() {
  local code fixed
  fixed=$(dd if="$1" bs=1 skip=178 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n')
  code=$(dd if="$1" bs=1 skip=172 count=4 2>/dev/null | tr -dc 'A-Z0-9')
  if [ "$fixed" != 96 ]; then echo "That file is not a Game Boy Advance game (.gba)."; return; fi
  case "$code" in
    BPPE) echo "" ;;
    BPP*) echo "That is Pokemon Pinball: Ruby & Sapphire from another region (code $code). You need the US version (BPPE)." ;;
    *) echo "That is not Pokemon Pinball: Ruby & Sapphire (game code $code)." ;;
  esac
}

# ------------------------------------------------------------------------------------------------ check
step_check() {
  say "Pinball project folder : $ROOT"
  g=$(free_gb "$ROOT"); [ -n "$g" ] && { [ "$g" -ge 2 ] && ok "free disk space ${g} GB" || miss "only ${g} GB free disk space (the build needs about 2 GB)"; }
  for c in git make gcc g++ python3 sha1sum jq; do command -v $c >/dev/null && ok $c || miss $c; done
  have_png && ok "libpng (for the decompilation's graphics tools)" || miss "libpng-dev"
  [ -x "$DKP/devkitARM/bin/arm-none-eabi-gcc" ] && ok "devkitARM" || miss "devkitARM"
  [ -f "$DKP/libctru/include/3ds.h" ] && ok libctru || miss libctru
  [ -x "$DKP/tools/bin/3dsxtool" ] && ok 3dstools || miss "3dstools (3dsxtool)"
  if [ -n "$ROM" ] && [ -f "$ROM" ]; then
    s=$(sha1sum "$ROM" | cut -d' ' -f1)
    [ "$s" = "$WANT_SHA1" ] && ok "Pinball ROM (SHA1 matches the US release)" || say "  [!]       ROM SHA1 $s is not the US release ($WANT_SHA1); the build will stop"
  else miss "Pinball ROM not selected"; fi
  [ -f "$ROOT/build/upstream/.pokepinballrs.built" ] && ok "decompilation rebuilt and matched to your ROM" || say "  [--]      decompilation not built yet (first build does it; a few minutes)"
  if [ -f "$OUT3DSX" ]; then ok "built: $(ls -l --time-style=long-iso "$OUT3DSX" | awk '{print $5" bytes, "$6" "$7}')"; else say "  [--]      no .3dsx built yet"; fi
  return 0
}

step_needs() {
  local tools=ok
  for c in git make gcc g++ python3 sha1sum; do command -v $c >/dev/null || tools=missing; done
  have_png || tools=missing
  command -v jq >/dev/null || tools=missing
  devkitpro_ready "$DKP" || tools=missing
  [ $tools = ok ] || echo "##NEED:setup"
  [ -f "$ROOT/build/upstream/.pokepinballrs.built" ] || echo "##NEED:full"
  return 0
}

# ------------------------------------------------------------------------------------------------ setup (root)
step_setup() {
  setup_build_tools || return 1
  say "Setup done."
}

# ------------------------------------------------------------------------------------------------ stage
step_stage() {
  [ -n "$ROM" ] && [ -f "$ROM" ] || { die "no Pinball ROM selected"; hint "Pick your Pokemon Pinball: Ruby & Sapphire .gba file in the Pinball box."; return 1; }
  why=$(rom_problem "$ROM")
  [ -z "$why" ] || { die "$why"; hint "$why"; return 1; }
  s=$(sha1sum "$ROM" | cut -d' ' -f1)
  if [ "$s" != "$WANT_SHA1" ]; then
    die "Pinball ROM SHA1 is $s, expected $WANT_SHA1 (US)."
    hint "This Pinball file does not match the clean US dump the build needs. Dump your cartridge again."
    return 1
  fi
  ok "Pinball ROM verified (US, SHA1 matches)"
}

# ------------------------------------------------------------------------------------------------ build / cia
step_build() {
  [ -f "$ROM" ] || { die "Pinball ROM not selected"; return 1; }
  [ -x "$DKP/devkitARM/bin/arm-none-eabi-gcc" ] || { die "devkitARM missing - run 'setup'"; return 1; }
  have_png || { die "libpng-dev missing - run 'setup'"; hint "One build tool (libpng) is missing. Open Troubleshooting and choose Repair tools, then press the button again."; return 1; }
  fix_line_endings
  g=$(free_gb "$ROOT"); [ -n "$g" ] && [ "$g" -lt 1 ] && { die "only ${g} GB free"; hint "The disk is almost full. Free up about 2 GB and press the button again."; return 1; }
  say "Compiling Pinball. The first time it downloads the decompilation and rebuilds the game from it (a few minutes); later builds are quick."
  say "Project version: $(git -C "$ROOT" log -1 --format='commit %h (%cs): %s' 2>/dev/null)"
  rm -f "$OUT3DSX" "$OUTELF"      # never hand over a leftover from an earlier run
  DEVKITPRO=$DKP JOBS=$(jobs_n) bash "$ROOT/scripts/build.sh" "$ROM" || {
    die "scripts/build.sh failed - the real error is higher up in the log"
    if ! git ls-remote -q https://github.com/pret/pokepinballrs HEAD >/dev/null 2>&1; then
      hint "Could not reach GitHub to download the decompilation. Check that this computer is online, then press the button again."
    fi
    return 1
  }
  [ -f "$OUT3DSX" ] || { die "no .3dsx produced"; return 1; }
  ok "built $(stat -c %s "$OUT3DSX") bytes  SHA1 $(sha1sum "$OUT3DSX" | cut -c1-12)"
}

step_cia() {
  [ -f "$OUTELF" ] || { die "nothing built yet - run the 'build' step first"; return 1; }
  P3DS_JOBS=$(jobs_n) bash "$HERE/build_cia_tools.sh" "$CIA_TOOLS" || {
    die "could not build makerom and bannertool"
    hint "The HOME Menu tools could not be built. Uncheck the HOME Menu installer to build the Homebrew Launcher version."
    return 1
  }
  rm -f "$OUTCIA"      # a leftover .cia must never be copied to the card by mistake
  bash "$ROOT/scripts/make_cia.sh" "$CIA_TOOLS/bin" || { die "making the .cia failed (error above)"; hint "The HOME Menu (.cia) version failed. The .3dsx still works. Press 'Copy details for help'."; return 1; }
  [ -f "$OUTCIA" ] || { die "no .cia produced"; return 1; }
  CIA_BUILT=1
  ok "built $(stat -c %s "$OUTCIA") bytes pinball-rs.cia"
}

# ------------------------------------------------------------------------------------------------ export
# Copies the finished .3dsx / .cia to a normal folder (P3DS_OUT, e.g. Documents\Pokemon 3DS Builds\Pinball\),
# whether or not an SD card is picked. No game data (ROM, save) is copied there.
step_export() {
  [ -n "${P3DS_OUT:-}" ] || return 0
  [ -f "$OUT3DSX" ] || return 0
  local o="$P3DS_OUT/Pinball"
  mkdir -p "$o" 2>/dev/null && [ -w "$o" ] || { die "cannot write to the output folder $o"; hint "Could not save the finished game in your output folder. Pick another folder in 'Output folder' and press the button again."; return 1; }
  cp -f "$OUT3DSX" "$o/pinball-rs.3dsx" || { die "copy to the output folder failed"; hint "Could not save the finished game in your output folder. Check there is free space, then try again."; return 1; }
  rm -f "$o/pinball-rs.cia"
  if [ -f "$OUTCIA" ] && [ "${P3DS_CIA:-0}" = 1 ]; then
    cp -f "$OUTCIA" "$o/pinball-rs.cia" || {
      die "could not save the HOME Menu installer"
      hint "Check the free space and permissions on the output folder, then retry the build."
      return 1
    }
  fi
  cat > "$o/How to put this on your 3DS.txt" <<'TXT'
How to put this on your 3DS
===========================
1. Put your 3DS SD card in this computer.
2. Homebrew Launcher version: copy pinball-rs.3dsx to  SD:\3ds\pinball-rs\  and start it from the Homebrew Launcher.
3. HOME Menu version (optional): copy pinball-rs.cia to  SD:\cias\  then on the 3DS open FBI > SD > cias > pinball-rs.cia > Install.
The whole game is inside these files, so no .gba is needed on the card.
Your save (pinball.sav in SD:\3ds\pinball-rs\) is never touched by copying these files. If something goes wrong,
log.txt and crash.txt in that folder say what happened.
TXT
  printf '\nBuilt version: %s\n' "$(git -C "$ROOT" log -1 --format='commit %h (%cs): %s' 2>/dev/null)" >> "$o/How to put this on your 3DS.txt"
  sed -i 's/$/\r/' "$o/How to put this on your 3DS.txt" 2>/dev/null   # Windows line endings for Notepad
  ok "saved to your output folder (Pinball)"
  echo "##OUTPUT:Pinball"
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
  [ -d "$sd/Nintendo 3DS" ] || [ -d "$sd/3ds" ] || say "  [!]       This does not look like a 3DS SD card (no 'Nintendo 3DS' or '3ds' folder). Copying anyway."
  need=$(( $(stat -c %s "$OUT3DSX") * 2 + 20000000 )); have=$(df -Pk "$sd" 2>/dev/null | awk 'NR==2 {print $4*1024}')
  [ -n "$have" ] && [ "$have" -lt "$need" ] && { die "not enough space on the SD card"; hint "The SD card is full. Free up about 50 MB on it and press the button again."; return 1; }
  d="$sd/3ds/$SDNAME"
  mkdir -p "$d" 2>/dev/null && [ -w "$d" ] || { die "cannot write to $d"; hint "The SD card is write-protected. Slide the little lock switch up, plug it back in, and try again."; return 1; }
  if [ -f "$d/pinball.sav" ]; then
    mkdir -p "$d/save_backups"; cp -f "$d/pinball.sav" "$d/save_backups/pinball_$(date +%Y%m%d_%H%M%S).sav" && ok "save backed up to save_backups/"
  fi
  [ -f "$d/pinball-rs.3dsx" ] && cp -f "$d/pinball-rs.3dsx" "$d/pinball-rs.3dsx.prev"      # one backup of the previous build
  cp -f "$OUT3DSX" "$d/pinball-rs.3dsx" && sync \
    && [ "$(sha1sum < "$OUT3DSX")" = "$(sha1sum < "$d/pinball-rs.3dsx")" ] \
    || { die "copy to the SD card failed or came out different"; hint "Copying to the SD card failed. Re-seat the card and try again; if it keeps failing the card may be damaged."; return 1; }
  if [ "$CIA_BUILT" = 1 ] && [ -f "$OUTCIA" ]; then
    mkdir -p "$sd/cias" && cp -f "$OUTCIA" "$sd/cias/pinball-rs.cia" && ok "HOME Menu installer copied to $sd/cias/pinball-rs.cia (install it with FBI)"
  fi
  ok "copied to $d"
  say "Launch it from the Homebrew Launcher on the New 3DS."
  echo "##INSTALLED:$d"
  step_clean >/dev/null 2>&1 || true      # tidy leftovers after a good copy (see step_clean)
}

# ------------------------------------------------------------------------------------------------ clean tree
step_cleantree() {
  # Deletes only what the build generated (the downloaded decompilation, the adapted tree, objects). ROMs, saves and
  # the SD card are never touched; the next build downloads and rebuilds everything.
  if [ -d "$ROOT/build" ]; then
    rm -rf "$ROOT/build" && ok "build tree removed ($ROOT/build)"
  else
    ok "no build tree to remove"
  fi
}

# ------------------------------------------------------------------------------------------------ clean
# Removes only leftovers this builder made. NEVER touches saves, save_backups/, logs, ROMs, the toolchain or the build cache.
# P3DS_CLEAN_DRY=1 only lists (##CLEAN:<path>) what would go.
step_clean() {
  local list=() f sd="${P3DS_SD:-}"
  for f in /tmp/p3ds_builder.$(id -u).pid; do [ -f "$f" ] && ! kill -0 "$(cat "$f" 2>/dev/null)" 2>/dev/null && list+=("$f"); done
  for f in "$ROOT".partial; do [ -e "$f" ] && list+=("$f"); done        # half-finished download
  if [ -n "$sd" ] && [ -d "$sd" ]; then
    for f in "$sd"/cias/pinball-rs*.cia; do [ -f "$f" ] && [ "$f" != "$sd/cias/pinball-rs.cia" ] && list+=("$f"); done
    for f in "$sd/3ds/$SDNAME"/pinball-rs*.3dsx "$sd/3ds/$SDNAME"/pinball-rs*.3dsx.*; do
      case "$f" in "$sd/3ds/$SDNAME/pinball-rs.3dsx"|"$sd/3ds/$SDNAME/pinball-rs.3dsx.prev") continue ;; esac
      [ -f "$f" ] && list+=("$f")
    done
  fi
  if [ ${#list[@]} = 0 ]; then ok "nothing to clean up"; return 0; fi
  for f in "${list[@]}"; do
    case "$f" in *.sav|*.sav.*|*/save_backups/*|*.gba|*.txt|*.ini|*.cfg|*.json) continue ;; esac   # belt and braces
    if [ "${P3DS_CLEAN_DRY:-}" = 1 ]; then echo "##CLEAN:$f"; else rm -rf -- "$f" && echo "  removed $f"; fi
  done
  return 0
}

# ------------------------------------------------------------------------------------------------ driver
run_step() {
  name="$1"
  case "$name" in
    check|needs|setup|stage|build|cia|deploy|clean|cleantree) ;;
    *) echo "unknown step: $name" >&2; return 2 ;;
  esac
  echo "##STEP:$name:START"
  if "step_$name"; then echo "##STEP:$name:OK"; return 0; else echo "##STEP:$name:FAIL"; return 1; fi
}

[ $# -gt 0 ] || { sed -n '2,17p' "$0"; exit 2; }
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
