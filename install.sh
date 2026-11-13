#!/usr/bin/env bash
# Install Vesper on macOS or Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/Kernel-Hunter/vesper/main/install.sh | bash
#
# By default this installs the `vesper` terminal agent:
#   1. uv (https://docs.astral.sh/uv/), if it is not there yet. uv brings its own Python,
#      so you do not need one installed.
#   2. Vesper from the latest release (a source archive, so git is not needed), as an
#      isolated uv tool in ~/.local/bin.
#
# Options (pass them after `bash -s --`):
#   --app       also install the desktop app (Apple Silicon Mac or x86_64 Linux)
#   --app-only  only the desktop app
#
# The desktop download is checked against SHA256SUMS.txt from the same release. Files
# downloaded with curl carry no quarantine flag, so macOS opens the app without the
# Gatekeeper "cannot be opened" dialog.
set -euo pipefail

REPO="Kernel-Hunter/vesper"
API="https://api.github.com/repos/$REPO/releases/latest"
ICON_URL="https://raw.githubusercontent.com/$REPO/main/packaging/icon.png"
APP="Vesper"
WANT_CLI=1
WANT_APP=0
for arg in "$@"; do
  case "$arg" in
    --app) WANT_APP=1 ;;
    --app-only) WANT_APP=1; WANT_CLI=0 ;;
    -h|--help) sed -n '2,19p' "$0" 2>/dev/null || true; exit 0 ;;
    *) echo "Unknown option: $arg (use --app or --app-only)" >&2; exit 1 ;;
  esac
done
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp:?}"' EXIT

say() { printf '\033[1;35m==>\033[0m %s\n' "$*"; }

# Print the download URL of the latest release asset whose name ends with $1.
asset_url() {
  curl -fsSL -H "Accept: application/vnd.github+json" "$API" \
    | grep -o '"browser_download_url": *"[^"]*"' \
    | sed -E 's/.*"(https:[^"]*)"$/\1/' \
    | grep -E -- "$1\$" \
    | head -n 1
}

# Check a download against SHA256SUMS.txt from the same release.
verify() { # verify <file> <asset name>
  sums="$(asset_url 'SHA256SUMS\.txt')"
  [ -n "$sums" ] || { echo "The release has no SHA256SUMS.txt; refusing to install an unchecked file." >&2; exit 1; }
  curl -fsSL "$sums" -o "$tmp/SHA256SUMS.txt"
  want="$(grep -E " \*?$2\$" "$tmp/SHA256SUMS.txt" | head -n 1 | cut -d' ' -f1)"
  if command -v sha256sum >/dev/null 2>&1; then got="$(sha256sum "$1" | cut -d' ' -f1)"
  else got="$(shasum -a 256 "$1" | cut -d' ' -f1)"; fi
  if [ -z "$want" ] || [ "$want" != "$got" ]; then
    echo "Checksum mismatch for $2. The download may be damaged or tampered with; nothing was installed." >&2
    exit 1
  fi
  say "Checksum OK"
}

install_app() {
  case "$(uname -s)" in
    Darwin)
      if [ "$(uname -m)" != "arm64" ]; then
        echo "This build is for Apple Silicon Macs (M1 or newer). Intel Macs: run from source, see the README." >&2
        exit 1
      fi
      url="$(asset_url '-mac-arm64\.zip')"
      [ -n "$url" ] || { echo "Could not find the macOS download in the latest release." >&2; exit 1; }
      say "Downloading Vesper for macOS…"
      curl -fL --progress-bar "$url" -o "$tmp/app.zip"
      verify "$tmp/app.zip" "${url##*/}"
      ditto -x -k "$tmp/app.zip" "$tmp/unpacked"
      [ -d "$tmp/unpacked/$APP.app" ] || { echo "The download did not contain $APP.app." >&2; exit 1; }
      dest="/Applications"
      [ -w "$dest" ] || { dest="$HOME/Applications"; mkdir -p "$dest"; }
      say "Installing to $dest"
      rm -rf "${dest:?}/$APP.app"
      mv "$tmp/unpacked/$APP.app" "$dest/"
      xattr -dr com.apple.quarantine "$dest/$APP.app" 2>/dev/null || true
      say "Desktop app installed in $dest (find it in Launchpad or Spotlight)."
      ;;
    Linux)
      if [ "$(uname -m)" != "x86_64" ]; then
        echo "This build is for x86_64 Linux. Other CPUs: run from source, see the README." >&2
        exit 1
      fi
      url="$(asset_url '\.AppImage')"
      [ -n "$url" ] || { echo "Could not find the Linux download in the latest release." >&2; exit 1; }
      say "Downloading Vesper for Linux…"
      curl -fL --progress-bar "$url" -o "$tmp/Vesper.AppImage"
      verify "$tmp/Vesper.AppImage" "${url##*/}"
      dest="$HOME/.local/share/vesper"
      mkdir -p "$dest" "$HOME/.local/bin" "$HOME/.local/share/applications"
      rm -f "${dest:?}/Vesper.AppImage"
      mv "$tmp/Vesper.AppImage" "$dest/Vesper.AppImage"
      chmod +x "$dest/Vesper.AppImage"
      curl -fsSL "$ICON_URL" -o "$dest/icon.png" || true
      ln -sf "$dest/Vesper.AppImage" "$HOME/.local/bin/vesper-app"
      cat > "$HOME/.local/share/applications/vesper.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Vesper
Comment=AI agent for your folders and documents
Exec=$dest/Vesper.AppImage %U
Icon=$dest/icon.png
Terminal=false
Categories=Development;
EOF
      say "Desktop app installed. Start it from your app menu (\"Vesper\") or run: vesper-app"
      say "If it does not start, install libfuse2 (Ubuntu/Debian: sudo apt install libfuse2)."
      ;;
    *)
      echo "Use install.ps1 on Windows." >&2
      exit 1
      ;;
    esac
  }

# Latest release tag, or "main" when there is no release yet.
latest_tag() {
  curl -fsSL -H "Accept: application/vnd.github+json" "$API" 2>/dev/null \
    | grep -o '"tag_name": *"[^"]*"' | head -n 1 | sed -E 's/.*"([^"]*)"$/\1/' || true
}

install_cli() {
  uv_bin="$(command -v uv || true)"
  for c in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    [ -z "$uv_bin" ] && [ -x "$c" ] && uv_bin="$c"
  done
  if [ -z "$uv_bin" ]; then
    say "Installing uv (Python package manager from Astral)…"
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
    uv_bin="$HOME/.local/bin/uv"
    [ -x "$uv_bin" ] || uv_bin="$HOME/.cargo/bin/uv"
    [ -x "$uv_bin" ] || { echo "uv did not install. See https://docs.astral.sh/uv/ and run this again." >&2; exit 1; }
  fi
  ref="$(latest_tag)"
  ref="${ref:-main}"
  say "Installing the vesper command ($ref)…"
  if ! "$uv_bin" tool install --force --python 3.12 "vesper-agent @ https://github.com/$REPO/archive/$ref.tar.gz" >"$tmp/uv.log" 2>&1; then
    cat "$tmp/uv.log" >&2
    echo "Installing vesper failed (output above)." >&2
    exit 1
  fi
  "$uv_bin" tool update-shell >/dev/null 2>&1 || true
  bin_dir="$("$uv_bin" tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"
  say "Installed $bin_dir/vesper"
}

[ "$WANT_CLI" = 1 ] && install_cli
[ "$WANT_APP" = 1 ] && install_app

echo
if [ "$WANT_CLI" = 1 ]; then
  if command -v vesper >/dev/null 2>&1; then
    say "Done. Go to any folder and run: vesper"
  else
    say "Done. Open a new terminal, go to any folder and run: vesper"
  fi
  [ "$WANT_APP" = 1 ] || say "Desktop app: run this again with --app, or type /app inside vesper."
else
  say "Done."
fi
