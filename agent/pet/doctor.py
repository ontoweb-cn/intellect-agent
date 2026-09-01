"""Pets doctor checks (PT-01): config + store + manifest health."""

from __future__ import annotations

from typing import List

from agent.pet import store
from agent.pet.constants import STATES


def run_pet_doctor(intellect_home=None) -> List[str]:
    """Return fix-instruction issues (empty = healthy). Prints progress."""
    issues: List[str] = []
    from intellect_cli.config import load_config

    cfg = (load_config() or {}).get("display", {}).get("pet", {}) or {}
    slug = str(cfg.get("slug") or "")
    enabled = bool(cfg.get("enabled"))
    if enabled and not slug:
        issues.append(
            "display.pet.enabled is true but display.pet.slug is empty — "
            "run `intellect pets select <slug>`."
        )
    if slug:
        meta = store.load_pet(slug)
        if meta is None:
            issues.append(
                f"Selected pet '{slug}' is not installed — "
                "run `intellect pets install {slug}`."
            )
    installed = store.installed()
    for meta in installed:
        state_meta = meta.get("states")
        if isinstance(state_meta, list) and len(state_meta) < len(STATES):
            issues.append(
                f"Pet '{meta.get('slug')}' has {len(state_meta)} states "
                f"(expected {len(STATES)}) — animation may loop oddly."
            )
    print(f"  ✓ Pets: {len(installed)} installed, "
          f"{'enabled' if enabled else 'disabled'}"
          + (f" ({slug})" if slug else ""))
    return issues
