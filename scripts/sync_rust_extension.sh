#!/usr/bin/env bash
# Sync built intellect_community_core .so from venv site-packages into the
# repo python-source tree (HP-205e). Run after ``maturin develop --release``.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

find_venv_so() {
  local venv_dir="$1"
  find "$venv_dir/lib" -path "*/site-packages/intellect_community_core/*.so" 2>/dev/null | head -1
}

VENV_SO=""
for candidate in "$ROOT/.venv" "$ROOT/venv" "$HOME/.intellect/intellect-agent/venv"; do
  if [[ -d "$candidate" ]]; then
    found="$(find_venv_so "$candidate" || true)"
    if [[ -n "$found" ]]; then
      VENV_SO="$found"
      break
    fi
  fi
done

if [[ -z "$VENV_SO" ]]; then
  echo "No intellect_community_core .so in venv — run: cd rust-core && maturin develop --release" >&2
  exit 1
fi

mkdir -p "$ROOT/intellect_community_core"
cp -f "$VENV_SO" "$ROOT/intellect_community_core/"
echo "Synced $(basename "$VENV_SO") -> intellect_community_core/ (from $VENV_SO)"
