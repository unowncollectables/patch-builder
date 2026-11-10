#!/usr/bin/env bash
# Shared by the three drivers. Uses their as_root, say, ok, die and hint functions.
install_devkitpro_pacman() (
  local url='https://apt.devkitpro.org/install-devkitpro-pacman'
  local agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'
  local work installer attempt first_line
  work=$(mktemp -d) || {
    hint "Could not create a temporary folder for devkitPro. Check your free disk space and try again."
    return 1
  }
  trap 'rm -rf -- "$work"' EXIT
  installer="$work/install-devkitpro-pacman"
  for attempt in 1 2 3; do
    if curl --fail --silent --show-error --location --connect-timeout 10 --max-time 30 \
        --user-agent "$agent" --output "$installer" "$url" ||
       wget --no-verbose --timeout=20 --tries=1 --user-agent="$agent" --output-document="$installer" "$url"; then
      if IFS= read -r first_line < "$installer" && [[ "$first_line" == '#!'*bash* ]] && bash -n "$installer"; then
        # The upstream script calls apt-get without -y. Set that default only for this run.
        printf '%s\n' 'APT::Get::Assume-Yes "true";' > "$work/apt.conf" || return 1
        as_root env DEBIAN_FRONTEND=noninteractive APT_CONFIG="$work/apt.conf" bash "$installer" </dev/null || {
          die "The devkitPro installer ran but failed (see the installer output above)."
          hint "Check the build details for the failed package or download, then retry setup."
          return 1
        }
        return 0
      fi
      say "The devkitPro server did not return the expected installer script."
    fi
    if [ "$attempt" -lt 3 ]; then
      say "Could not download the devkitPro installer. Retrying..."
      sleep 3
    fi
  done
  die "Could not download the devkitPro installer after three attempts."
  hint "Check that this computer can reach apt.devkitpro.org, then retry setup."
  return 1
)

setup_build_tools() {
  if [ "$(id -u)" -ne 0 ] && ! sudo -n true 2>/dev/null; then
    die "Setup needs administrator access."
    hint "Run setup from the builder, or run the setup command with sudo on Linux."
    return 1
  fi
  say "Installing system packages..."
  as_root env DEBIAN_FRONTEND=noninteractive apt-get update -qq &&
    as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      make git patch python3 python3-pip build-essential rsync imagemagick curl wget xz-utils ca-certificates gnupg >/dev/null || {
    die "Could not install the Linux build tools (see the apt output above)."
    hint "Check your internet connection, wait for any other package installer to finish, then retry setup."
    return 1
  }
  if [ ! -x "$DKP/devkitARM/bin/arm-none-eabi-gcc" ] ||
     [ ! -f "$DKP/libctru/include/3ds.h" ] ||
     [ ! -f "$DKP/libctru/include/citro3d.h" ] ||
     [ ! -x "$DKP/tools/bin/3dsxtool" ] ||
     [ ! -x "$DKP/tools/bin/picasso" ]; then
    say "Installing devkitPro (official installer)..."
    if ! command -v dkp-pacman >/dev/null; then
      install_devkitpro_pacman || return 1
    fi
    as_root dkp-pacman -Sy --noconfirm 3ds-dev </dev/null || {
      die "Could not install the devkitPro 3DS packages (see the pacman output above)."
      hint "Check the build details for the failed package or download, then retry setup."
      return 1
    }
  else
    ok "devkitARM already installed"
  fi
  say "Installing Python modules (ndspy, pillow, numpy)..."
  as_root python3 -m pip install -q --no-input --break-system-packages ndspy pillow numpy 2>/dev/null ||
    as_root python3 -m pip install -q --no-input ndspy pillow numpy || {
    die "Could not install the Python modules (see the pip output above)."
    hint "Check your internet connection and retry setup."
    return 1
  }
}
