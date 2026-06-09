"""Resolve Redis connection URLs from config + env."""

from __future__ import annotations

import os
from typing import Any, Mapping


def resolve_redis_url(
    section: Mapping[str, Any] | None,
    *,
    default_db: int,
) -> str:
    """Build a Redis URL from a config subsection (``cache.redis`` / ``events.redis``)."""
    sec = section if isinstance(section, Mapping) else {}
    url = str(sec.get("url") or os.getenv("INTELLECT_REDIS_URL") or "").strip()
    if url:
        return url
    host = str(sec.get("host") or "localhost").strip() or "localhost"
    try:
        port = int(sec.get("port") or 6379)
    except (TypeError, ValueError):
        port = 6379
    if sec.get("db") is not None:
        try:
            db = int(sec.get("db"))
        except (TypeError, ValueError):
            db = default_db
    else:
        db = default_db
    password = str(sec.get("password") or "").strip()
    if password:
        return f"redis://:{password}@{host}:{port}/{db}"
    return f"redis://{host}:{port}/{db}"


def resolve_events_redis_url(config: dict | None = None) -> str:
    cfg = config or {}
    events = cfg.get("events") if isinstance(cfg.get("events"), dict) else {}
    redis = events.get("redis") if isinstance(events.get("redis"), dict) else {}
    return resolve_redis_url(redis, default_db=1)
