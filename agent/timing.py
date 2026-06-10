"""Lightweight timing decorator for hot-path profiling.

Usage:
    from agent.timing import timed

    @timed                      # auto-label from func.__name__
    @timed(label="api_call")    # explicit label
"""

from __future__ import annotations

import functools
import logging
import time

logger = logging.getLogger(__name__)


def timed(func=None, *, label: str = ""):
    """Decorator: log DEBUG-level timing for slow operations (>50ms)."""
    def _decorator(fn):
        @functools.wraps(fn)
        def _wrapper(*a, **kw):
            _label = label or fn.__name__
            t0 = time.perf_counter()
            try:
                return fn(*a, **kw)
            finally:
                elapsed = time.perf_counter() - t0
                if elapsed > 0.050:
                    logger.debug("timing %s: %.3fs", _label, elapsed)
        return _wrapper
    return _decorator(func) if func else _decorator
