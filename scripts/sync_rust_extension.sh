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

# macOS/arm64: the linker's ad-hoc signature can be page-level invalid after
# maturin's wheel round-trip (kernel kills the process with "Code Signature
# Invalid" on first import). Re-sign both copies defensively — no-op intent,
# fixes the load. See docs/plans/2026-10-05-rust-migration-next-steps-plan.md.
if [[ "$(uname)" == "Darwin" ]] && command -v codesign >/dev/null 2>&1; then
  for so in "$ROOT/intellect_community_core/"*.so "$VENV_SO"; do
    codesign -f -s - "$so" >/dev/null 2>&1 || true
  done
fi
