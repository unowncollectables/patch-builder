#!/usr/bin/env bash
# HeartGold 3DS build driver (runs inside Linux / WSL2). EXPERIMENTAL. The GUI calls it; you can also run it by hand:
#   hg_build.sh <step> [<step> ...]
# HeartGold is the SoulSilver port built as a separate game. It is the same GitHub project (Pokemon-Soulsilver-New3ds), but
# everything it makes is its own: its own project folder (~/Pokemon-HeartGold-New3ds), its own build tree (pspoke-hg), its own
# SD folder (3ds/heartgold3ds with heartgold.nds / heartgold.sav), its own heartgold3ds.cia and HOME Menu title. Nothing here
# reads or writes a SoulSilver folder, file or save. This file follows ss_build.sh step for step: keep them in line.
# Steps (each can be run on its own, and each is safe to repeat):
#   check     show what is installed / built
#   needs     print which one-time steps are still missing (##NEED:<step>); used by "Build and install"
#   setup     install tools (apt + devkitPro + pip)  [needs root]
#   stage     check your HeartGold ROM (it is read in place, never copied into the project)
#   build     compile the game: scripts/hg_build.sh (pspoke at upstream.lock + pspoke-patches/, as HeartGold)  - the long one
#   link      link heartgold3ds.3dsx
#   cia       make the HOME Menu installable heartgold3ds.cia (builds makerom/bannertool the first time)
#   deploy    copy to the SD card (backs up the save first)
#   all       check stage build link [cia] deploy        quick   link [cia] deploy
# Environment: P3DS_ROOT (HeartGold's clone of Pokemon-Soulsilver-New3ds), P3DS_HEARTGOLD (path to .nds), P3DS_SD (SD root, optional),
#              P3DS_CIA=1 (also make the .cia)
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
if [ -z "$ROOT" ] || [ ! -f "$ROOT/upstream.lock" ] || [ ! -d "$ROOT/pspoke-patches" ]; then
  echo "ERROR: P3DS_ROOT must point at HeartGold's clone of Pokemon-Soulsilver-New3ds (the folder containing upstream.lock)." >&2
  echo "##HINT:The HeartGold project folder is wrong. In Advanced, empty the HeartGold project folder box and press the button again: the builder downloads it for you."; exit 2
fi
if [ ! -f "$ROOT/scripts/hg_build.sh" ] || [ ! -f "$ROOT/heartgold3ds/full/Makefile" ]; then
  echo "ERROR: $ROOT has no HeartGold build (scripts/hg_build.sh is missing): it is an older or a SoulSilver-only copy of the project." >&2
  echo "##HINT:This project folder does not contain the HeartGold build yet. In Advanced, empty the HeartGold project folder box and press the button again so the builder downloads the right one."; exit 2
fi
DKP=/opt/devkitpro
WANT_SHA1=$(sed -n 's/^heartgold_sha1=//p' "$ROOT/upstream.lock")
ROM="${P3DS_HEARTGOLD:-}"
FULLDIR="$ROOT/heartgold3ds/full"
OUT3DSX="$FULLDIR/heartgold3ds.3dsx"
OUTCIA="$FULLDIR/heartgold3ds.cia"
TREE="$ROOT/pspoke-hg/.work/tree/test_out"
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
  # Parallel compiles: one job per CPU, but at most one per GB of free RAM (WSL gets about half the PC's memory) and 12.
  # P3DS_JOBS=1 turns it off if a build ever fails only when parallel.
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

rom_sha1() {   # SHA1 of the ROM, remembered by size+mtime so check/stage do not each re-read 128 MB (slow on /mnt/c)
  local f="$1" key c="$ROOT/build-hg/.rom_sha1"
  key="$(stat -c '%s %Y' "$f" 2>/dev/null)|$f"
  if [ -f "$c" ] && [ "$(sed -n 1p "$c")" = "$key" ]; then sed -n 2p "$c"; return; fi
  local s; s=$(sha1sum "$f" | cut -d' ' -f1)
  mkdir -p "$ROOT/build-hg" 2>/dev/null && printf '%s\n%s\n' "$key" "$s" > "$c" 2>/dev/null
  printf '%s\n' "$s"
}

fix_line_endings() {   # a Windows git checkout turns every line ending into CRLF, which breaks bash scripts and patches
  local list head stamp="$ROOT/build-hg/.eol_checked"
  # Reading every tracked file takes a while on /mnt/c, so it is skipped when the checkout has not moved since the last clean scan.
  head=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null)
  if [ -n "$head" ] && [ "$(cat "$stamp" 2>/dev/null)" = "$head" ] && [ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no 2>/dev/null | head -1)" ]; then return 0; fi
  list=$(cd "$ROOT" && git ls-files -z 2>/dev/null | xargs -0 -r grep -lIZ $'\r' 2>/dev/null | tr '\0' '\n')
  if [ -z "$list" ]; then
    [ -n "$head" ] && mkdir -p "$ROOT/build-hg" 2>/dev/null && printf '%s\n' "$head" > "$stamp" 2>/dev/null
    return 0
  fi
  say "Fixing Windows line endings in $(printf '%s\n' "$list" | wc -l) project file(s)..."
  ( cd "$ROOT" && printf '%s\n' "$list" | while IFS= read -r f; do [ -L "$f" ] || sed -i 's/\r$//' "$f"; done )
}

# Files under pspoke/port/ are copied into the build tree once (with @WORK@ paths filled in) and never refreshed, so a patch
# that changes one used to need "Clean build tree". This refreshes only the ones that changed since the last build, and
# asks for the game compile to re-run only when one of them is not already re-synced by ss3ds_game.sh itself.
# The first run just records a baseline (it cannot know what an old tree was staged from). P3DS_NO_SYNC=1 skips all of it.
sync_staged() {
  local P="$ROOT/pspoke-hg" W T man new f rel sha dest changed=0 stale=0 sub
  W="$P/.work/tree"; T="$W/test_out"; man="$W/p3ds_port.manifest"; new="$W/p3ds_port.manifest.new"
  [ "${P3DS_NO_SYNC:-}" = 1 ] && return 0
  [ -d "$P/port" ] && [ -d "$T" ] || return 0
  ( cd "$P/port" && find . -type f -print0 | sort -z | xargs -0 -r sha1sum ) > "$new" 2>/dev/null || { rm -f "$new"; return 0; }
  if [ ! -f "$man" ]; then mv -f "$new" "$man"; say "  [--]      recorded the project files the build tree was made from (nothing refreshed this time)"; return 0; fi
  while read -r sha f; do
    rel="${f#./}"
    grep -qF "$sha  $f" "$man" && continue
    dest="$T/$rel"; [ -d "$(dirname "$dest")" ] || continue
    if [ -f "$P/scripts/ss3ds_game.sh" ] && grep -qF "$rel" "$P/scripts/ss3ds_game.sh"; then
      say "  [sync]    $rel (the build re-syncs this one itself)"; continue
    fi
    sub=$(grep -lIs -e '@WORK@' -e '@PSPDEV@' -e '@PPSSPP@' "$P/port/$rel")
    if [ -n "$sub" ]; then
      sed -e "s#@WORK@#$T#g" -e "s#@PSPDEV@#${PSPDEV:-$ROOT/toolchain-3ds}#g" -e "s#@PPSSPP@#PPSSPPHeadless#g" "$P/port/$rel" > "$dest"
    else
      cp -f "$P/port/$rel" "$dest"
    fi
    say "  [sync]    $rel refreshed in the build tree"; changed=$((changed+1))
  done < "$new"
  if [ "$changed" -gt 0 ]; then
    rm -f "$W/.stamp-soulsilver-game"
    say "  [sync]    $changed file(s) refreshed; the game compile will re-run once (no need for 'Clean build tree')"
  fi
  mv -f "$new" "$man"
}

rom_problem() {
  local code
  code=$(dd if="$1" bs=1 skip=12 count=4 2>/dev/null | tr -dc 'A-Z0-9')
  case "$code" in
    IPKE) echo "" ;;
    IPGE) echo "That is SoulSilver, not HeartGold. Put it in the SoulSilver box." ;;
    IPK*|IPG*) echo "That is HeartGold/SoulSilver from another region (code $code). You need US HeartGold (IPKE)." ;;
    CPUE|CPU*) echo "That is Pokemon Platinum, not HeartGold. Put it in the Platinum box." ;;
    "") echo "That file is not a DS game (.nds)." ;;
    *) echo "That is not Pokemon HeartGold (game code $code)." ;;
  esac
}

# ------------------------------------------------------------------------------------------------ check
step_check() {
  say "HeartGold project folder : $ROOT"
  g=$(free_gb "$ROOT"); [ -n "$g" ] && { [ "$g" -ge 8 ] && ok "free disk space ${g} GB" || miss "only ${g} GB free disk space (the build needs about 8 GB)"; }
  for c in git make patch rsync curl python3; do command -v $c >/dev/null && ok $c || miss $c; done
  python3 -I -c 'import ndspy' 2>/dev/null && ok "python ndspy" || miss "python ndspy  (pip install ndspy)"
  [ -x "$DKP/devkitARM/bin/arm-none-eabi-gcc" ] && ok "devkitARM" || miss "devkitARM"
  [ -f "$DKP/libctru/include/3ds.h" ] && ok libctru || miss libctru
  [ -f "$DKP/libctru/include/citro3d.h" ] && ok citro3d || miss citro3d
  [ -x "$DKP/tools/bin/3dsxtool" ] && ok 3dstools || miss "3dstools (3dsxtool)"
  [ -x "$DKP/tools/bin/picasso" ] && ok picasso || miss picasso
  if [ -n "$ROM" ] && [ -f "$ROM" ]; then
    m=$(python3 "$ROOT/scripts/hg_romcheck.py" "$ROM" 2>&1) && ok "${m#\[HG\] ROM ok: }" || say "  [!]       ${m#error: }"
  else miss "HeartGold ROM not selected"; fi
  [ -d "$ROOT/pspoke-hg/.git" ] && ok "pspoke prepared" || say "  [--]      pspoke not downloaded yet (first build does it)"
  [ -d "$TREE" ] && ok "generated tree exists" || say "  [--]      generated tree not built yet (first build is long)"
  if [ -f "$OUT3DSX" ]; then ok "built: $(ls -l --time-style=long-iso "$OUT3DSX" | awk '{print $5" bytes, "$6" "$7}')"; else say "  [--]      no .3dsx built yet"; fi
  return 0
}

step_needs() {
  local tools=ok
  for c in git make patch rsync curl python3 sha1sum; do command -v $c >/dev/null || tools=missing; done
  python3 -I -c 'import ndspy, PIL, numpy' 2>/dev/null || tools=missing
  devkitpro_ready "$DKP" || tools=missing
  [ $tools = ok ] || echo "##NEED:setup"
  [ -f "$TREE/soulsilver-native-core/nitromain-perf/Makefile" ] || echo "##NEED:full"
  return 0
}

# ------------------------------------------------------------------------------------------------ setup (root)
step_setup() {
  setup_build_tools || return 1
  say "Setup done."
}

# ------------------------------------------------------------------------------------------------ stage
step_stage() {
  [ -n "$ROM" ] && [ -f "$ROM" ] || { die "no HeartGold ROM selected"; hint "Pick your Pokemon HeartGold .nds file in the HeartGold box."; return 1; }
  why=$(rom_problem "$ROM")
  [ -z "$why" ] || { die "$why"; hint "$why"; return 1; }
  # The project's own check: the exact reference dump, or a US HeartGold whose game data and overlays all match it.
  if m=$(python3 "$ROOT/scripts/hg_romcheck.py" "$ROM" 2>&1); then ok "${m#\[HG\] ROM ok: }"
  else m=${m#error: }; die "$m"; hint "This HeartGold file cannot be used: $m"; return 1; fi
}

# ------------------------------------------------------------------------------------------------ build / link / cia
# Proves every patch in the project is really in the build: prints the count and fails if one is missing.
patch_report() {
  local P="$ROOT/pspoke-hg" p subj have total=0 miss=0 last=""
  [ -d "$P/.git" ] || return 0
  have=$(git -C "$P" log --format=%s ss3ds 2>/dev/null)
  for p in "$ROOT"/pspoke-patches/*.patch; do
    [ -f "$p" ] || continue
    total=$((total+1)); last=$(basename "$p")
    subj=$(sed -n 's/^Subject: \[PATCH[^]]*\] //p' "$p" | head -1)
    grep -qF -- "$subj" <<<"$have" || { miss=$((miss+1)); say "  [MISSING] patch not in the build: $(basename "$p")"; }
  done
  if [ "$miss" -gt 0 ]; then
    die "$miss patch(es) are in the project but NOT in the build"
    hint "Some of the project's patches are missing from the build, so the game would be out of date. Press 'Copy details for help' and send it; do not install anything from this run."
    return 1
  fi
  ok "all $total patches are in the build (newest: $last)"
}

step_build() {
  [ -f "$ROM" ] || { die "HeartGold ROM not selected"; return 1; }
  [ -x "$DKP/devkitARM/bin/arm-none-eabi-gcc" ] || { die "devkitARM missing - run 'setup'"; return 1; }
  fix_line_endings
  g=$(free_gb "$ROOT"); [ -n "$g" ] && [ "$g" -lt 4 ] && { die "only ${g} GB free"; hint "The disk is almost full (${g} GB free). Free up about 8 GB and press the button again."; return 1; }
  build_env; cd "$ROOT" || return 1
  say "Compiling HeartGold (experimental). The first time this takes a long time (it downloads the decompilation); later builds are quick."
  say "Project version: $(git -C "$ROOT" log -1 --format='commit %h (%cs): %s' 2>/dev/null)"
  # --prepare: puts pspoke-hg back to the shared source, applies new pspoke-patches, then gives it the HeartGold names again,
  # so sync_staged below sees exactly the files the build will use.
  local bl; bl=$(bash scripts/hg_build.sh --prepare 2>&1); local brc=$?
  printf '%s\n' "$bl" | grep -E '^(applied|moved) ' | sed 's/^/  [patch]   /'
  if [ "$brc" -ne 0 ]; then
    printf '%s\n' "$bl" | tail -15
    die "applying the project's patches failed"; hint "A patch could not be applied, so building now would give you an OLD or broken game. Press 'Copy details for help' and send it; do not install anything from this run."; return 1
  fi
  patch_report || return 1
  sync_staged
  bash scripts/hg_build.sh "$ROM" || { die "scripts/hg_build.sh failed - the real error is higher up in the log"; return 1; }
  ok "game compiled"
}

step_link() {
  [ -d "$TREE" ] || { die "the game is not compiled yet - run the 'build' step first"; return 1; }
  build_env
  # Never trust an old link: if any compiled object/archive of the game is newer than the .elf, throw the .elf away so it must relink.
  local elf0="$FULLDIR/heartgold3ds.elf" newer
  if [ -f "$elf0" ]; then
    newer=$(find "$ROOT/pspoke-hg/.work" "$FULLDIR/build" \( -name '*.a' -o -name '*.o' \) -newer "$elf0" -print -quit 2>/dev/null)
    [ -n "$newer" ] && { say "  [fresh]   the game changed since the last link ($(basename "$newer")); relinking"; rm -f "$elf0" "$OUT3DSX"; }
  fi
  ( cd "$FULLDIR" && make check && make heartgold3ds.3dsx ) || { die "link failed (error above)"; hint "The game did not link. The first red 'error' line in the details is the cause."; return 1; }
  [ -f "$OUT3DSX" ] || { die "no .3dsx produced"; return 1; }
  ok "built $(stat -c %s "$OUT3DSX") bytes  SHA1 $(sha1sum "$OUT3DSX" | cut -c1-12)"
  # Proof that this is a NEW binary and not an old one left over: when it was linked, and whether the GTS bypass is staged.
  local elf="$FULLDIR/heartgold3ds.elf"
  if [ -f "$elf" ]; then
    ok "linked $(date -r "$elf" '+%Y-%m-%d %H:%M:%S')  (project commit $(git -C "$ROOT" log -1 --format=%h 2>/dev/null))"
    # The 0179 text is compiled out of release builds, so check the network script the lib is built from instead.
    local nw="$TREE/native-probe/compile-network.py"
    if [ -f "$nw" ] && grep -q 'ss3ds 0178' "$nw"; then ok "GTS bypass (0178) is in the network library script"; else say "  [--]      GTS bypass (0178) NOT in the staged network library script (GTS will fail the old way)"; fi
  fi
}

step_cia() {
  [ -f "$FULLDIR/heartgold3ds.elf" ] || { die "nothing linked yet - run the 'link' step first"; return 1; }
  build_env
  rm -f "$OUTCIA"      # a leftover .cia must never be copied to the card by mistake
  ( cd "$FULLDIR" && make cia ) || { die "making the .cia failed (error above)"; hint "The HOME Menu (.cia) version failed. The .3dsx still works. Press 'Copy details for help'."; return 1; }
  [ -f "$OUTCIA" ] || { die "no .cia produced"; return 1; }
  [ "$OUTCIA" -nt "$FULLDIR/heartgold3ds.elf" ] || { die "the .cia is older than the game it should contain"; hint "The HOME Menu installer did not come out fresh, so it was not used. Press the button again; if it repeats, press 'Copy details for help'."; rm -f "$OUTCIA"; return 1; }
  CIA_BUILT=1
  ok "built $(stat -c %s "$OUTCIA") bytes heartgold3ds.cia"
}

# ------------------------------------------------------------------------------------------------ export
# Copies the finished .3dsx / .cia to a normal Windows folder (P3DS_OUT, e.g. Documents\Pokemon 3DS Builds\HeartGold\),
# whether or not an SD card is picked. No game data (ROM, save) is copied there.
step_export() {
  [ -n "${P3DS_OUT:-}" ] || return 0
  [ -f "$OUT3DSX" ] || return 0
  local o="$P3DS_OUT/HeartGold"
  mkdir -p "$o" 2>/dev/null && [ -w "$o" ] || { die "cannot write to the output folder $o"; hint "Could not save the finished game in your output folder. Pick another folder in 'Output folder' and press the button again."; return 1; }
  cp -f "$OUT3DSX" "$o/heartgold3ds.3dsx" || { die "copy to the output folder failed"; hint "Could not save the finished game in your output folder. Check there is free space, then try again."; return 1; }
  if [ -f "$OUTCIA" ] && [ "${P3DS_CIA:-0}" = 1 ]; then
    cp -f "$OUTCIA" "$o/heartgold3ds.cia" || {
      die "could not save the HOME Menu installer"
      hint "Check the free space and permissions on the output folder, then retry the build."
      return 1
    }
  fi
  cat > "$o/How to put this on your 3DS.txt" <<'TXT'
How to put this on your 3DS
===========================
1. Put your 3DS SD card in this computer.
2. Homebrew Launcher version: copy heartgold3ds.3dsx to  SD:\3ds\heartgold3ds\
   (also copy your own HeartGold .nds there once, named heartgold.nds). Start it from the Homebrew Launcher.
3. HOME Menu version (optional): copy heartgold3ds.cia to  SD:\cias\  then on the 3DS open FBI > SD > cias > heartgold3ds.cia > Install.
Your save (heartgold.sav in SD:\3ds\heartgold3ds\) is never touched by copying these files.
TXT
  printf '\nBuilt version: %s\n' "$(git -C "$ROOT" log -1 --format='commit %h (%cs): %s' 2>/dev/null)" >> "$o/How to put this on your 3DS.txt"
  sed -i 's/$/\r/' "$o/How to put this on your 3DS.txt" 2>/dev/null   # Windows line endings for Notepad
  ok "saved to your output folder (HeartGold)"
  echo "##OUTPUT:HeartGold"
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
  need=$(( $(stat -c %s "$OUT3DSX") + 140000000 )); have=$(df -Pk "$sd" 2>/dev/null | awk 'NR==2 {print $4*1024}')
  [ -n "$have" ] && [ "$have" -lt "$need" ] && { die "not enough space on the SD card"; hint "The SD card is full. Free up about 200 MB on it and press the button again."; return 1; }
  d="$sd/3ds/heartgold3ds"
  mkdir -p "$d" 2>/dev/null && [ -w "$d" ] || { die "cannot write to $d"; hint "The SD card is write-protected. Slide the little lock switch up, plug it back in, and try again."; return 1; }
  if [ -f "$d/heartgold.sav" ]; then
    mkdir -p "$d/save_backups"; cp -f "$d/heartgold.sav" "$d/save_backups/heartgold_$(date +%Y%m%d_%H%M%S).sav" && ok "save backed up to save_backups/"
  fi
  [ -f "$d/heartgold3ds.3dsx" ] && cp -f "$d/heartgold3ds.3dsx" "$d/heartgold3ds.3dsx.prev"      # one backup of the previous build
  cp -f "$OUT3DSX" "$d/heartgold3ds.3dsx" && sync \
    && [ "$(sha1sum < "$OUT3DSX")" = "$(sha1sum < "$d/heartgold3ds.3dsx")" ] \
    || { die "copy to the SD card failed or came out different"; hint "Copying to the SD card failed. Re-seat the card and try again; if it keeps failing the card may be damaged."; return 1; }
  # Your own ROM, only if there is none on the card yet. The save (heartgold.sav) is never touched.
  [ -f "$ROM" ] && [ ! -f "$d/heartgold.nds" ] && cp -f "$ROM" "$d/heartgold.nds"
  if [ "$CIA_BUILT" = 1 ] && [ -f "$OUTCIA" ]; then
    mkdir -p "$sd/cias" && cp -f "$OUTCIA" "$sd/cias/heartgold3ds.cia" && ok "HOME Menu installer copied to $sd/cias/heartgold3ds.cia (install it with FBI)"
  fi
  ok "copied to $d"
  say "Launch it from the Homebrew Launcher on the New 3DS."
  echo "##INSTALLED:$d"
  step_clean >/dev/null 2>&1 || true      # tidy leftovers after a good copy (see step_clean)
}

# ------------------------------------------------------------------------------------------------ driver
# ------------------------------------------------------------------------------------------------ clean
# Removes only leftovers this builder made. NEVER touches saves, save_backups/, config, ROMs (.nds), the toolchain
# (/opt/devkitpro), pspoke/ or the build cache. P3DS_CLEAN_DRY=1 only lists (##CLEAN:<path>) what would go.
# ------------------------------------------------------------------------------------------------ clean tree
step_cleantree() {
  # Deletes only the staged build tree (objects, stamps, staged sources). Downloaded
  # sources (.cache), ROMs, saves and the SD card are never touched, so the next
  # build re-stages and recompiles from scratch without re-downloading anything.
  # This is what makes a patch that changes a staged file (e.g. ds_ioreg.c) actually
  # take effect: the staged copy is otherwise reused no matter how often you rebuild.
  if [ -d "$ROOT/pspoke-hg/.work" ]; then
    rm -rf "$ROOT/pspoke-hg/.work" && ok "build tree removed ($ROOT/pspoke-hg/.work)"
  else
    ok "no build tree to remove"
  fi
}

step_clean() {
  local list=() f sd="${P3DS_SD:-}" me
  for f in /tmp/p3ds_builder.$(id -u).pid; do [ -f "$f" ] && ! kill -0 "$(cat "$f" 2>/dev/null)" 2>/dev/null && list+=("$f"); done
  for f in "$HOME"/*.partial "$ROOT".partial; do [ -e "$f" ] && list+=("$f"); done        # half-finished downloads
  if [ -n "$sd" ] && [ -d "$sd" ]; then
    # old HOME Menu installers of this game (other names than the current one)
    for f in "$sd"/cias/heartgold3ds*.cia; do [ -f "$f" ] && [ "$f" != "$sd/cias/heartgold3ds.cia" ] && list+=("$f"); done
    # old .3dsx copies: keep the current one and one backup (.prev)
    for f in "$sd/3ds/heartgold3ds"/heartgold3ds*.3dsx "$sd/3ds/heartgold3ds"/heartgold3ds*.3dsx.*; do
      case "$f" in "$sd/3ds/heartgold3ds/heartgold3ds.3dsx"|"$sd/3ds/heartgold3ds/heartgold3ds.3dsx.prev") continue ;; esac
      [ -f "$f" ] && list+=("$f")
    done
  fi
  if [ ${#list[@]} = 0 ]; then ok "nothing to clean up"; return 0; fi
  for f in "${list[@]}"; do
    case "$f" in *.sav|*.sav.*|*/save_backups/*|*.nds|*.ini|*.cfg|*.json) continue ;; esac   # belt and braces
    if [ "${P3DS_CLEAN_DRY:-}" = 1 ]; then echo "##CLEAN:$f"; else rm -rf -- "$f" && echo "  removed $f"; fi
  done
  return 0
}

run_step() {
  name="$1"
  case "$name" in
    check|needs|setup|stage|build|link|cia|deploy|clean|cleantree) ;;
    *) echo "unknown step: $name" >&2; return 2 ;;
  esac
  echo "##STEP:$name:START"
  if "step_$name"; then echo "##STEP:$name:OK"; return 0; else echo "##STEP:$name:FAIL"; return 1; fi
}

[ $# -gt 0 ] || { sed -n '2,20p' "$0"; exit 2; }
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
