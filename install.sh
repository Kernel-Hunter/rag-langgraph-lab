#!/usr/bin/env bash
# Install RAG Help Desk on macOS (Apple Silicon) or Linux (x86_64).
#
#   curl -fsSL https://raw.githubusercontent.com/Kernel-Hunter/rag-langgraph-lab/main/install.sh | bash
#
# Files downloaded with curl are not marked "from the internet", so macOS opens
# the app without the "cannot be opened" Gatekeeper dialog. Read this script
# before running it if you like: it only writes the app folder, a launcher and
# a menu entry.
set -euo pipefail

REPO="Kernel-Hunter/rag-langgraph-lab"
BASE="https://github.com/$REPO/releases/latest/download"
APP="RAG-Help-Desk"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

say() { printf '\033[1;35m==>\033[0m %s\n' "$*"; }

case "$(uname -s)" in
  Darwin)
    if [ "$(uname -m)" != "arm64" ]; then
      echo "This build is for Apple Silicon Macs (M1 or newer). Intel Macs: run from source, see the README." >&2
      exit 1
    fi
    say "Downloading RAG Help Desk for macOS…"
    curl -fL --progress-bar "$BASE/$APP-macOS-arm64.zip" -o "$tmp/app.zip"
    dest="/Applications"
    [ -w "$dest" ] || { dest="$HOME/Applications"; mkdir -p "$dest"; }
    say "Installing to $dest"
    rm -rf "${dest:?}/$APP.app"
    ditto -x -k "$tmp/app.zip" "$dest"
    xattr -dr com.apple.quarantine "$dest/$APP.app" 2>/dev/null || true
    say "Done. Opening the app (find it later in Launchpad or Spotlight: \"RAG Help Desk\")."
    open "$dest/$APP.app"
    ;;
  Linux)
    if [ "$(uname -m)" != "x86_64" ]; then
      echo "This build is for x86_64 Linux. Other CPUs: run from source, see the README." >&2
      exit 1
    fi
    say "Downloading RAG Help Desk for Linux…"
    curl -fL --progress-bar "$BASE/$APP-Linux-x86_64.tar.gz" -o "$tmp/app.tar.gz"
    dest="$HOME/.local/share/rag-help-desk"
    rm -rf "${dest:?}"
    mkdir -p "$dest" "$HOME/.local/bin" "$HOME/.local/share/applications"
    tar -xzf "$tmp/app.tar.gz" -C "$dest"
    ln -sf "$dest/$APP/$APP" "$HOME/.local/bin/rag-help-desk"
    cat > "$HOME/.local/share/applications/rag-help-desk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=RAG Help Desk
Comment=Ask your documents with any AI model
Exec=$dest/$APP/$APP
Icon=$dest/$APP/icon.png
Terminal=false
Categories=Education;Office;
EOF
    say "Done. Start it from your app menu (\"RAG Help Desk\") or run: rag-help-desk"
    ;;
  *)
    echo "Use install.ps1 on Windows." >&2
    exit 1
    ;;
esac
