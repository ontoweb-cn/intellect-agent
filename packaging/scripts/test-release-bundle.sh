#!/usr/bin/env bash
# Smoke-test a standalone release bundle.
#
# Usage:
#   ./test-release-bundle.sh /path/to/intellect-linux-x86_64-0.6.5.tar.gz
#   ./test-release-bundle.sh /path/to/intellect-windows-amd64-0.6.5.zip
#
set -euo pipefail

BUNDLE_FILE="${1:?Usage: $0 <bundle.tar.gz|.zip>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT

log() { printf '\e[1;32m[test]\e[0m %s\n' "$*"; }
fail() { printf '\e[1;31m[test] FAIL:\e[0m %s\n' "$*" >&2; FAILURES=$((FAILURES + 1)); }
FAILURES=0

log "Testing: $(basename "$BUNDLE_FILE")"

# ── Extract ─────────────────────────────────────────────────────────────────

case "$BUNDLE_FILE" in
    *.tar.gz) tar xzf "$BUNDLE_FILE" -C "$TEST_DIR" ;;
    *.zip) unzip -q "$BUNDLE_FILE" -d "$TEST_DIR" ;;
    *) echo "Unknown archive format" >&2; exit 1 ;;
esac

BUNDLE_DIR="$(find "$TEST_DIR" -name VERSION -exec dirname {} \; | head -1)"
if [[ -z "$BUNDLE_DIR" ]]; then
    fail "No VERSION file found in bundle"
    exit 1
fi
log "Bundle root: $BUNDLE_DIR"

# ── Test 1: Version file ────────────────────────────────────────────────────

EXPECTED_VERSION="$(cat "$ROOT/pyproject.toml" | grep "^version" | head -1 | sed 's/.*"\(.*\)".*/\1/')"
ACTUAL_VERSION="$(cat "$BUNDLE_DIR/VERSION")"
if [[ "$ACTUAL_VERSION" == "$EXPECTED_VERSION" ]]; then
    log "✓ version: ${ACTUAL_VERSION}"
else
    fail "Version mismatch: expected ${EXPECTED_VERSION}, got ${ACTUAL_VERSION}"
fi

# ── Test 2: Rust extension import ───────────────────────────────────────────

if [[ -f "$BUNDLE_DIR/venv/bin/python3" ]]; then
    VENV_PYTHON="$BUNDLE_DIR/venv/bin/python3"
elif [[ -f "$BUNDLE_DIR/venv/bin/python" ]]; then
    VENV_PYTHON="$BUNDLE_DIR/venv/bin/python"
elif [[ -f "$BUNDLE_DIR/venv/Scripts/python.exe" ]]; then
    VENV_PYTHON="$BUNDLE_DIR/venv/Scripts/python.exe"
else
    fail "Cannot find Python interpreter in bundle venv"
    exit 1
fi

if "$VENV_PYTHON" -c "
from intellect_rust import HAS_SANDBOX, HAS_COUNTERS, HAS_ERROR_CLASSIFIER
assert HAS_SANDBOX, 'missing sandbox'
assert HAS_COUNTERS, 'missing counters'
assert HAS_ERROR_CLASSIFIER, 'missing error classifier'
print('OK')
" 2>&1; then
    log "✓ Rust extension import"
else
    fail "Rust extension import failed"
fi

# ── Test 3: Entry point existence ───────────────────────────────────────────

for cmd in intellect intellect-agent intellect-acp; do
    if [[ -x "$BUNDLE_DIR/bin/${cmd}" ]] || [[ -f "$BUNDLE_DIR/bin/${cmd}.cmd" ]]; then
        log "✓ entry point: ${cmd}"
    else
        fail "Missing entry point: ${cmd}"
    fi
done

# ── Test 4: VERSION file readable ──────────────────────────────────────────

"$VENV_PYTHON" -c "
import sys
sys.path.insert(0, '$BUNDLE_DIR/venv/lib/python3.12/site-packages')
from intellect_cli import __version__
print(f'CLI version: {__version__}')
" 2>&1 && log "✓ CLI package importable" || fail "CLI package import failed"

# ── Test 5: Node.js (if bundled) ────────────────────────────────────────────

if [[ -x "$BUNDLE_DIR/node-runtime/bin/node" ]]; then
    NODE_VER="$("$BUNDLE_DIR/node-runtime/bin/node" --version 2>&1 || true)"
    if [[ -n "$NODE_VER" ]]; then
        log "✓ Node.js: ${NODE_VER}"
    else
        fail "Node.js binary found but not executable (glibc mismatch?)"
    fi
else
    log "  Node.js: not bundled (OK for pure-Python use)"
fi

# ── Result ──────────────────────────────────────────────────────────────────

if [[ "$FAILURES" -eq 0 ]]; then
    log "═══════════════════════════════════"
    log "All tests PASSED"
    log "═══════════════════════════════════"
    exit 0
else
    log "═══════════════════════════════════"
    log "${FAILURES} test(s) FAILED"
    log "═══════════════════════════════════"
    exit 1
fi
