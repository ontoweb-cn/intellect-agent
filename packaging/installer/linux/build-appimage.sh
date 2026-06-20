#!/usr/bin/env bash
# Build a self-contained AppImage from a standalone bundle directory.
#
# Usage:
#   ./build-appimage.sh --bundle-dir /path/to/bundle --semver 0.6.5 --arch x86_64
#
# Requires: appimagetool (https://github.com/AppImage/AppImageKit)
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BUNDLE_DIR="" SEMVER="" ARCH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bundle-dir) BUNDLE_DIR="$2"; shift 2 ;;
        --semver) SEMVER="$2"; shift 2 ;;
        --arch) ARCH="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

[[ -d "$BUNDLE_DIR" ]] || { echo "ERROR: bundle dir not found: $BUNDLE_DIR" >&2; exit 1; }
[[ -n "$SEMVER" ]] || SEMVER="0.0.0"
[[ -n "$ARCH" ]] || ARCH=x86_64

if ! command -v appimagetool >/dev/null 2>&1; then
    echo "Skipping AppImage: appimagetool not installed." >&2
    echo "Install: wget https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" >&2
    exit 0
fi

APPDIR="$(mktemp -d)"
mkdir -p "$APPDIR/usr"

# Copy bundle contents
cp -r "$BUNDLE_DIR"/* "$APPDIR/usr/"

# AppRun script
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
BUNDLE="$HERE/usr"

export PATH="${BUNDLE}/node-runtime/bin:${BUNDLE}/venv/bin:${PATH}"
export VIRTUAL_ENV="${BUNDLE}/venv"
export PLAYWRIGHT_BROWSERS_PATH="${BUNDLE}/playwright-browsers"

exec "${BUNDLE}/venv/bin/intellect" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

# Desktop entry
cat > "$APPDIR/intellect-agent.desktop" <<DESKTOP
[Desktop Entry]
Name=Intellect Agent
Comment=Self-improving AI agent framework
Exec=intellect chat
Icon=intellect-agent
Terminal=true
Type=Application
Categories=Development;ArtificialIntelligence;
DESKTOP

# Placeholder icon
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
if [[ -f "$ROOT/webui/static/favicon.png" ]]; then
    cp "$ROOT/webui/static/favicon.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/intellect-agent.png"
fi
cp "$APPDIR/intellect-agent.desktop" "$APPDIR/usr/share/applications/" 2>/dev/null || true

OUT_DIR="$ROOT/dist/installers"
mkdir -p "$OUT_DIR"
OUT_FILE="${OUT_DIR}/intellect-agent-${SEMVER}-${ARCH}.AppImage"

ARCH="$ARCH" appimagetool "$APPDIR" "$OUT_FILE" 2>&1 || {
    echo "AppImage build failed (non-fatal)" >&2
    exit 0
}

chmod +x "$OUT_FILE"
echo "→ ${OUT_FILE} ($(du -sh "$OUT_FILE" | cut -f1))"

rm -rf "$APPDIR"
