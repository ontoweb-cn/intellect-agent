"""Tests for gateway/turn_lease.py — per-session turn serialization."""

import asyncio

import pytest

from gateway.turn_lease import (
    SessionTurnLeaseRegistry,
    TurnLeaseTimeoutError,
)


def _run(coro):
    return asyncio.run(coro)


def test_acquire_and_release_idempotent():
    reg = SessionTurnLeaseRegistry()

    async def main():
        token = await reg.acquire("sid-1", owner_key="key-a", generation=1)
        assert token is not None
        assert reg.release(token) is True
        # release is idempotent — a second release is a safe no-op
        assert reg.release(token) is False

    _run(main())


def test_falsy_session_id_returns_none():
    reg = SessionTurnLeaseRegistry()

    async def main():
        assert await reg.acquire("", owner_key="k", generation=1) is None

    _run(main())


def test_second_turn_waits_for_first_flush():
    reg = SessionTurnLeaseRegistry()

    async def main():
        t1 = await reg.acquire("sid-1", owner_key="key-a", generation=1)
        assert t1 is not None

        acquired = []

        async def second_turn():
            t2 = await reg.acquire(
                "sid-1", owner_key="key-b", generation=2, timeout=5
            )
            acquired.append(t2)

        task = asyncio.create_task(second_turn())
        await asyncio.sleep(0.05)
        assert acquired == []  # still serialized behind the held lease

        assert reg.release(t1) is True
        await asyncio.wait_for(task, timeout=5)
        assert acquired and acquired[0] is not None
        reg.release(acquired[0])

    _run(main())


def test_timeout_fails_closed():
    reg = SessionTurnLeaseRegistry()

    async def main():
        t1 = await reg.acquire("sid-1", owner_key="key-a", generation=1)
        with pytest.raises(TurnLeaseTimeoutError):
            await reg.acquire(
                "sid-1", owner_key="key-b", generation=2, timeout=0.05
            )
        reg.release(t1)

    _run(main())


def test_rebind_aliases_held_lease():
    reg = SessionTurnLeaseRegistry()

    async def main():
        t1 = await reg.acquire("sid-old", owner_key="key-a", generation=1)
        assert reg.rebind(t1, "sid-new") is True
        # release on the token (now bound to sid-new) frees the shared lease
        assert reg.release(t1) is True
        assert reg._leases["sid-new"].idle

    _run(main())


def test_rebind_refuses_live_target():
    reg = SessionTurnLeaseRegistry()

    async def main():
        t1 = await reg.acquire("sid-old", owner_key="key-a", generation=1)
        t2 = await reg.acquire("sid-new", owner_key="key-b", generation=1)
        # target already has a live lease → rebind is refused, token stays put
        assert reg.rebind(t1, "sid-new") is False
        assert t1.session_id == "sid-old"
        reg.release(t1)
        reg.release(t2)

    _run(main())


def test_bounded_registry_never_evicts_live_lease():
    reg = SessionTurnLeaseRegistry(max_entries=2)

    async def main():
        t1 = await reg.acquire("s1", owner_key="k", generation=1)
        t2 = await reg.acquire("s2", owner_key="k", generation=1)
        assert len(reg) == 2
        # both live → a third acquire may transiently exceed the cap rather
        # than break serialization
        t3 = await reg.acquire("s3", owner_key="k", generation=1)
        assert len(reg) == 3
        for t in (t1, t2, t3):
            reg.release(t)

    _run(main())


def test_idle_eviction_when_over_cap():
    reg = SessionTurnLeaseRegistry(max_entries=2)

    async def main():
        t1 = await reg.acquire("s1", owner_key="k", generation=1)
        reg.release(t1)  # idle
        t2 = await reg.acquire("s2", owner_key="k", generation=1)
        reg.release(t2)  # idle
        assert len(reg) == 2

        t3 = await reg.acquire("s3", owner_key="k", generation=1)
        # oldest idle entry was evicted to make room
        assert len(reg) == 2
        reg.release(t3)

    _run(main())
