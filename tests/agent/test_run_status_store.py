"""Run status store tests (P4a / T3)."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent.cache.run_status import RunStatusStore, create_run_status_store


def test_run_status_store_local_roundtrip():
    store = RunStatusStore()
    store.set("run-1", {"run_id": "run-1", "status": "running"})
    assert store.get("run-1")["status"] == "running"
    store.pop("run-1")
    assert store.get("run-1") is None


def test_run_status_store_reads_from_cache_when_local_miss():
    cache = MagicMock()
    cache.get_sync.return_value = {"run_id": "run-2", "status": "completed"}
    store = RunStatusStore(cache)
    assert store.get("run-2")["status"] == "completed"


def test_create_run_status_store_memory_backend():
    store = create_run_status_store({"cache": {"backend": "memory"}})
    assert isinstance(store, RunStatusStore)
    assert store._cache is None
