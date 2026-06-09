"""Redis cache backend (P4a)."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from agent.cache.backend import CacheBackend
from agent.events.redis_url import resolve_redis_url

logger = logging.getLogger(__name__)

_KEY_PREFIX = "intellect:cache:"


def resolve_cache_redis_url(config: dict | None = None) -> str:
    cfg = config or {}
    cache = cfg.get("cache") if isinstance(cfg.get("cache"), dict) else {}
    redis = cache.get("redis") if isinstance(cache.get("redis"), dict) else {}
    return resolve_redis_url(redis, default_db=0)


def _encode(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def _decode(raw: str) -> Any:
    return json.loads(raw)


class RedisCache(CacheBackend):
    """Shared Redis cache with sync-first access for worker threads."""

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        cache_cfg = self._config.get("cache") if isinstance(self._config.get("cache"), dict) else {}
        memory_cfg = cache_cfg.get("memory") if isinstance(cache_cfg.get("memory"), dict) else {}
        self._default_ttl = int(memory_cfg.get("agent_cache_ttl_seconds", 3600))
        self._client = None
        self._client_lock = threading.Lock()

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is not None:
                return self._client
            try:
                import redis
            except ImportError as exc:
                raise ImportError(
                    "Redis cache requires redis>=5. "
                    "Install: pip install 'intellect-agent[high-performance]'"
                ) from exc
            url = resolve_cache_redis_url(self._config)
            self._client = redis.Redis.from_url(url, decode_responses=True)
            self._client.ping()
            return self._client

    def _key(self, key: str) -> str:
        return f"{_KEY_PREFIX}{key}"

    async def get(self, key: str) -> Any | None:
        return self.get_sync(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.set_sync(key, value, ttl=ttl)

    async def delete(self, key: str) -> None:
        self.delete_sync(key)

    def delete_sync(self, key: str) -> None:
        client = self._ensure_client()
        client.delete(self._key(key))

    async def exists(self, key: str) -> bool:
        client = self._ensure_client()
        return bool(client.exists(self._key(key)))

    async def clear(self) -> None:
        client = self._ensure_client()
        cursor = 0
        pattern = f"{_KEY_PREFIX}*"
        while True:
            cursor, keys = client.scan(cursor=cursor, match=pattern, count=200)
            if keys:
                client.delete(*keys)
            if cursor == 0:
                break

    def get_sync(self, key: str) -> Any | None:
        client = self._ensure_client()
        raw = client.get(self._key(key))
        if raw is None:
            return None
        try:
            return _decode(raw)
        except (TypeError, json.JSONDecodeError):
            logger.debug("Redis cache decode failed for key %s", key)
            return None

    def set_sync(self, key: str, value: Any, ttl: int | None = None) -> None:
        client = self._ensure_client()
        ttl_seconds = self._default_ttl if ttl is None else ttl
        payload = _encode(value)
        namespaced = self._key(key)
        if ttl_seconds and ttl_seconds > 0:
            client.setex(namespaced, ttl_seconds, payload)
        else:
            client.set(namespaced, payload)

    def close(self) -> None:
        with self._client_lock:
            client = self._client
            self._client = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
