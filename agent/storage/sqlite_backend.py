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
                pass  # intentionally silent — cleanup/teardown path
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
                            pass  # intentionally silent — cleanup/teardown path
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
            logger.debug('non-critical operation failed', exc_info=True)

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


# ── Rust backend wrapper ────────────────────────────────────────────────────

from intellect_rust import SQLiteBackend as _RustSQLiteBackend


def create_backend(db_path, config=None):
    """Factory: return the Rust SQLite backend."""
    return RustSQLiteBackend(db_path, config=config)


class RustSQLiteBackend:
    """Thin Python wrapper around intellect_core.SQLiteBackend (Rust).

    Presents the same interface as SQLiteBackend so SessionDB can
    switch transparently.
    """

    def __init__(self, db_path, *, config=None):
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
        db_path = str(Path(raw_path).expanduser())
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path
        self._backend = _RustSQLiteBackend(db_path)

        # Open a Python sqlite3 connection for read compatibility.
        # SessionDB reads use conn.execute(), conn.cursor(), etc. which
        # RustConnection doesn't fully support yet.  The Rust backend is
        # used for execute_write + FTS/compression acceleration.
        self._python_conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            timeout=1.0,
            isolation_level=None,
        )
        self._python_conn.row_factory = sqlite3.Row
        # Apply WAL and FK pragmas on the Python connection too
        from intellect_state import apply_wal_with_fallback
        apply_wal_with_fallback(self._python_conn, db_label="state.db")
        self._python_conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.Lock()
        self._write_count = 0
        self._checkpoint_every = int(
            sqlite_cfg.get("checkpoint_every_n_writes", CHECKPOINT_EVERY_N_WRITES)
        )

    @property
    def connection(self):
        """Return the Python sqlite3.Connection for read operations."""
        return self._python_conn

    @property
    def dialect(self) -> str:
        return "sqlite"

    @property
    def is_connected(self) -> bool:
        return True

    def initialize(self) -> None:
        # Already initialized in __init__ (Rust backend opens on creation)
        pass

    def close(self) -> None:
        self._backend.close()
        try:
            self._python_conn.close()
        except Exception:
            pass  # intentionally silent — cleanup/teardown path

    def execute(self, sql, params=()):
        """Execute a read query via Python sqlite3 (backward compat)."""
        with self._lock:
            cursor = self._python_conn.cursor()
            cursor.execute(sql, params)
            return CursorProxy(cursor)

    def execute_write(self, fn):
        """Execute a write with BEGIN IMMEDIATE + retry.

        Uses Python's retry loop with the Python sqlite3 connection.
        The Rust backend's execute_write is available for callbacks that
        can use RustConnection, but existing SessionDB callbacks need
        the Python sqlite3 interface.
        """
        last_err = None
        for attempt in range(WRITE_MAX_RETRIES):
            try:
                with self._lock:
                    conn = self._python_conn
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        result = fn(conn)
                        conn.commit()
                    except BaseException:
                        try:
                            conn.rollback()
                        except Exception:
                            pass  # intentionally silent — cleanup/teardown path
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
        self._backend.try_wal_checkpoint()

    def ensure_schema(self, ddl: str) -> None:
        if ddl.strip():
            # Use Python connection for DDL so both connections see the schema
            self._python_conn.executescript(ddl)

    def supports_fts(self) -> bool:
        return True

    def search(self, query: str, limit: int = 50) -> list[dict]:
        raise NotImplementedError(
            "search is not wired; use SessionDB.search_messages"
        )
