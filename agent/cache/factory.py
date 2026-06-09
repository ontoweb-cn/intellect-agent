"""Cache backend factory."""

from __future__ import annotations

import logging
import os

from agent.cache.memory_cache import MemoryCache

logger = logging.getLogger(__name__)

_redis_fallback_warned = False


def get_cache_backend_name(config: dict | None = None) -> str:
    cfg = config or {}
    cache = cfg.get("cache") if isinstance(cfg.get("cache"), dict) else {}
    env = (os.environ.get("INTELLECT_CACHE_BACKEND") or "").strip().lower()
    if env in ("memory", "redis"):
        return env
    name = str(cache.get("backend") or "memory").strip().lower()
    return name if name in ("memory", "redis") else "memory"


def create_cache_backend(config: dict | None = None):
    name = get_cache_backend_name(config)
    if name == "memory":
        return MemoryCache(config)
    if name == "redis":
        global _redis_fallback_warned
        try:
            from agent.cache.redis_cache import RedisCache

            return RedisCache(config)
        except Exception as exc:
            if not _redis_fallback_warned:
                logger.warning(
                    "cache.backend=redis unavailable (%s); falling back to memory",
                    exc,
                )
                _redis_fallback_warned = True
            return MemoryCache(config)
    raise ValueError(f"Unknown cache backend: {name!r}")
