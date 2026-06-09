"""Process-scoped StorageManager facade."""

from __future__ import annotations

import threading
from typing import Any

from agent.storage.sqlite_backend import SQLiteBackend

_managers_lock = threading.Lock()
_managers: dict[str, StorageManager] = {}


class StorageManager:
    """Facade grouping storage, cache, and event bus backends."""

    def __init__(
        self,
        config: dict,
        *,
        db: SQLiteBackend | None = None,
        cache: Any | None = None,
        events: Any | None = None,
    ) -> None:
        self._config = config
        self.db = db or SQLiteBackend(config)
        if cache is None:
            from agent.cache import create_cache_backend

            cache = create_cache_backend(config)
        if events is None:
            from agent.events import create_event_bus

            events = create_event_bus(config)
        self.cache = cache
        self.events = events
        self._initialized = False

    def initialize(self) -> None:
        if not self._initialized:
            self.db.initialize()
            self._initialized = True

    def close(self) -> None:
        try:
            self.db.close()
        finally:
            self._initialized = False
            if hasattr(self.cache, "close"):
                self.cache.close()
            if hasattr(self.events, "close"):
                self.events.close()


def _manager_key(config: dict) -> str:
    storage = config.get("storage") if isinstance(config.get("storage"), dict) else {}
    sqlite = storage.get("sqlite") if isinstance(storage.get("sqlite"), dict) else {}
    path = str(sqlite.get("path") or "")
    from intellect_constants import get_intellect_home

    return f"{get_intellect_home()}|{path}"


def get_storage_manager(config: dict | None = None) -> StorageManager:
    """Return a cached StorageManager for *config* (default: load_cli config)."""
    if config is None:
        from intellect_cli.config import load_config

        config = load_config()
    key = _manager_key(config)
    with _managers_lock:
        manager = _managers.get(key)
        if manager is None:
            manager = StorageManager(config)
            manager.initialize()
            _managers[key] = manager
        return manager


def reset_storage_managers() -> None:
    """Close and clear cached managers (tests)."""
    with _managers_lock:
        for manager in _managers.values():
            try:
                manager.close()
            except Exception:
                pass
        _managers.clear()
