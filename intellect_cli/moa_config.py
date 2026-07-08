"""MoA preset configuration — model lists, temperatures, aggregator (HP-302).

Presets are loaded from ``{INTELLECT_HOME}/moa/presets.yaml`` with
built-in defaults as fallback.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

from intellect_constants import get_intellect_home

logger = logging.getLogger(__name__)

DEFAULT_PRESETS: dict[str, dict[str, Any]] = {
    "default": {
        "references": [
            {"provider": "anthropic", "model": "claude-sonnet-4-6"},
            {"provider": "openai", "model": "gpt-5.4-pro"},
            {"provider": "google", "model": "gemini-2.5-pro"},
            {"provider": "deepseek", "model": "deepseek-v3.2"},
        ],
        "aggregator": {"provider": "anthropic", "model": "claude-opus-4-8"},
        "reference_temperature": 0.6,
        "aggregator_temperature": 0.4,
    },
}


def _presets_dir() -> Path:
    return get_intellect_home() / "moa"


def _presets_file() -> Path:
    return _presets_dir() / "presets.yaml"


def load_presets() -> dict[str, dict[str, Any]]:
    """Load all presets — user file overrides built-in by name."""
    presets = dict(DEFAULT_PRESETS)
    pf = _presets_file()
    if pf.is_file():
        try:
            user = yaml.safe_load(pf.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                presets.update(user)
        except Exception:
            logger.debug("moa_config: failed to load %s", pf, exc_info=True)
    return presets


def load_preset(name: str = "default") -> Optional[dict[str, Any]]:
    """Load a single preset by name."""
    return load_presets().get(name)


def list_presets() -> list[str]:
    """Return preset names."""
    return sorted(load_presets().keys())


def preset_summary(name: str) -> Optional[dict[str, Any]]:
    """Human-readable summary of a preset."""
    preset = load_preset(name)
    if not preset:
        return None
    ref_names = [f"{r['provider']}/{r['model']}" for r in preset.get("references", [])]
    agg = preset.get("aggregator", {})
    return {
        "name": name,
        "reference_models": ref_names,
        "aggregator": f"{agg.get('provider', '?')}/{agg.get('model', '?')}",
        "reference_count": len(ref_names),
        "estimated_cost_note": f"Each request makes {len(ref_names) + 1} LLM calls",
    }
