"""Lightweight timing decorator for hot-path profiling.

Usage:
    from agent.timing import timed

    @timed                      # auto-label from func.__name__
    @timed(label="api_call")    # explicit label

Environment variables:
    INTELLECT_TIMING_JSON=1  — emit JSON lines with ``duration_ms`` instead of
                                human-readable debug messages.  Useful for
                                automated perf analysis.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

_JSON_MODE = os.environ.get("INTELLECT_TIMING_JSON", "").strip().lower() in ("1", "true", "yes")


def timed(func=None, *, label: str = ""):
    """Decorator: log DEBUG-level timing for slow operations (>50ms).

    When ``INTELLECT_TIMING_JSON=1`` is set, emits structured JSON::

        {"label": "api_call", "duration_ms": 123}

    on every call (not just slow ones) so scripts can aggregate.
    """
    def _decorator(fn):
        @functools.wraps(fn)
        def _wrapper(*a, **kw):
            _label = label or fn.__name__
            t0 = time.perf_counter()
            try:
                return fn(*a, **kw)
            finally:
                elapsed = time.perf_counter() - t0
                if _JSON_MODE:
                    logger.info(
                        json.dumps({"label": _label, "duration_ms": int(elapsed * 1000)})
                    )
                elif elapsed > 0.050:
                    logger.debug("timing %s: %.3fs", _label, elapsed)
        return _wrapper
    return _decorator(func) if func else _decorator
