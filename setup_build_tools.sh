#!/usr/bin/env bash
# Shared by the three drivers. Uses their as_root, say, ok, die and hint functions.
devkitpro_ready() {
  [ -x "$1/devkitARM/bin/arm-none-eabi-gcc" ] &&
  [ -f "$1/devkitARM/arm-none-eabi/lib/3dsx.specs" ] &&
  [ -f "$1/libctru/include/3ds.h" ] &&
  [ -f "$1/libctru/include/citro3d.h" ] &&
  [ -f "$1/libctru/lib/libctru.a" ] &&
  [ -f "$1/libctru/lib/libcitro3d.a" ] &&
  [ -x "$1/tools/bin/3dsxtool" ] &&
  [ -x "$1/tools/bin/bin2s" ] &&
  [ -x "$1/tools/bin/picasso" ]
}

install_devkitpro() (
  local layer work token backup
  local repository='devkitpro/devkitarm'
  # Final layers from the official devkitARM image index
  # sha256:116afba8df8453961de2936ffab20dd441edf4d682856c1ec8b0e53d7ed0bbf5.
  # These layers contain the 3DS SDK. Pin the bytes and extract only its files.
  case "$(uname -m)" in
    x86_64) layer=3c5026e520aeec9e208399f6d0a62098d5ee4c13d8cca97cdb9adbc6bb56a1c0 ;;
    aarch64) layer=a716a160de65205f0e2d51bedbd018832d44df38ca07732569081b22c5bd7f44 ;;
    *) die "Automatic devkitPro setup needs 64-bit x86 or ARM Linux."; return 1 ;;
  esac
  work=$(mktemp -d) || return 1
  trap 'rm -rf -- "$work"' EXIT
  say "Downloading the official devkitPro 3DS tools (about 215 MB)..."
  token=$(curl -fsS --retry 3 --connect-timeout 15 --max-time 60 \
    "https://auth.docker.io/token?service=registry.docker.io&scope=repository:$repository:pull" |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["token"])') || return 1
  # curl strips the authorization header if the registry redirects to a different host.
  curl -fL --retry 3 --connect-timeout 15 --max-time 600 \
    -H "Authorization: Bearer $token" \
    "https://registry-1.docker.io/v2/$repository/blobs/sha256:$layer" \
    -o "$work/tools.tar.gz" || return 1
  printf '%s  %s\n' "$layer" "$work/tools.tar.gz" | sha256sum --check --status || {
    die "The devkitPro download failed its checksum."
    return 1
  }
  say "Installing the verified devkitPro tools..."
  mkdir "$work/sdk" &&
    tar -xzf "$work/tools.tar.gz" -C "$work/sdk" --strip-components=2 \
      opt/devkitpro/devkitARM opt/devkitpro/libctru opt/devkitpro/tools \
      opt/devkitpro/portlibs/3ds opt/devkitpro/licenses &&
    devkitpro_ready "$work/sdk" &&
    "$work/sdk/devkitARM/bin/arm-none-eabi-gcc" -dumpversion >/dev/null || return 1

  # Merge into the standard SDK path without deleting other devkitPro files.
  as_root install -d -m 0755 "$DKP" || return 1
  backup=$(as_root mktemp -d "$DKP/.p3ds-backup.XXXXXX") || return 1
  local result=0
  as_root flock /run/lock/p3ds-devkitpro.lock rsync -a --chown=root:root \
    --safe-links --delay-updates --backup --backup-dir="$backup" "$work/sdk/" "$DKP/" || result=$?
  as_root rmdir -- "$backup" 2>/dev/null || say "Previous SDK files were kept in $backup."
  [ "$result" -eq 0 ] && devkitpro_ready "$DKP"
)

setup_build_tools() {
  if [ "$(id -u)" -ne 0 ] && ! sudo -n true 2>/dev/null; then
    die "Setup needs administrator access."
    hint "Run setup from the builder, or run the setup command with sudo on Linux."
    return 1
  fi

  # The old installer could leave only its package manager half-configured.
  # Clear that failed package so apt can install Ubuntu dependencies again.
  local pacman_state
  pacman_state=$(dpkg-query -W -f='${db:Status-Status}' devkitpro-pacman 2>/dev/null) || pacman_state=
  case "$pacman_state" in
    half-configured|unpacked)
      say "Recovering from an incomplete devkitPro package manager installation..."
      as_root dpkg --remove devkitpro-pacman || {
        hint "Open the build details to see why Ubuntu could not clear the incomplete devkitPro package, then retry setup."
        return 1
      }
      ;;
  esac

  say "Installing system packages..."
  as_root env DEBIAN_FRONTEND=noninteractive apt-get update -qq &&
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      make git patch python3 python3-pip build-essential rsync imagemagick libpng-dev curl wget xz-utils ca-certificates gnupg >/dev/null || {
    die "Could not install the Linux build tools (see the apt output above)."
    hint "Check your internet connection, wait for any other package installer to finish, then retry setup."
    return 1
  }
  if devkitpro_ready "$DKP"; then
    ok "devkitARM already installed"
  else
    install_devkitpro || {
      die "Could not install the devkitPro 3DS tools (see the output above)."
      hint "Check your internet connection and free disk space, then retry setup."
      return 1
    }
  fi
  say "Installing Python modules (ndspy, pillow, numpy)..."
  as_root python3 -m pip install -q --no-input --break-system-packages ndspy pillow numpy 2>/dev/null ||
    as_root python3 -m pip install -q --no-input ndspy pillow numpy || {
    die "Could not install the Python modules (see the pip output above)."
    hint "Check your internet connection and retry setup."
    return 1
  }
}
