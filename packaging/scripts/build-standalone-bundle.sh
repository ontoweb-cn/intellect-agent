#!/usr/bin/env bash
# Build a self-contained standalone bundle for binary distribution.
#
# Extends build-native-bundle.sh with bundled Node.js, agent-browser,
# WebUI assets, skills, locales, and smart wrapper scripts.
#
# Output: dist/standalone/intellect-{platform}-{arch}-{semver}.tar.gz|.zip
#
# Usage:
#   ./packaging/scripts/build-standalone-bundle.sh
#   ./packaging/scripts/build-standalone-bundle.sh --platform linux --arch x86_64
#   ./packaging/scripts/build-standalone-bundle.sh --platform windows --arch amd64 --skip-node
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PLATFORM=""
ARCH=""
SEMVER=""
SKIP_NODE=0
SKIP_WEBUI=0
SKIP_TUI=0
MATURIN_ARGS=(--release)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform) PLATFORM="$2"; shift 2 ;;
        --arch) ARCH="$2"; shift 2 ;;
        --semver) SEMVER="$2"; shift 2 ;;
        --skip-node) SKIP_NODE=1; shift ;;
        --skip-webui) SKIP_WEBUI=1; shift ;;
        --skip-tui) SKIP_TUI=1; shift ;;
        --dev) MATURIN_ARGS=(); shift ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

# ── Platform detection ─────────────────────────────────────────────────────

if [[ -z "$PLATFORM" ]]; then
    case "$(uname -s)" in
        Linux)  PLATFORM=linux ;;
        Darwin) PLATFORM=darwin ;;
        MINGW*|MSYS*|CYGWIN*) PLATFORM=windows ;;
        *) echo "Cannot detect platform; pass --platform" >&2; exit 2 ;;
    esac
fi

if [[ -z "$ARCH" ]]; then
    case "$PLATFORM" in
        linux)
            case "$(uname -m)" in
                x86_64) ARCH=x86_64 ;;
                aarch64|arm64) ARCH=aarch64 ;;
                *) ARCH="$(uname -m)" ;;
            esac
            ;;
        darwin) ARCH=universal2 ;;
        windows) ARCH=amd64 ;;
    esac
fi

if [[ -z "$SEMVER" ]]; then
    SEMVER="$(python3 -c "
import tomllib
print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])
")"
fi

log() { printf '\e[1;34m[standalone]\e[0m %s\n' "$*" >&2; }
die() { printf '\e[1;31m[standalone] ERROR:\e[0m %s\n' "$*" >&2; exit 1; }

# ── Preflight checks ───────────────────────────────────────────────────────

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}
require_cmd python3
require_cmd cargo
require_cmd maturin
command -v uv >/dev/null 2>&1 || pip install -q uv

log "Building standalone bundle for ${PLATFORM}/${ARCH} v${SEMVER}"

# ── Step 1: TUI ────────────────────────────────────────────────────────────

if [[ "$SKIP_TUI" -eq 0 ]]; then
    log "Building TUI (ui-tui)..."
    (
        cd ui-tui
        npm ci --silent 2>/dev/null || npm install --silent 2>/dev/null
        npm run build
    )
    mkdir -p intellect_cli/tui_dist
    if [[ -f ui-tui/dist/entry.js ]]; then
        cp ui-tui/dist/entry.js intellect_cli/tui_dist/entry.js
        log "TUI bundle → intellect_cli/tui_dist/entry.js"
    else
        log "⚠ TUI build did not produce entry.js (non-fatal for CLI-only usage)"
    fi
else
    log "TUI build skipped (--skip-tui)"
    mkdir -p intellect_cli/tui_dist
    touch intellect_cli/tui_dist/.gitkeep
fi

# ── Step 2: Rust wheel ─────────────────────────────────────────────────────

log "Building Rust wheel (${PLATFORM}/${ARCH})..."
(
    cd rust-core
    case "${PLATFORM}-${ARCH}" in
        linux-x86_64|linux-aarch64|windows-amd64)
            maturin build "${MATURIN_ARGS[@]}"
            ;;
        darwin-universal2)
            rustup target add aarch64-apple-darwin x86_64-apple-darwin
            maturin build "${MATURIN_ARGS[@]}" --target universal2-apple-darwin
            ;;
        *) die "Unsupported matrix cell: ${PLATFORM}-${ARCH}" ;;
    esac
)
RS_WHL="$(ls -1t rust-core/target/wheels/intellect_community_core-*.whl 2>/dev/null | head -1)"
[[ -n "$RS_WHL" ]] || die "No Rust wheel found"

log "Rust wheel: $(basename "$RS_WHL")"

# ── Step 3: Python wheel ───────────────────────────────────────────────────

log "Building Python sdist + wheel..."
mkdir -p intellect_cli/scripts
cp -f scripts/install.sh intellect_cli/scripts/install.sh
cp -f scripts/install.ps1 intellect_cli/scripts/install.ps1
uv build --sdist --wheel

PY_WHL="$(ls -1 dist/intellect_agent-"${SEMVER}"-py3-none-any.whl 2>/dev/null | head -1)"
[[ -n "$PY_WHL" ]] || die "No Python wheel found"

log "Python wheel: $(basename "$PY_WHL")"

# ── Step 4: Create staging directory ───────────────────────────────────────

BUNDLE_NAME="intellect-${PLATFORM}-${ARCH}-${SEMVER}"
STAGING="$ROOT/dist/standalone/${BUNDLE_NAME}"
rm -rf "$STAGING"
mkdir -p "$STAGING"/{bin,venv,node-runtime/bin}

log "Creating venv in ${STAGING}/venv ..."

if [[ "$PLATFORM" == "windows" ]]; then
    python3 -m venv "$STAGING/venv"
    # On CI Windows runners, use the venv directly
    VENV_PYTHON="$STAGING/venv/Scripts/python.exe"
    VENV_PIP="$STAGING/venv/Scripts/pip.exe"
    VENV_BIN="$STAGING/venv/Scripts"
else
    python3 -m venv "$STAGING/venv"
    VENV_PYTHON="$STAGING/venv/bin/python3"
    VENV_PIP="$STAGING/venv/bin/pip"
    VENV_BIN="$STAGING/venv/bin"
fi

"$VENV_PYTHON" -m pip install -q --upgrade pip
"$VENV_PIP" install -q "$RS_WHL" "$PY_WHL"

# ── Step 5: Verify Rust extension imports ──────────────────────────────────

log "Verifying Rust extension import..."
"$VENV_PYTHON" -c "
from intellect_rust import HAS_SANDBOX, HAS_COUNTERS, HAS_ERROR_CLASSIFIER
assert HAS_SANDBOX, 'Rust extension: sandbox functions missing!'
assert HAS_COUNTERS, 'Rust extension: counters missing!'
assert HAS_ERROR_CLASSIFIER, 'Rust extension: error classifier missing!'
print('Rust extension import: OK')
print('  - sandbox:   present')
print('  - counters:  present')
print('  - classifier: present')
" || die "Rust extension verification FAILED"

# ── Step 6: Bundle Node.js runtime ─────────────────────────────────────────

NODE_VERSION="22.17.0"
NODE_BUNDLE="$STAGING/node-runtime"

if [[ "$SKIP_NODE" -eq 0 ]]; then
    log "Bundling Node.js ${NODE_VERSION}..."
    case "${PLATFORM}-${ARCH}" in
        linux-x86_64)
            NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz"
            ;;
        linux-aarch64)
            NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-arm64.tar.xz"
            ;;
        darwin-*)
            NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-darwin-arm64.tar.gz"
            ;;
        windows-*)
            NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-win-x64.zip"
            ;;
    esac

    if [[ -n "${NODE_URL:-}" ]]; then
        NODE_TMP="$(mktemp -d)"
        NODE_ARCHIVE="$NODE_TMP/node.tar.xz"
        log "  downloading ${NODE_URL}..."
        curl -sSL "$NODE_URL" -o "$NODE_ARCHIVE"

        if [[ "$PLATFORM" == "windows" ]]; then
            unzip -q "$NODE_ARCHIVE" -d "$NODE_TMP"
            NODE_SRC="$(ls -d "$NODE_TMP"/node-v* 2>/dev/null | head -1)"
            cp -f "$NODE_SRC/node.exe" "$NODE_BUNDLE/bin/"
            cp -f "$NODE_SRC/npm.cmd" "$NODE_BUNDLE/bin/" 2>/dev/null || true
            cp -f "$NODE_SRC/npx.cmd" "$NODE_BUNDLE/bin/" 2>/dev/null || true
            # npm needs node_modules for npx
            if [[ -d "$NODE_SRC/node_modules" ]]; then
                cp -r "$NODE_SRC/node_modules" "$NODE_BUNDLE/"
            fi
        else
            tar -xJf "$NODE_ARCHIVE" -C "$NODE_TMP"
            NODE_SRC="$(ls -d "$NODE_TMP"/node-v* 2>/dev/null | head -1)"
            cp -f "$NODE_SRC/bin/node" "$NODE_BUNDLE/bin/"
            cp -f "$NODE_SRC/bin/npm" "$NODE_BUNDLE/bin/" 2>/dev/null || true
            cp -f "$NODE_SRC/bin/npx" "$NODE_BUNDLE/bin/" 2>/dev/null || true
            if [[ -d "$NODE_SRC/lib/node_modules" ]]; then
                mkdir -p "$NODE_BUNDLE/lib"
                cp -r "$NODE_SRC/lib/node_modules" "$NODE_BUNDLE/lib/"
            fi
        fi

        # Verify node works
        PATH="$NODE_BUNDLE/bin:$PATH" node --version || log "  ⚠ node binary may have incompatible glibc (non-fatal)"

        rm -rf "$NODE_TMP"
        log "  Node.js $(node --version 2>/dev/null || echo '?') bundled"
    else
        log "  ⚠ No prebuilt Node.js URL for ${PLATFORM}/${ARCH}; skipping"
        SKIP_NODE=1
    fi
fi

# ── Step 7: Install agent-browser ──────────────────────────────────────────

if [[ "$SKIP_NODE" -eq 0 ]]; then
    log "Installing agent-browser..."
    export PATH="$NODE_BUNDLE/bin:$PATH"
    if [[ "$PLATFORM" == "windows" ]]; then
        "$NODE_BUNDLE/bin/npm.cmd" install -g agent-browser --prefix "$NODE_BUNDLE" 2>&1 || \
            log "  ⚠ agent-browser npm install failed (non-fatal: may need platform-specific binary)"
    else
        npm install -g agent-browser --prefix "$NODE_BUNDLE" 2>&1 || \
            log "  ⚠ agent-browser npm install failed (non-fatal)"
    fi
    if [[ -f "$NODE_BUNDLE/bin/agent-browser" ]]; then
        log "  agent-browser installed"
    fi
fi

# ── Step 8: Bundle static assets ───────────────────────────────────────────

log "Bundling static assets..."

# WebUI
if [[ "$SKIP_WEBUI" -eq 0 && -d webui/static ]]; then
    cp -r webui/static "$STAGING/webui/"
    log "  webui/static → bundle"
fi

# Skills
if [[ -d skills ]]; then
    cp -r skills "$STAGING/skills"
    log "  skills/ → bundle"
fi

# Optional skills
if [[ -d optional-skills ]]; then
    cp -r optional-skills "$STAGING/optional-skills/"
    log "  optional-skills/ → bundle"
fi

# Locales
if [[ -d locales ]]; then
    cp -r locales "$STAGING/locales/"
    log "  locales/ → bundle"
fi

# Assets
if [[ -d assets ]]; then
    cp -r assets "$STAGING/assets/"
    log "  assets/ → bundle"
fi

# ── Step 9: Wrapper scripts ────────────────────────────────────────────────

echo "$SEMVER" > "$STAGING/VERSION"

cat > "$STAGING/bin/intellect" <<'WRAPPER'
#!/usr/bin/env bash
set -e
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

# Set PATH to include bundled binaries
export PATH="${ROOT}/node-runtime/bin:${ROOT}/venv/bin:${PATH}"

# Ensure we use the bundled Python
export VIRTUAL_ENV="${ROOT}/venv"

# Playwright browsers path
if [ -d "${ROOT}/playwright-browsers" ]; then
    export PLAYWRIGHT_BROWSERS_PATH="${ROOT}/playwright-browsers"
fi

# Determine which entry point to run
CMD_NAME="$(basename "$0")"
case "$CMD_NAME" in
    intellect-agent) exec "${ROOT}/venv/bin/intellect-agent" "$@" ;;
    intellect-acp)   exec "${ROOT}/venv/bin/intellect-acp" "$@" ;;
    *)               exec "${ROOT}/venv/bin/intellect" "$@" ;;
esac
WRAPPER
chmod +x "$STAGING/bin/intellect"

# Create symlinks for alternate entry points
ln -sf intellect "$STAGING/bin/intellect-agent"
ln -sf intellect "$STAGING/bin/intellect-acp"

# Windows .cmd wrapper
cat > "$STAGING/bin/intellect.cmd" <<WRAPPER
@echo off
set ROOT=%~dp0..
set PATH=%ROOT%\\node-runtime\\bin;%ROOT%\\venv\\Scripts;%PATH%
"%ROOT%\\venv\\Scripts\\intellect.exe" %*
WRAPPER

cat > "$STAGING/README.txt" <<EOF
Intellect Agent ${SEMVER} (${PLATFORM}/${ARCH})
==============================================

Self-contained bundle — no system Python, Node.js, or Rust required.

Run:      ./bin/intellect version
Chat:     ./bin/intellect chat
Gateway:  ./bin/intellect gateway start
ACP:      ./bin/intellect-acp

Data:  ~/.intellect/  (or set INTELLECT_HOME)

Documentation:
  https://gitee.com/ontoweb/intellect-agent/tree/main/docs/packaging
  https://gitee.com/ontoweb/intellect-agent/tree/main/docs
EOF

# ── Step 10: Package ──────────────────────────────────────────────────────

mkdir -p dist/standalone
OUT_BASE="dist/standalone/${BUNDLE_NAME}"

if [[ "$PLATFORM" == "windows" ]]; then
    OUT="${OUT_BASE}.zip"
    log "Packaging ${OUT}..."
    (cd dist/standalone && zip -rq "${BUNDLE_NAME}.zip" "${BUNDLE_NAME}")
else
    OUT="${OUT_BASE}.tar.gz"
    log "Packaging ${OUT}..."
    tar -czf "$OUT" -C dist/standalone "${BUNDLE_NAME}"
fi

# ── Summary ────────────────────────────────────────────────────────────────

cat <<EOF


═══ STANDALONE BUNDLE BUILD COMPLETE ═══

  Platform:  ${PLATFORM}
  Arch:      ${ARCH}
  Version:   ${SEMVER}
  Output:    ${OUT}
  Size:      $(du -sh "$OUT" | cut -f1)

  Contents:
    Rust extension:  $(basename "$RS_WHL")
    Node.js:         $([[ "$SKIP_NODE" -eq 0 ]] && echo "${NODE_VERSION}" || echo "skipped")
    agent-browser:   $([[ "$SKIP_NODE" -eq 0 && -f "$STAGING/node-runtime/bin/agent-browser" ]] && echo "installed" || echo "skipped")
    WebUI:           $([[ "$SKIP_WEBUI" -eq 0 && -d "$STAGING/webui" ]] && echo "bundled" || echo "skipped")
    Skills:          $([[ -d "$STAGING/skills" ]] && echo "bundled" || echo "skipped")

  Smoke test:
    STAGING=$STAGING
    \$STAGING/bin/intellect version

EOF

log "Done."
