#!/usr/bin/env bash
# Build platform-native installers from standalone bundles.
#
# Usage:
#   ./packaging/scripts/build-installer.sh --platform linux --arch x86_64
#   ./packaging/scripts/build-installer.sh --platform darwin --arch universal2
#   ./packaging/scripts/build-installer.sh --platform windows --arch amd64
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

PLATFORM=""
ARCH=""
SEMVER=""
BUNDLE_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform) PLATFORM="$2"; shift 2 ;;
        --arch) ARCH="$2"; shift 2 ;;
        --semver) SEMVER="$2"; shift 2 ;;
        --bundle-dir) BUNDLE_DIR="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

[[ -n "$PLATFORM" && -n "$ARCH" ]] || { echo "Usage: $0 --platform <linux|darwin|windows> --arch <x86_64|aarch64|universal2|amd64>" >&2; exit 2; }

# Auto-detect semver from pyproject.toml
if [[ -z "$SEMVER" ]]; then
    SEMVER="$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")"
fi

# Find standalone bundle
if [[ -z "$BUNDLE_DIR" ]]; then
    BUNDLE_NAME="intellect-agent-${SEMVER}-${PLATFORM}-${ARCH}"
    if [[ "$PLATFORM" == "windows" ]]; then
        BUNDLE_ARCHIVE="$ROOT/dist/standalone/${BUNDLE_NAME}.zip"
    else
        BUNDLE_ARCHIVE="$ROOT/dist/standalone/${BUNDLE_NAME}.tar.gz"
    fi

    if [[ ! -f "$BUNDLE_ARCHIVE" ]]; then
        echo "Standalone bundle not found: $BUNDLE_ARCHIVE" >&2
        echo "Run packaging/scripts/build-standalone-bundle.sh first." >&2
        exit 1
    fi

    BUNDLE_DIR="$ROOT/dist/standalone/${BUNDLE_NAME}"
    if [[ ! -d "$BUNDLE_DIR" ]]; then
        echo "Extracting $BUNDLE_ARCHIVE..."
        mkdir -p "$ROOT/dist/standalone"
        if [[ "$PLATFORM" == "windows" ]]; then
            unzip -q "$BUNDLE_ARCHIVE" -d "$ROOT/dist/standalone"
        else
            tar xzf "$BUNDLE_ARCHIVE" -C "$ROOT/dist/standalone"
        fi
    fi
fi

[[ -d "$BUNDLE_DIR" ]] || { echo "Bundle directory not found: $BUNDLE_DIR" >&2; exit 1; }

OUT_DIR="$ROOT/dist/installers"
mkdir -p "$OUT_DIR"

log() { printf '\e[1;35m[installer]\e[0m %s\n' "$*"; }
die() { printf '\e[1;31m[installer] ERROR:\e[0m %s\n' "$*" >&2; exit 1; }

log "Building installer for ${PLATFORM}/${ARCH} v${SEMVER}"

case "$PLATFORM" in
    linux)
        log "→ AppImage"
        bash "$ROOT/packaging/installer/linux/build-appimage.sh" \
            --bundle-dir "$BUNDLE_DIR" --semver "$SEMVER" --arch "$ARCH" || \
            log "(AppImage build skipped — install appimagetool to enable)"
        log "→ deb"
        bash "$ROOT/packaging/installer/linux/build-deb.sh" \
            --bundle-dir "$BUNDLE_DIR" --semver "$SEMVER" --arch "$ARCH"
        ;;
    darwin)
        log "→ DMG"
        bash "$ROOT/packaging/installer/macos/build-dmg.sh" \
            --bundle-dir "$BUNDLE_DIR" --semver "$SEMVER"
        ;;
    windows)
        if command -v makensis >/dev/null 2>&1; then
            log "→ NSIS exe"
            bash "$ROOT/packaging/installer/windows/build-nsis.sh" \
                --bundle-dir "$BUNDLE_DIR" --semver "$SEMVER" --arch "$ARCH"
        else
            log "(NSIS build skipped — install makensis to enable)"
        fi
        ;;
esac

log "Installers in: ${OUT_DIR}/"
ls -lh "$OUT_DIR/" 2>/dev/null || true
