"""SQLite storage backend — connection lifecycle and write retry."""

from __future__ import annotations

import logging
import random
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

from agent.storage.cursor import CursorProxy
from intellect_constants import get_intellect_home

logger = logging.getLogger(__name__)

T = TypeVar("T")

WRITE_MAX_RETRIES = 15
WRITE_RETRY_MIN_S = 0.020
WRITE_RETRY_MAX_S = 0.150
CHECKPOINT_EVERY_N_WRITES = 50


class SQLiteBackend:
    """SQLite storage backend using stdlib sqlite3 (default install path)."""

    def __init__(self, config: dict | None = None, *, db_path: Path | str | None = None) -> None:
        cfg = config or {}
        storage_cfg = cfg.get("storage") if isinstance(cfg.get("storage"), dict) else {}
        sqlite_cfg = (
            storage_cfg.get("sqlite")
            if isinstance(storage_cfg.get("sqlite"), dict)
            else {}
        )
        raw_path = db_path or sqlite_cfg.get("path") or (get_intellect_home() / "state.db")
        if isinstance(raw_path, str) and not raw_path.strip():
            raw_path = get_intellect_home() / "state.db"
        self.db_path = Path(str(raw_path)).expanduser()
        self._wal = bool(sqlite_cfg.get("wal", True))
        self._checkpoint_every = int(
            sqlite_cfg.get("checkpoint_every_n_writes", CHECKPOINT_EVERY_N_WRITES)
        )
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._write_count = 0
        self._fts_enabled = False

    @property
    def connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SQLiteBackend is not initialized")
        return self._conn

    @property
    def dialect(self) -> str:
        return "sqlite"

    @property
    def is_connected(self) -> bool:
        return self._conn is not None

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=1.0,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            if self._wal:
                from intellect_state import apply_wal_with_fallback

                apply_wal_with_fallback(conn, db_label="state.db")
            conn.execute("PRAGMA foreign_keys=ON")
            self._conn = conn
        except Exception:
            self._conn = None
            raise

    def close(self) -> None:
        with self._lock:
            if not self._conn:
                return
            try:
                self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except Exception:
                pass
            try:
                self._conn.close()
            finally:
                self._conn = None

    def execute(self, sql: str, params: tuple = ()) -> CursorProxy:
        with self._lock:
            cursor = self.connection.cursor()
            cursor.execute(sql, params)
            return CursorProxy(cursor)

    def execute_write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        last_err: Exception | None = None
        for attempt in range(WRITE_MAX_RETRIES):
            try:
                with self._lock:
                    conn = self.connection
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        result = fn(conn)
                        conn.commit()
                    except BaseException:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        raise
                self._write_count += 1
                if self._write_count % self._checkpoint_every == 0:
                    self.try_wal_checkpoint()
                return result
            except sqlite3.OperationalError as exc:
                err_msg = str(exc).lower()
                if "locked" in err_msg or "busy" in err_msg:
                    last_err = exc
                    if attempt < WRITE_MAX_RETRIES - 1:
                        time.sleep(random.uniform(WRITE_RETRY_MIN_S, WRITE_RETRY_MAX_S))
                        continue
                raise
        raise last_err or sqlite3.OperationalError("database is locked after max retries")

    def try_wal_checkpoint(self) -> None:
        try:
            with self._lock:
                if not self._conn:
                    return
                result = self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
                if result and result[1] > 0:
                    logger.debug(
                        "WAL checkpoint: %d/%d pages checkpointed",
                        result[2],
                        result[1],
                    )
        except Exception:
            pass

    def ensure_schema(self, ddl: str) -> None:
        if not ddl.strip():
            return
        with self._lock:
            self.connection.executescript(ddl)

    def supports_fts(self) -> bool:
        return self._fts_enabled

    def search(self, query: str, limit: int = 50) -> list[dict]:
        raise NotImplementedError(
            "SQLiteBackend.search is not wired; use SessionDB.search_messages"
        )
