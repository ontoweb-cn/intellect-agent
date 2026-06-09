"""Shared idempotency cache tests (P4a / T2)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from agent.cache.idempotency import SharedIdempotencyCache, create_idempotency_cache


@pytest.mark.asyncio
async def test_idempotency_cache_dedupes_inflight():
    cache = SharedIdempotencyCache(max_items=10, ttl_seconds=60)
    calls = 0

    async def compute():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"status": 200, "body": "ok"}

    fp = "fp-1"
    results = await asyncio.gather(
        cache.get_or_set("key-1", fp, compute),
        cache.get_or_set("key-1", fp, compute),
    )
    assert results == [{"status": 200, "body": "ok"}, {"status": 200, "body": "ok"}]
    assert calls == 1


@pytest.mark.asyncio
async def test_idempotency_cache_reads_shared_backend():
    backend = MagicMock()
    backend.get_sync.return_value = {
        "fp": "fp-shared",
        "resp": {"cached": True},
        "ts": 1.0,
    }
    cache = SharedIdempotencyCache(backend)

    async def compute():
        raise AssertionError("should not compute")

    result = await cache.get_or_set("shared-key", "fp-shared", compute)
    assert result == {"cached": True}


def test_create_idempotency_cache_memory_backend():
    cache = create_idempotency_cache({"cache": {"backend": "memory"}})
    assert isinstance(cache, SharedIdempotencyCache)
    assert cache._cache is None
