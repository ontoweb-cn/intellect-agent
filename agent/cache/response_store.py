"""Responses API state store — CacheBackend (Redis) with SQLite fallback (P4a / T12)."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PREFIX_RESP = "intellect:responses:"
_PREFIX_CONV = "intellect:conversations:"
_LRU_KEY = "intellect:responses:lru"


class SqliteResponseStore:
    """SQLite-backed LRU store (legacy default when cache.backend=memory)."""

    def __init__(self, max_size: int = 1000, db_path: str | None = None):
        if db_path is None:
            try:
                from intellect_cli.config import get_intellect_home

                db_path = str(get_intellect_home() / "response_store.db")
            except Exception:
                db_path = ":memory:"
        self._max_size = max_size
        self._db_path: str | None = db_path if db_path != ":memory:" else None
        try:
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
        except Exception:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._db_path = None
        from intellect_state import apply_wal_with_fallback

        apply_wal_with_fallback(self._conn, db_label="response_store.db")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS responses (
                response_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                accessed_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS conversations (
                name TEXT PRIMARY KEY,
                response_id TEXT NOT NULL
            )"""
        )
        self._conn.commit()
        self._tighten_file_permissions()

    def _tighten_file_permissions(self) -> None:
        if not self._db_path:
            return
        for candidate in (
            Path(self._db_path),
            Path(f"{self._db_path}-wal"),
            Path(f"{self._db_path}-shm"),
        ):
            try:
                if candidate.exists():
                    candidate.chmod(0o600)
            except OSError:
                logger.debug("response store chmod failed for %s", candidate, exc_info=True)

    def get(self, response_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT data FROM responses WHERE response_id = ?", (response_id,)
        ).fetchone()
        if row is None:
            return None
        self._conn.execute(
            "UPDATE responses SET accessed_at = ? WHERE response_id = ?",
            (time.time(), response_id),
        )
        self._conn.commit()
        return json.loads(row[0])

    def put(self, response_id: str, data: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO responses (response_id, data, accessed_at) VALUES (?, ?, ?)",
            (response_id, json.dumps(data, default=str), time.time()),
        )
        count = self._conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
        if count > self._max_size:
            evict_ids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT response_id FROM responses ORDER BY accessed_at ASC LIMIT ?",
                    (count - self._max_size,),
                ).fetchall()
            ]
            if evict_ids:
                placeholders = ",".join("?" for _ in evict_ids)
                self._conn.execute(
                    f"DELETE FROM conversations WHERE response_id IN ({placeholders})",
                    evict_ids,
                )
                self._conn.execute(
                    f"DELETE FROM responses WHERE response_id IN ({placeholders})",
                    evict_ids,
                )
        self._conn.commit()

    def delete(self, response_id: str) -> bool:
        self._conn.execute(
            "DELETE FROM conversations WHERE response_id = ?", (response_id,)
        )
        cursor = self._conn.execute(
            "DELETE FROM responses WHERE response_id = ?", (response_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def get_conversation(self, name: str) -> str | None:
        row = self._conn.execute(
            "SELECT response_id FROM conversations WHERE name = ?", (name,)
        ).fetchone()
        return row[0] if row else None

    def set_conversation(self, name: str, response_id: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO conversations (name, response_id) VALUES (?, ?)",
            (name, response_id),
        )
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass  # intentionally silent — cleanup/teardown path

    def __len__(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM responses").fetchone()
        return row[0] if row else 0


class RedisResponseStore:
    """Shared response store via CacheBackend (Redis)."""

    def __init__(self, cache, *, max_size: int = 1000, ttl: int = 86400):
        self._cache = cache
        self._max_size = max_size
        self._ttl = ttl

    def _resp_key(self, response_id: str) -> str:
        return f"{_PREFIX_RESP}{response_id}"

    def _conv_key(self, name: str) -> str:
        return f"{_PREFIX_CONV}{name}"

    def _touch_lru(self, response_id: str) -> None:
        client = getattr(self._cache, "_client", None)
        if client is None:
            return
        try:
            now = time.time()
            client.zadd(_LRU_KEY, {response_id: now})
            count = client.zcard(_LRU_KEY)
            if count > self._max_size:
                overflow = count - self._max_size
                stale = client.zrange(_LRU_KEY, 0, overflow - 1)
                for rid in stale:
                    self.delete(str(rid))
        except Exception:
            logger.debug("response store LRU trim failed", exc_info=True)

    def get(self, response_id: str) -> dict[str, Any] | None:
        raw = self._cache.get_sync(self._resp_key(response_id))
        if raw is None:
            return None
        self._touch_lru(response_id)
        return raw if isinstance(raw, dict) else None

    def put(self, response_id: str, data: dict[str, Any]) -> None:
        self._cache.set_sync(self._resp_key(response_id), data, ttl=self._ttl)
        self._touch_lru(response_id)

    def delete(self, response_id: str) -> bool:
        existed = self._cache.get_sync(self._resp_key(response_id)) is not None
        self._cache.delete_sync(self._resp_key(response_id))
        client = getattr(self._cache, "_client", None)
        if client is not None:
            try:
                client.zrem(_LRU_KEY, response_id)
            except Exception:
                logger.debug('non-critical operation failed', exc_info=True)
        return existed

    def get_conversation(self, name: str) -> str | None:
        val = self._cache.get_sync(self._conv_key(name))
        return str(val) if val else None

    def set_conversation(self, name: str, response_id: str) -> None:
        self._cache.set_sync(self._conv_key(name), response_id, ttl=self._ttl)

    def close(self) -> None:
        if hasattr(self._cache, "close"):
            self._cache.close()

    def __len__(self) -> int:
        client = getattr(self._cache, "_client", None)
        if client is None:
            return 0
        try:
            return int(client.zcard(_LRU_KEY))
        except Exception:
            return 0


def create_response_store(
    config: dict | None = None,
    *,
    max_size: int = 1000,
    db_path: str | None = None,
):
    """Return Redis-backed store when ``cache.backend=redis``, else SQLite file."""
    from agent.cache.factory import create_cache_backend, get_cache_backend_name

    cfg = config or {}
    if get_cache_backend_name(cfg) == "redis":
        try:
            cache = create_cache_backend(cfg)
            if type(cache).__name__ == "MemoryCache":
                logger.debug("Redis cache unavailable; using SQLite response store")
            else:
                return RedisResponseStore(cache, max_size=max_size)
        except Exception:
            logger.debug("Redis response store init failed; using SQLite", exc_info=True)
    return SqliteResponseStore(max_size=max_size, db_path=db_path)


# Back-compat alias used by gateway tests
ResponseStore = SqliteResponseStore
