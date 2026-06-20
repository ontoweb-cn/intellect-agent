#!/usr/bin/env bash
# Build a macOS .dmg disk image from a standalone bundle directory.
#
# Usage:
#   ./build-dmg.sh --bundle-dir /path/to/bundle --semver 0.6.5
#
# Requires: macOS with hdiutil (built-in)
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BUNDLE_DIR="" SEMVER=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bundle-dir) BUNDLE_DIR="$2"; shift 2 ;;
        --semver) SEMVER="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

[[ -d "$BUNDLE_DIR" ]] || { echo "ERROR: bundle dir not found: $BUNDLE_DIR" >&2; exit 1; }
[[ -n "$SEMVER" ]] || SEMVER="0.0.0"
[[ "$(uname -s)" == "Darwin" ]] || { echo "This script only runs on macOS." >&2; exit 1; }

APP_NAME="Intellect Agent"
VOL_NAME="${APP_NAME} ${SEMVER}"
DMG_STAGING="$(mktemp -d)"
OUT_DIR="$ROOT/dist/installers"
mkdir -p "$OUT_DIR"
DMG_FILE="${OUT_DIR}/intellect-agent-${SEMVER}-universal.dmg"

# Create Applications symlink for drag-to-install
mkdir -p "$DMG_STAGING/.background"
cp -r "$BUNDLE_DIR" "$DMG_STAGING/IntellectAgent"

# Simple staging: put the bundle and a README
cat > "$DMG_STAGING/README.txt" <<EOF
Intellect Agent ${SEMVER}
=========================

To install: drag the IntellectAgent folder to your Applications folder.

Then add to your PATH:
  echo 'export PATH="/Applications/IntellectAgent/bin:\$PATH"' >> ~/.zshrc

Or run directly:
  /Applications/IntellectAgent/bin/intellect version

Documentation:
  https://gitee.com/ontoweb/intellect-agent/tree/main/docs/packaging
EOF

# Build DMG
echo "Creating DMG at ${DMG_FILE}..."

hdiutil create \
    -volname "${VOL_NAME}" \
    -srcfolder "$DMG_STAGING" \
    -ov -format UDZO \
    -imagekey zlib-level=9 \
    "$DMG_FILE"

echo "→ ${DMG_FILE} ($(du -sh "$DMG_FILE" | cut -f1))"

rm -rf "$DMG_STAGING"
