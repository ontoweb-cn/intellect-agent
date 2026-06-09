"""RedisCache unit tests (mocked redis client)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent.cache.redis_cache import RedisCache, _KEY_PREFIX


def test_redis_cache_set_get_roundtrip():
    client = MagicMock()
    client.ping.return_value = True
    client.get.return_value = None
    cache = RedisCache({"cache": {"redis": {"url": "redis://127.0.0.1:6379/0"}}})
    cache._client = client

    cache.set_sync("k1", {"ok": True}, ttl=60)

    client.setex.assert_called_once()
    args, kwargs = client.setex.call_args
    assert args[0] == f"{_KEY_PREFIX}k1"
    assert args[1] == 60
    assert json.loads(args[2]) == {"ok": True}

    client.get.return_value = args[2]
    assert cache.get_sync("k1") == {"ok": True}


def test_create_cache_backend_falls_back_to_memory(monkeypatch):
    from agent.cache import create_cache_backend
    from agent.cache.memory_cache import MemoryCache

    import agent.cache.factory as factory_mod

    factory_mod._redis_fallback_warned = False
    monkeypatch.setenv("INTELLECT_CACHE_BACKEND", "redis")

    with patch("agent.cache.redis_cache.RedisCache", side_effect=OSError("down")):
        backend = create_cache_backend({})

    assert isinstance(backend, MemoryCache)
