#!/usr/bin/env bash
# Install Vesper on macOS (Apple Silicon) or Linux (x86_64).
#
#   curl -fsSL https://raw.githubusercontent.com/Kernel-Hunter/vesper/main/install.sh | bash
#
# Files downloaded with curl are not marked "from the internet" (no quarantine
# flag), so macOS opens the app without the "cannot be opened" Gatekeeper
# dialog. Read this script before running it if you like: it only writes the
# app, a launcher and a menu entry.
set -euo pipefail

REPO="Kernel-Hunter/vesper"
API="https://api.github.com/repos/$REPO/releases/latest"
ICON_URL="https://raw.githubusercontent.com/$REPO/main/packaging/icon.png"
APP="Vesper"
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
    for old in "/Applications/RAG-Help-Desk.app" "$HOME/Applications/RAG-Help-Desk.app"; do
      if [ -d "$old" ]; then
        rm -rf "${old:?}"
        say "Replaced the old RAG Help Desk app ($old) with Vesper."
      fi
    done
    say "Done. Opening Vesper (find it later in Launchpad or Spotlight)."
    open "$dest/$APP.app"
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
    ln -sf "$dest/Vesper.AppImage" "$HOME/.local/bin/vesper"
    cat > "$HOME/.local/share/applications/vesper.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Vesper
Comment=Chat with your documents using any AI model
Exec=$dest/Vesper.AppImage %U
Icon=$dest/icon.png
Terminal=false
Categories=Office;
EOF
    # Remove the launcher of the old RAG Help Desk build, if any.
    old="$HOME/.local/share/rag-help-desk"
    if [ -d "$old" ]; then
      rm -rf "${old:?}" "$HOME/.local/bin/rag-help-desk" "$HOME/.local/share/applications/rag-help-desk.desktop"
      say "Replaced the old RAG Help Desk app with Vesper."
    fi
    say "Done. Start it from your app menu (\"Vesper\") or run: vesper"
    say "If it does not start, install libfuse2 (Ubuntu/Debian: sudo apt install libfuse2)."
    ;;
  *)
    echo "Use install.ps1 on Windows." >&2
    exit 1
    ;;
esac
