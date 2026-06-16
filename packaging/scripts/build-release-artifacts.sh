#!/usr/bin/env bash
# Build Intellect Agent release artifacts for the current platform.
#
# Maintainer script — implements docs/packaging/design.md §5.
# Does NOT publish; outputs to ./dist/ and rust-core/target/wheels/.
#
# Usage:
#   ./packaging/scripts/build-release-artifacts.sh              # current platform
#   ./packaging/scripts/build-release-artifacts.sh --skip-tui   # faster iteration
#   ./packaging/scripts/build-release-artifacts.sh --rust-only
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

SKIP_TUI=0
RUST_ONLY=0
RELEASE=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-tui) SKIP_TUI=1 ;;
        --rust-only) RUST_ONLY=1 ;;
        --dev) RELEASE=0 ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

log() { printf '[build] %s\n' "$*"; }
die() { printf '[build] ERROR: %s\n' "$*" >&2; exit 1; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

# ── Preflight ───────────────────────────────────────────────────────────────

require_cmd python3
require_cmd cargo

if ! command -v uv >/dev/null 2>&1; then
    die "uv is required. Install: https://docs.astral.sh/uv/"
fi

if ! command -v maturin >/dev/null 2>&1; then
    log "Installing maturin into active environment..."
    uv pip install maturin
fi

mkdir -p dist

# ── TUI bundle (bundled into intellect_cli/tui_dist/) ───────────────────────

if [[ "$RUST_ONLY" -eq 0 && "$SKIP_TUI" -eq 0 ]]; then
    log "Building TUI (ui-tui)..."
    require_cmd npm
    (
        cd ui-tui
        npm ci
        npm run build
    )
    mkdir -p intellect_cli/tui_dist
    cp ui-tui/dist/entry.js intellect_cli/tui_dist/entry.js
    log "TUI bundle → intellect_cli/tui_dist/entry.js"
fi

# ── Rust extension wheel ─────────────────────────────────────────────────────

log "Building intellect_community_core (maturin)..."
MATURIN_ARGS=(build)
if [[ "$RELEASE" -eq 1 ]]; then
    MATURIN_ARGS+=(--release)
fi

(
    cd rust-core
    maturin "${MATURIN_ARGS[@]}"
)

RUST_WHEEL="$(ls -1t rust-core/target/wheels/intellect_community_core-*.whl 2>/dev/null | head -1 || true)"
if [[ -z "$RUST_WHEEL" ]]; then
    die "No Rust wheel found under rust-core/target/wheels/"
fi
cp -f "$RUST_WHEEL" dist/
log "Rust wheel → dist/$(basename "$RUST_WHEEL")"

if [[ "$RUST_ONLY" -eq 1 ]]; then
    log "Done (--rust-only)."
    exit 0
fi

# ── Python sdist + wheel ────────────────────────────────────────────────────

log "Bundling install scripts into intellect_cli/scripts/..."
mkdir -p intellect_cli/scripts
cp -f scripts/install.sh intellect_cli/scripts/install.sh
cp -f scripts/install.ps1 intellect_cli/scripts/install.ps1

log "Building intellect-agent (uv build)..."
uv build --sdist --wheel

log "Artifacts in dist/:"
ls -la dist/

log "Verify Rust import (install wheel into temp venv):"
TMPVENV="$(mktemp -d)"
uv venv "$TMPVENV" --python 3.12
# shellcheck disable=SC1091
source "$TMPVENV/bin/activate"
uv pip install "$RUST_WHEEL"
python -c "import intellect_community_core as c; assert hasattr(c, 'detect_dangerous_command_rs')"
log "intellect_community_core import OK"
rm -rf "$TMPVENV"

log "Done."
