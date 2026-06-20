#!/usr/bin/env bash
# Build a Windows NSIS installer (.exe) from a standalone bundle directory.
#
# Usage:
#   ./build-nsis.sh --bundle-dir /path/to/bundle --semver 0.6.5 --arch amd64
#
# Requires: makensis (NSIS)
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
[[ -n "$ARCH" ]] || ARCH=amd64

if ! command -v makensis >/dev/null 2>&1; then
    echo "Skipping NSIS: makensis not installed. Install: choco install nsis" >&2
    exit 0
fi

OUT_DIR="$ROOT/dist/installers"
mkdir -p "$OUT_DIR"

# Convert to Windows path if running under MSYS2/Cygwin
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
    WIN_BUNDLE="$(cygpath -w "$BUNDLE_DIR" 2>/dev/null || echo "$BUNDLE_DIR")"
    WIN_OUT="$(cygpath -w "$OUT_DIR" 2>/dev/null || echo "$OUT_DIR")"
else
    WIN_BUNDLE="$BUNDLE_DIR"
    WIN_OUT="$OUT_DIR"
fi

NSI_SCRIPT="$ROOT/packaging/installer/windows/intellect-agent.nsi"

# Generate NSI with version substituted
TEMP_NSI="$(mktemp)"
sed "s/!define PRODUCT_VERSION .*/!define PRODUCT_VERSION \"${SEMVER}\"/" "$NSI_SCRIPT" > "$TEMP_NSI"

echo "Building NSIS installer..."
makensis \
    "-DSOURCE_DIR=${WIN_BUNDLE}" \
    "-DOUTPUT_DIR=${WIN_OUT}" \
    "$TEMP_NSI"

EXE_FILE="${OUT_DIR}/Intellect-Agent-${SEMVER}-Setup.exe"
if [[ -f "$EXE_FILE" ]]; then
    echo "→ ${EXE_FILE} ($(du -sh "$EXE_FILE" | cut -f1))"
else
    echo "⚠ NSIS build completed but .exe not found at expected path"
    ls -la "$OUT_DIR/"*.exe 2>/dev/null || true
fi

rm -f "$TEMP_NSI"
