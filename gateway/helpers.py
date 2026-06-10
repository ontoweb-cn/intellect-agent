"""Gateway utility helpers extracted from run.py."""

from __future__ import annotations

import os
from typing import Any, Optional


def gateway_platform_value(platform: Any) -> str:
    """Return a normalized gateway platform value for enums or raw strings."""
    return str(getattr(platform, "value", platform) or "").strip().lower()


def float_env(name: str, default: float) -> float:
    """Read an env var as float, falling back to ``default`` on typos/empty."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def coerce_gateway_timestamp(value: Any) -> Optional[float]:
    """Coerce various timestamp representations to a float (epoch seconds).

    Handles: int/float epoch, datetime, ISO-8601 string, None, empty.
    Returns None when the input cannot be interpreted as a timestamp.
    """
    from datetime import datetime as _datetime

    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, _datetime):
        return value.timestamp()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        from datetime import timezone as _timezone

        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                dt = _datetime.strptime(stripped, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_timezone.utc)
                return dt.timestamp()
            except ValueError:
                continue
    return None
