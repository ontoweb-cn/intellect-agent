"""P1 tests for storage/cache/events foundation (single-user)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.cache import MemoryCache, create_cache_backend, get_cache_backend_name
from agent.events import MemoryEventBus, create_event_bus, get_events_backend_name
from agent.storage import get_storage_manager, reset_storage_managers
from agent.storage.sqlite_backend import SQLiteBackend
from intellect_state import SessionDB


@pytest.fixture(autouse=True)
def _reset_managers():
    reset_storage_managers()
    yield
    reset_storage_managers()


def test_default_storage_backend_is_sqlite():
    backend = SQLiteBackend({})
    assert backend.dialect == "sqlite"


def test_memory_cache_lru_and_ttl():
    cache = MemoryCache(
        {
            "cache": {
                "memory": {
                    "agent_cache_size": 2,
                    "agent_cache_ttl_seconds": 1,
                }
            }
        }
    )

    async def _run():
        await cache.set("a", 1)
        await cache.set("b", 2)
        await cache.set("c", 3)
        assert await cache.get("a") is None
        assert await cache.get("b") == 2
        assert await cache.get("c") == 3

    asyncio.run(_run())


def test_memory_event_bus_delivers_to_subscriber():
    bus = MemoryEventBus()
    seen: list[dict] = []

    async def handler(msg: dict) -> None:
        seen.append(msg)

    async def _run():
        await bus.subscribe("webui.sessions", handler)
        await bus.publish("webui.sessions", {"ok": True})
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    assert seen == [{"ok": True}]


def test_storage_manager_wires_sqlite_db(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    db_path = home / "state.db"
    monkeypatch.setenv("INTELLECT_HOME", str(home))

    config = {
        "storage": {
            "sqlite": {"path": str(db_path)},
        }
    }
    manager = get_storage_manager(config)
    assert manager.db.dialect == "sqlite"
    assert Path(manager.db.db_path) == db_path


def test_sessiondb_delegates_writes_via_sqlite_backend(tmp_path):
    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    try:
        db.create_session(session_id="p1-test", source="cli", model="m")
        row = db.get_session("p1-test")
        assert row is not None
        assert row["id"] == "p1-test"
        assert db._storage_backend is not None
    finally:
        db.close()


def test_cache_and_events_factory_defaults():
    assert get_cache_backend_name({}) == "memory"
    assert get_events_backend_name({}) == "memory"
    assert isinstance(create_cache_backend({}), MemoryCache)
    assert isinstance(create_event_bus({}), MemoryEventBus)
