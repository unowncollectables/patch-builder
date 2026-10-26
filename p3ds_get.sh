#!/usr/bin/env bash
# Downloads (or updates) a port's project folder so nobody has to use git by hand. The builder window calls it.
#   p3ds_get.sh platinum | soulsilver      clone into ~/3ds-platinum or ~/Pokemon-Soulsilver-New3ds (or P3DS_DEST)
#   p3ds_get.sh ensure-git                 install git (needs root)
# Prints  ##PROJECT:<folder>  when done. An existing clone is only fast-forwarded, and only if you have no edits in it.
set -uo pipefail
if [ "${1:-}" = "--env-b64" ]; then eval "$(printf %s "${2:-}" | base64 -d)"; shift 2; fi
what="${1:-}"
export GIT_TERMINAL_PROMPT=0   # never sit waiting for a GitHub username a beginner does not have
hint() { printf '##HINT:%s\n' "$*"; }
case "$what" in
  ensure-git)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq git ca-certificates >/dev/null || { hint "Could not install git. Check that this computer is online."; exit 1; }
    exit 0 ;;
  platinum)   url=${P3DS_PLATINUM_URL:-https://github.com/unowncollectables/3ds-platinum};            def="$HOME/3ds-platinum";            marker=work_patches ;;
  soulsilver) url=https://github.com/unowncollectables/Pokemon-Soulsilver-New3ds; def="$HOME/Pokemon-Soulsilver-New3ds"; marker=pspoke-patches ;;
  *) echo "usage: $0 platinum|soulsilver|ensure-git" >&2; exit 2 ;;
esac
command -v git >/dev/null || { echo "git is not installed"; exit 3; }
dest="${P3DS_DEST:-$def}"
if [ -d "$dest/.git" ] && [ "${P3DS_OFFLINE:-}" = 1 ]; then
  echo "  [ok]      offline mode: using $dest as it is (not checking GitHub)"
elif [ -d "$dest/.git" ]; then
  # Make sure this clone points at the right project (only the remote changes; ROMs, saves, build cache stay).
  cur=$(git -C "$dest" remote get-url origin 2>/dev/null)
  norm() { printf %s "$1" | sed -E 's#^git@github.com:#https://github.com/#; s#\.git$##; s#/$##' | tr A-Z a-z; }
  if [ "$(norm "$cur")" != "$(norm "$url")" ]; then
    echo "  [!]       $dest pointed at '${cur:-nothing}'; switching it to $url"
    git -C "$dest" remote remove origin 2>/dev/null; git -C "$dest" remote add origin "$url"
  fi
  # Get the latest main. Only tracked source files change: ROMs, build cache (pspoke/, build/), followers.bin and the
  # SD card are untracked/ignored or elsewhere, so they are never touched.
  br=main
  if ! git -C "$dest" fetch -q origin "$br" 2>&1; then
    echo "  (could not check GitHub for updates; using what is there)"
  else
    n=$(git -C "$dest" rev-list --count HEAD..FETCH_HEAD 2>/dev/null || echo 0)
    if [ "$(git -C "$dest" rev-parse HEAD)" = "$(git -C "$dest" rev-parse FETCH_HEAD)" ] && [ "$(git -C "$dest" rev-parse --abbrev-ref HEAD)" = "$br" ]; then
      echo "  [ok]      $what project: already up to date"; echo "##NEWS:$what:Already up to date"
    else
      if [ -n "$(git -C "$dest" status --porcelain --untracked-files=no)" ]; then
        git -C "$dest" stash push -q -m "p3ds-builder update $(date +%Y%m%d_%H%M%S)" && echo "  (your own edits in $dest were saved with git stash before updating)"
      fi
      git -C "$dest" log --format='     - %s' -n 5 HEAD..FETCH_HEAD
      if git -C "$dest" checkout -q -B "$br" FETCH_HEAD 2>&1 && git -C "$dest" reset -q --hard FETCH_HEAD; then
        echo "  [ok]      $what project is now at the latest $br ($n new change(s))"
        echo "##NEWS:$what:$n new change(s). Latest: $(git -C "$dest" log -1 --format=%s)"
      else
        echo "  (could not update $dest; using what is there)"
      fi
    fi
  fi
elif [ -d "$dest/$marker" ]; then
  echo "  [ok]      using the project folder you picked: $dest"
else
  [ -e "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ] && { echo "ERROR: $dest exists and is not the project"; hint "The folder $dest already exists but is not the project. Pick the right folder in Advanced, or remove it."; exit 1; }
  if ! git ls-remote -q "$url" >/dev/null 2>&1; then
    if git ls-remote -q https://github.com/git/git HEAD >/dev/null 2>&1; then
      echo "ERROR: $url cannot be reached (the Platinum project is not public, or it moved)."
      hint "The $what project is not public yet, so it cannot be downloaded. Nothing is wrong with your computer or game file. Wait for the public release, then press the button again."
    else
      hint "Could not reach GitHub. Check that this computer is online, then press the button again."
    fi
    exit 1
  fi
  echo "Downloading $url ..."
  git clone -q "$url" "$dest.partial" 2>&1 && mv "$dest.partial" "$dest" || { rm -rf "$dest.partial"; hint "Could not download the project. Check that this computer is online, then press the button again."; exit 1; }
  echo "  [ok]      downloaded to $dest"
fi
[ -d "$dest/.git" ] && echo "##HEAD:$what:$(git -C "$dest" log -1 --format='%H %cs %s')"
if [ "${P3DS_CHECK_REMOTE:-}" = 1 ] && [ -d "$dest/.git" ]; then
  r=$(git ls-remote -q "$url" refs/heads/main 2>/dev/null | cut -f1)
  [ -n "$r" ] && echo "##REMOTE:$what:$r"
fi
echo "##PROJECT:$dest"
