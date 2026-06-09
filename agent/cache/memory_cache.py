"""In-process LRU cache backend (default)."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

from agent.cache.backend import CacheBackend


class MemoryCache(CacheBackend):
    """Thread-safe in-memory cache with optional TTL."""

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        cache_cfg = cfg.get("cache") if isinstance(cfg.get("cache"), dict) else {}
        memory_cfg = cache_cfg.get("memory") if isinstance(cache_cfg.get("memory"), dict) else {}
        self._max_entries = int(memory_cfg.get("agent_cache_size", 128))
        self._default_ttl = int(memory_cfg.get("agent_cache_ttl_seconds", 3600))
        self._lock = threading.Lock()
        self._data: OrderedDict[str, tuple[Any, float | None]] = OrderedDict()

    async def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at is not None and time.time() >= expires_at:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        ttl_seconds = self._default_ttl if ttl is None else ttl
        expires_at = time.time() + ttl_seconds if ttl_seconds > 0 else None
        with self._lock:
            self._data[key] = (value, expires_at)
            self._data.move_to_end(key)
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)

    async def delete(self, key: str) -> None:
        self.delete_sync(key)

    def delete_sync(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    async def exists(self, key: str) -> bool:
        return (await self.get(key)) is not None

    async def clear(self) -> None:
        with self._lock:
            self._data.clear()
