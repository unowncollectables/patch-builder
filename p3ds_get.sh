#!/usr/bin/env bash
# Downloads (or updates) a port's project folder so nobody has to use git by hand. The builder window calls it.
#   p3ds_get.sh platinum | soulsilver      clone into ~/3ds-platinum or ~/Pokemon-Soulsilver-New3ds (or P3DS_DEST)
#   p3ds_get.sh ensure-git                 install git (needs root)
# Prints  ##PROJECT:<folder>  when done. An existing clone is only fast-forwarded, and only if you have no edits in it.
set -uo pipefail
if [ "${1:-}" = "--env-b64" ]; then eval "$(printf %s "${2:-}" | base64 -d)"; shift 2; fi
what="${1:-}"
hint() { printf '##HINT:%s\n' "$*"; }
case "$what" in
  ensure-git)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq git ca-certificates >/dev/null || { hint "Could not install git. Check that this computer is online."; exit 1; }
    exit 0 ;;
  platinum)   url=https://github.com/unowncollectables/3ds-platinum;            def="$HOME/3ds-platinum";            marker=work_patches ;;
  soulsilver) url=https://github.com/unowncollectables/Pokemon-Soulsilver-New3ds; def="$HOME/Pokemon-Soulsilver-New3ds"; marker=pspoke-patches ;;
  *) echo "usage: $0 platinum|soulsilver|ensure-git" >&2; exit 2 ;;
esac
command -v git >/dev/null || { echo "git is not installed"; exit 3; }
dest="${P3DS_DEST:-$def}"
if [ -d "$dest/.git" ]; then
  if [ -z "$(git -C "$dest" status --porcelain --untracked-files=no)" ]; then
    git -C "$dest" pull -q --ff-only 2>&1 && echo "  [ok]      $what project is up to date" || echo "  (could not update $dest; using what is there)"
  else
    echo "  (you have changes in $dest, so it was not updated)"
  fi
elif [ -d "$dest/$marker" ]; then
  echo "  [ok]      using the project folder you picked: $dest"
else
  [ -e "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ] && { echo "ERROR: $dest exists and is not the project"; hint "The folder $dest already exists but is not the project. Pick the right folder in Advanced, or remove it."; exit 1; }
  echo "Downloading $url ..."
  git clone -q "$url" "$dest.partial" 2>&1 && mv "$dest.partial" "$dest" || { rm -rf "$dest.partial"; hint "Could not download the project. Check that this computer is online, then press the button again."; exit 1; }
  echo "  [ok]      downloaded to $dest"
fi
echo "##PROJECT:$dest"
