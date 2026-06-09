"""ResponseStore CacheBackend tests (P4a / T12)."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent.cache.response_store import RedisResponseStore, SqliteResponseStore, create_response_store


def test_sqlite_response_store_roundtrip():
    store = SqliteResponseStore(max_size=10, db_path=":memory:")
    store.put("resp-1", {"id": "resp-1", "output": "hi"})
    assert store.get("resp-1") == {"id": "resp-1", "output": "hi"}
    store.set_conversation("conv-a", "resp-1")
    assert store.get_conversation("conv-a") == "resp-1"
    assert store.delete("resp-1") is True
    assert store.get("resp-1") is None
    store.close()


def test_redis_response_store_uses_cache_backend():
    cache = MagicMock()
    cache.get_sync.return_value = None
    store = RedisResponseStore(cache, max_size=5)

    store.put("r1", {"ok": True})
    cache.set_sync.assert_called_once()
    key, payload = cache.set_sync.call_args[0][:2]
    assert "r1" in key
    assert payload == {"ok": True}

    cache.get_sync.return_value = {"ok": True}
    assert store.get("r1") == {"ok": True}


def test_create_response_store_uses_sqlite_when_memory_backend(monkeypatch):
    monkeypatch.delenv("INTELLECT_CACHE_BACKEND", raising=False)
    store = create_response_store({"cache": {"backend": "memory"}})
    assert isinstance(store, SqliteResponseStore)
    store.close()
