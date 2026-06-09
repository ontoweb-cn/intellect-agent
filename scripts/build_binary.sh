#!/usr/bin/env bash
# Build standalone Nuitka binaries for intellect, intellect-agent, and intellect-acp.
#
# Usage:
#   ./scripts/build_binary.sh --onefile --all
#   INTELLECT_BUILD_OUTPUT=./dist/bin ./scripts/build_binary.sh --onefile --all
#
# Environment:
#   INTELLECT_BUILD_OUTPUT  Output directory (default: ./dist/bin)
#   NUITKA_LTO              yes|no (default: yes on native builds)
#   NUITKA_JOBS             Parallel C compiler jobs (default: nproc)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="onefile"
BUILD_ALL=false
NUITKA_LTO="${NUITKA_LTO:-yes}"
NUITKA_JOBS="${NUITKA_JOBS:-$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 2)}"
OUTPUT="${INTELLECT_BUILD_OUTPUT:-${ROOT}/dist/bin}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --onefile)     MODE="onefile"; shift ;;
        --standalone)  MODE="standalone"; shift ;;
        --all)         BUILD_ALL=true; shift ;;
        --help|-h)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

if ! $BUILD_ALL; then
    echo "Specify --all to build intellect, intellect-agent, and intellect-acp." >&2
    exit 1
fi

require_cmd() {
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || {
            echo "ERROR: '$cmd' not found." >&2
            exit 1
        }
    done
}

require_cmd uv python3

mkdir -p "$OUTPUT" /tmp/intellect-launch
touch ./README.md
uv sync --frozen --extra all
uv pip install --no-cache-dir 'nuitka==2.8.10' cython ordered-set

printf 'import sys\nfrom intellect_cli.main import main\nsys.exit(main() or 0)\n' \
    > /tmp/intellect-launch/intellect.py
printf 'import sys\nfrom run_agent import main\nsys.exit(main() or 0)\n' \
    > /tmp/intellect-launch/intellect-agent.py
printf 'import sys\nfrom acp_adapter.entry import main\nsys.exit(main() or 0)\n' \
    > /tmp/intellect-launch/intellect-acp.py

export PYTHONPATH="$ROOT"

nuitka_base=(
    "--${MODE}"
    "--output-dir=${OUTPUT}"
    "--lto=${NUITKA_LTO}"
    "--jobs=${NUITKA_JOBS}"
    --python-flag=-OO
    --noinclude-setuptools-mode=nofollow
    --enable-plugin=anti-bloat
    --assume-yes-for-downloads
)

if [[ "${NUITKA_LOW_MEMORY:-}" == "1" ]]; then
    nuitka_base+=(--low-memory)
fi

echo "=== Building intellect (CLI) ==="
uv run python -m nuitka "${nuitka_base[@]}" \
    --output-filename=intellect \
    --include-module=unittest.mock \
    --include-data-dir=skills=skills \
    --include-data-dir=optional-skills=optional-skills \
    --include-data-dir=locales=locales \
    --include-data-dir=assets=assets \
    --include-package-data=agent \
    --include-package-data=gateway \
    /tmp/intellect-launch/intellect.py

echo "=== Building intellect-agent ==="
uv run python -m nuitka "${nuitka_base[@]}" \
    --output-filename=intellect-agent \
    --include-module=run_agent \
    --include-data-dir=skills=skills \
    --include-data-dir=optional-skills=optional-skills \
    --include-data-dir=locales=locales \
    --include-package-data=agent \
    /tmp/intellect-launch/intellect-agent.py

echo "=== Building intellect-acp ==="
uv run python -m nuitka "${nuitka_base[@]}" \
    --output-filename=intellect-acp \
    --include-package=acp_adapter \
    /tmp/intellect-launch/intellect-acp.py

for name in intellect intellect-agent intellect-acp; do
    if [[ ! -x "${OUTPUT}/${name}" ]]; then
        echo "ERROR: missing ${OUTPUT}/${name}" >&2
        exit 1
    fi
done

echo "=== Agent binaries ready in ${OUTPUT} ==="
