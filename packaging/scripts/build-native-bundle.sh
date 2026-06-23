#!/usr/bin/env bash
# Build a platform-native self-contained bundle for Gitee Release.
#
# Output: dist/native/intellect-{platform}-{arch}-{semver}.tar.gz|.zip
# Design: docs/packaging/gitee-releases.md §2.2
#
# Usage:
#   ./packaging/scripts/build-native-bundle.sh
#   ./packaging/scripts/build-native-bundle.sh --platform linux --arch x86_64
#   ./packaging/scripts/build-native-bundle.sh --platform windows --arch amd64
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PLATFORM=""
ARCH=""
SEMVER=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform) PLATFORM="$2"; shift 2 ;;
        --arch) ARCH="$2"; shift 2 ;;
        --semver) SEMVER="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

# Auto-detect platform/arch when omitted
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
        linux|darwin)
            ARCH="$(uname -m)"
            [[ "$ARCH" == "x86_64" ]] && ARCH=x86_64
            [[ "$ARCH" == "aarch64" || "$ARCH" == "arm64" ]] && ARCH=aarch64
            [[ "$PLATFORM" == "darwin" && "$ARCH" == "x86_64" ]] && ARCH=universal2
            [[ "$PLATFORM" == "darwin" && "$ARCH" == "aarch64" ]] && ARCH=universal2
            ;;
        windows) ARCH=amd64 ;;
    esac
fi

if [[ -z "$SEMVER" ]]; then
    SEMVER="$(grep -E '^version\s*=' "$ROOT/pyproject.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
fi

log() { printf '[native-bundle] %s\n' "$*"; }

# Step 1: ensure wheels exist
log "Building release artifacts (wheels)..."
"$ROOT/packaging/scripts/build-release-artifacts.sh"

PY_WHL="$(ls -1 dist/intellect_agent-"${SEMVER}"-py3-none-any.whl 2>/dev/null | head -1 || true)"
RS_WHL="$(ls -1 dist/intellect_community_core-*.whl 2>/dev/null | head -1 || true)"
[[ -n "$PY_WHL" && -n "$RS_WHL" ]] || { echo "Missing wheels in dist/"; exit 1; }

BUNDLE_NAME="intellect-${PLATFORM}-${ARCH}-${SEMVER}"
STAGING="$ROOT/dist/native/${BUNDLE_NAME}"
rm -rf "$STAGING"
mkdir -p "$STAGING/bin" "$STAGING/venv"

log "Creating venv in ${STAGING}/venv ..."
python3 -m venv "$STAGING/venv"
# shellcheck disable=SC1091
source "$STAGING/venv/bin/activate"
pip install -q --upgrade pip
pip install -q "$RS_WHL" "$PY_WHL"

# Bundled skills (symlink for dev bundle; release CI may copy)
if [[ -d skills ]]; then
    ln -sf "$ROOT/skills" "$STAGING/skills"
fi

echo "$SEMVER" > "$STAGING/VERSION"

cat > "$STAGING/bin/intellect" <<'WRAPPER'
#!/usr/bin/env bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ROOT/venv/bin/intellect" "$@"
WRAPPER
chmod +x "$STAGING/bin/intellect"

cat > "$STAGING/README.txt" <<EOF
Intellect Agent ${SEMVER} (${PLATFORM}/${ARCH})
Data directory: ~/.intellect/  (or set INTELLECT_HOME)
Run: ./bin/intellect version
Docs: https://gitee.com/ontoweb/intellect-agent/tree/main/docs/packaging
EOF

mkdir -p dist/native
OUT_BASE="dist/native/${BUNDLE_NAME}"

if [[ "$PLATFORM" == "windows" ]]; then
    OUT="${OUT_BASE}.zip"
    log "Packaging ${OUT} ..."
    (cd dist/native && zip -rq "${BUNDLE_NAME}.zip" "${BUNDLE_NAME}")
else
    OUT="${OUT_BASE}.tar.gz"
    log "Packaging ${OUT} ..."
    tar -czf "$OUT" -C dist/native "${BUNDLE_NAME}"
fi

log "Done: ${OUT}"
log "Upload to Gitee Release:"
log "  https://gitee.com/ontoweb/intellect-agent/releases"
