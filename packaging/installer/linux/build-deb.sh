#!/usr/bin/env bash
# Build a .deb package from a standalone bundle directory.
#
# Usage:
#   ./build-deb.sh --bundle-dir /path/to/bundle --semver 0.6.5 --arch x86_64
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
[[ -n "$SEMVER" ]] || { echo "ERROR: --semver required" >&2; exit 1; }
[[ -n "$ARCH" ]] || { echo "ERROR: --arch required" >&2; exit 1; }

# Map arch to dpkg architecture
case "$ARCH" in
    x86_64)  DEB_ARCH=amd64 ;;
    aarch64) DEB_ARCH=arm64 ;;
    *) DEB_ARCH="$ARCH" ;;
esac

PKG_NAME="intellect-agent"
INSTALL_DIR="/usr/lib/${PKG_NAME}"
BIN_LINK="/usr/bin/intellect"

DEB_ROOT="$(mktemp -d)"
TARGET="${DEB_ROOT}${INSTALL_DIR}"
mkdir -p "${DEB_ROOT}/DEBIAN" "$TARGET" "${DEB_ROOT}/usr/bin"

# Copy bundle files
cp -r "$BUNDLE_DIR"/* "$TARGET/"

# Create symlink in package
ln -sf "${INSTALL_DIR}/bin/intellect" "${DEB_ROOT}/${BIN_LINK}"
ln -sf "${INSTALL_DIR}/venv/bin/intellect-agent" "${DEB_ROOT}/usr/bin/intellect-agent"
ln -sf "${INSTALL_DIR}/venv/bin/intellect-acp" "${DEB_ROOT}/usr/bin/intellect-acp"

# Package metadata
cat > "${DEB_ROOT}/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${SEMVER}
Architecture: ${DEB_ARCH}
Maintainer: ONTOWEB <intellect@ontoweb.cn>
Installed-Size: $(du -sk "$BUNDLE_DIR" | cut -f1)
Section: devel
Priority: optional
Homepage: https://gitee.com/ontoweb/intellect-agent
Description: Self-improving AI agent framework
 A self-improving, multi-modal AI agent that supports chat, voice,
 terminal and browser automation. This package provides a standalone
 installation with bundled Python, Node.js, and Rust runtime.
 .
 Includes CLI (intellect), agent runtime (intellect-agent), and
 ACP protocol adapter (intellect-acp).
Depends: ca-certificates
Recommends: ripgrep, ffmpeg
EOF

# Post-install: set permissions
cat > "${DEB_ROOT}/DEBIAN/postinst" <<'POSTINST'
#!/bin/bash
set -e
# Ensure wrapper scripts are executable
chmod +x /usr/lib/intellect-agent/bin/intellect 2>/dev/null || true
chmod +x /usr/lib/intellect-agent/venv/bin/* 2>/dev/null || true
# Update desktop database if icons were installed
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database 2>/dev/null || true
fi
POSTINST
chmod 0755 "${DEB_ROOT}/DEBIAN/postinst"

# Build .deb
OUT_DIR="$ROOT/dist/installers"
mkdir -p "$OUT_DIR"
DEB_FILE="${OUT_DIR}/${PKG_NAME}_${SEMVER}_${DEB_ARCH}.deb"

dpkg-deb --build "$DEB_ROOT" "$DEB_FILE"
echo "→ ${DEB_FILE} ($(du -sh "$DEB_FILE" | cut -f1))"

rm -rf "$DEB_ROOT"
