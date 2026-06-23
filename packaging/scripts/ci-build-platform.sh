#!/usr/bin/env bash
# Build per-platform release artifacts for CI matrix jobs.
#
# Usage (env vars required):
#   PLATFORM=linux|darwin|windows
#   ARCH=x86_64|aarch64|universal2|amd64
#   PYTHON_WHEEL_DIR=path/to/dir/with/intellect_agent-*.whl  (optional for native)
#
# Outputs to dist/out/:
#   intellect_community_core-*.whl
#   intellect-{platform}-{arch}-{semver}.tar.gz|zip  (when PYTHON_WHEEL_DIR set)
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

: "${PLATFORM:?PLATFORM required (linux|darwin|windows)}"
: "${ARCH:?ARCH required}"

log() { printf '[ci-build] %s\n' "$*"; }

SEMVER="$(grep -E '^version\s*=' "$ROOT/pyproject.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
OUT="$ROOT/dist/out"
rm -rf "$OUT"
mkdir -p "$OUT"

# ── TUI bundle ──────────────────────────────────────────────────────────────
log "Building TUI..."
(
  cd ui-tui
  npm ci
  npm run build
)
mkdir -p intellect_cli/tui_dist
cp ui-tui/dist/entry.js intellect_cli/tui_dist/entry.js

# ── Rust wheel ──────────────────────────────────────────────────────────────
log "Building Rust wheel (${PLATFORM}/${ARCH})..."
(
  cd rust-core
  case "${PLATFORM}-${ARCH}" in
    linux-x86_64)
      maturin build --release
      ;;
    linux-aarch64)
      maturin build --release
      ;;
    darwin-universal2)
      rustup target add aarch64-apple-darwin x86_64-apple-darwin
      maturin build --release --target universal2-apple-darwin
      ;;
    windows-amd64)
      maturin build --release
      ;;
    *)
      log "Unsupported matrix cell: ${PLATFORM}-${ARCH}"
      exit 2
      ;;
  esac
)

RS_WHL="$(ls -1t rust-core/target/wheels/intellect_community_core-*.whl | head -1)"
cp -f "$RS_WHL" "$OUT/"

# ── Native bundle (optional — needs Python wheel from build-python job) ─────
if [ -n "${PYTHON_WHEEL_DIR:-}" ] && [ -d "$PYTHON_WHEEL_DIR" ]; then
  PY_WHL="$(ls -1 "$PYTHON_WHEEL_DIR"/intellect_agent-"${SEMVER}"-py3-none-any.whl 2>/dev/null | head -1 || true)"
  if [ -n "$PY_WHL" ]; then
    log "Building native bundle..."
    BUNDLE="intellect-${PLATFORM}-${ARCH}-${SEMVER}"
    STAGING="$ROOT/dist/native-staging/${BUNDLE}"
    rm -rf "$STAGING"
    mkdir -p "$STAGING/bin" "$STAGING/venv"

    if [ "$PLATFORM" = "windows" ]; then
      python3 -m venv "$STAGING/venv"
      "$STAGING/venv/Scripts/python.exe" -m pip install -q --upgrade pip
      "$STAGING/venv/Scripts/python.exe" -m pip install -q "$RS_WHL" "$PY_WHL"
    else
      python3 -m venv "$STAGING/venv"
      # shellcheck disable=SC1091
      source "$STAGING/venv/bin/activate"
      pip install -q --upgrade pip
      pip install -q "$RS_WHL" "$PY_WHL"
    fi

    if [ -d skills ]; then
      ln -sf "$ROOT/skills" "$STAGING/skills"
    fi
    echo "$SEMVER" > "$STAGING/VERSION"

    cat > "$STAGING/bin/intellect" <<'WRAPPER'
#!/usr/bin/env bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ROOT/venv/bin/intellect" "$@"
WRAPPER
    chmod +x "$STAGING/bin/intellect"

    if [ "$PLATFORM" = "windows" ]; then
      cat > "$STAGING/bin/intellect.cmd" <<'WRAPPER'
@echo off
set ROOT=%~dp0..
"%ROOT%\\venv\\Scripts\\intellect.exe" %*
WRAPPER
    fi

    mkdir -p "$ROOT/dist/native-staging"
    if [ "$PLATFORM" = "windows" ]; then
      powershell -NoProfile -Command \
        "Compress-Archive -Path '${STAGING}' -DestinationPath '${OUT}/${BUNDLE}.zip' -Force"
    else
      tar -czf "$OUT/${BUNDLE}.tar.gz" -C dist/native-staging "${BUNDLE}"
    fi
    log "Native bundle → $OUT/"
  else
    log "Python wheel not found in ${PYTHON_WHEEL_DIR}; skipping native bundle"
  fi
fi

log "Done: $(ls -1 "$OUT")"
