"""
WebUI High-Availability helpers.

When ``INTELLECT_WEBUI_WORKERS`` is > 1, validates that the configured
storage, cache, and event backends support concurrent access (PostgreSQL
+ Redis).  Single-worker deployments (the default) require no additional
configuration.
"""

from __future__ import annotations

import os
from typing import Any


def parse_webui_worker_count() -> int:
    """Return the configured WebUI worker count (default: 1)."""
    try:
        return max(1, int(os.environ.get("INTELLECT_WEBUI_WORKERS", "1")))
    except (TypeError, ValueError):
        return 1


def validate_webui_ha_startup(config: dict[str, Any] | None = None) -> list[str]:
    """Validate HA configuration when workers > 1.

    Returns a list of error strings (empty = all good).
    """
    workers = parse_webui_worker_count()
    if workers <= 1:
        return []

    errors: list[str] = []
    cfg = config or {}

    storage = (cfg.get("storage") or {}).get("backend") or os.environ.get(
        "INTELLECT_STORAGE_BACKEND", "sqlite"
    )
    cache = (cfg.get("cache") or {}).get("backend") or os.environ.get(
        "INTELLECT_CACHE_BACKEND", "memory"
    )
    events = (cfg.get("events") or {}).get("backend") or os.environ.get(
        "INTELLECT_EVENTS_BACKEND", "memory"
    )

    if storage != "postgresql":
        errors.append(
            f"INTELLECT_WEBUI_WORKERS={workers} requires storage.backend=postgresql "
            f"(current: {storage})"
        )
    if cache != "redis":
        errors.append(
            f"INTELLECT_WEBUI_WORKERS={workers} requires cache.backend=redis "
            f"(current: {cache})"
        )
    if events != "redis":
        errors.append(
            f"INTELLECT_WEBUI_WORKERS={workers} requires events.backend=redis "
            f"(current: {events})"
        )
    return errors


def format_webui_ha_errors(errors: list[str]) -> str:
    """Format HA validation errors as a human-readable string."""
    if not errors:
        return ""
    lines = [
        "[!!] WARNING: Multi-worker WebUI (INTELLECT_WEBUI_WORKERS > 1) requires:",
        "",
    ]
    for e in errors:
        lines.append(f"  - {e}")
    lines.append("")
    lines.append("  Falling back to single-worker mode.")
    return "\n".join(lines)
