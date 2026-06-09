"""Shared idempotency cache for API server (P4a / T2)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

_PREFIX = "intellect:idem:"


class SharedIdempotencyCache:
    """Idempotency-Key dedup with CacheBackend for completed responses."""

    def __init__(
        self,
        cache=None,
        *,
        max_items: int = 1000,
        ttl_seconds: int = 300,
    ):
        self._cache = cache
        self._local = OrderedDict()
        self._inflight: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._ttl = ttl_seconds
        self._max = max_items

    def _purge_local(self) -> None:
        now = time.time()
        expired = [k for k, v in self._local.items() if now - v["ts"] > self._ttl]
        for k in expired:
            self._local.pop(k, None)
        while len(self._local) > self._max:
            self._local.popitem(last=False)

    def _cache_get(self, key: str) -> dict | None:
        if self._cache is None:
            return None
        try:
            raw = self._cache.get_sync(f"{_PREFIX}{key}")
            return raw if isinstance(raw, dict) else None
        except Exception:
            return None

    def _cache_set(self, key: str, payload: dict) -> None:
        if self._cache is None:
            return
        try:
            self._cache.set_sync(f"{_PREFIX}{key}", payload, ttl=self._ttl)
        except Exception:
            logger.debug("idempotency cache set failed", exc_info=True)

    async def get_or_set(self, key: str, fingerprint: str, compute_coro):
        self._purge_local()
        item = self._local.get(key)
        if item and item["fp"] == fingerprint:
            return item["resp"]

        cached = self._cache_get(key)
        if cached and cached.get("fp") == fingerprint:
            resp = cached.get("resp")
            self._local[key] = {"resp": resp, "fp": fingerprint, "ts": time.time()}
            return resp

        inflight_key = (key, fingerprint)
        task = self._inflight.get(inflight_key)
        if task is None:

            async def _compute_and_store():
                resp = await compute_coro()
                entry = {"resp": resp, "fp": fingerprint, "ts": time.time()}
                self._local[key] = entry
                self._cache_set(key, entry)
                self._purge_local()
                return resp

            task = asyncio.create_task(_compute_and_store())
            self._inflight[inflight_key] = task

            def _clear(done_task: asyncio.Task[Any]) -> None:
                if self._inflight.get(inflight_key) is done_task:
                    self._inflight.pop(inflight_key, None)

            task.add_done_callback(_clear)

        return await asyncio.shield(task)


def create_idempotency_cache(config: dict | None = None) -> SharedIdempotencyCache:
    cfg = config or {}
    cache_cfg = cfg.get("cache") if isinstance(cfg.get("cache"), dict) else {}
    memory_cfg = cache_cfg.get("memory") if isinstance(cache_cfg.get("memory"), dict) else {}
    max_items = int(memory_cfg.get("idempotency_cache_size", 1000))
    ttl = int(memory_cfg.get("idempotency_cache_ttl_seconds", 300))
    cache = None
    try:
        from agent.cache.factory import create_cache_backend, get_cache_backend_name

        if get_cache_backend_name(cfg) == "redis":
            backend = create_cache_backend(cfg)
            if type(backend).__name__ != "MemoryCache":
                cache = backend
    except Exception:
        logger.debug("shared idempotency cache backend init failed", exc_info=True)
    return SharedIdempotencyCache(cache, max_items=max_items, ttl_seconds=ttl)
