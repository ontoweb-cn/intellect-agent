"""Durable persistence for delegation completion notifications (A2-3③).

The in-process completion registry (rust DelegationRegistry + the module
``_completion_queue``) loses queued completions when the gateway restarts
mid-injection. This module is the durable backstop: a small WAL SQLite
database holding one row per undelivered synthesis text.

Integration contract:
- ``persist()`` when a gateway-bound completion is synthesized (before the
  watcher may drain it).
- ``drain_gateway_completions`` merges persisted rows for the same session
  key into the batch — a gateway crash BEFORE drain is transparently
  recovered because the row is still here after restart.
- ``put_back()`` on failed injection (attempts + 1; >= 8 attempts drops the
  row — a poison notification must not spin forever).
- ``delete()`` only after a successful injection.

Independent tiny DB (not state.db) so the queue's lifecycle and schema stay
decoupled from session storage. Everything is best-effort: failures here
degrade to the historical in-memory-only behavior, never raise into the
caller.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from intellect_constants import get_intellect_home

logger = logging.getLogger(__name__)

_DB_NAME = "delegation_queue.db"
_MAX_ATTEMPTS = 8
_ROW_TTL_S = 48 * 3600

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db_path() -> Path:
    return get_intellect_home() / _DB_NAME


def _get_conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is not None:
            return _conn
        path = _db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(path), check_same_thread=False, timeout=5.0, isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass  # WAL-incompatible FS: default journal still works
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS completions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_key TEXT NOT NULL,
                synth TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )
            """
        )
        _conn = conn
        return conn


def reset_for_tests() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
        _conn = None


def persist(session_key: str, synth: str) -> bool:
    """Store one synthesis for a session key. Best-effort (bool result)."""
    if not session_key or not synth:
        return False
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO completions (session_key, synth, attempts, created_at) "
            "VALUES (?, ?, 0, ?)",
            (session_key, synth, time.time()),
        )
        _prune_expired(conn)
        return True
    except Exception:
        logger.debug("persist completion failed", exc_info=True)
        return False


def pop_for_session(session_key: str, limit: int = 3) -> list[str]:
    """Atomically pop up to *limit* persisted synths for one session key."""
    if not session_key:
        return []
    out: list[str] = []
    try:
        conn = _get_conn()
        with _lock:
            cur = conn.execute(
                "SELECT id, synth FROM completions WHERE session_key = ? "
                "ORDER BY id LIMIT ?",
                (session_key, max(1, int(limit))),
            )
            rows = cur.fetchall()
            if rows:
                ids = [r["id"] for r in rows]
                conn.execute(
                    f"DELETE FROM completions WHERE id IN "
                    f"({','.join('?' * len(ids))})",
                    ids,
                )
                out = [r["synth"] for r in rows]
    except Exception:
        logger.debug("pop persisted completions failed", exc_info=True)
    return out


def delete_for_session(session_key: str) -> None:
    try:
        conn = _get_conn()
        with _lock:
            conn.execute(
                "DELETE FROM completions WHERE session_key = ?", (session_key,)
            )
    except Exception:
        logger.debug("delete persisted completions failed", exc_info=True)


def put_back(session_key: str, synth: str) -> bool:
    """Re-persist a failed injection; drops poison rows after _MAX_ATTEMPTS."""
    try:
        conn = _get_conn()
        with _lock:
            cur = conn.execute(
                "SELECT attempts FROM completions WHERE session_key = ? AND synth = ? "
                "ORDER BY id LIMIT 1",
                (session_key, synth),
            )
            row = cur.fetchone()
            if row is None:
                attempts = 0
            else:
                attempts = int(row["attempts"])
            # The re-inserted row REPLACES the old one — keeping both would
            # leave the attempts=0 original as the oldest row and the cap
            # would never advance.
            conn.execute(
                "DELETE FROM completions WHERE session_key = ? AND synth = ?",
                (session_key, synth),
            )
            if attempts + 1 >= _MAX_ATTEMPTS:
                logger.error(
                    "Dropping delegation completion for %s after %d failed "
                    "injection attempts",
                    session_key, attempts + 1,
                )
                return False
            conn.execute(
                "INSERT INTO completions (session_key, synth, attempts, created_at) "
                "VALUES (?, ?, ?, ?)",
                (session_key, synth, attempts + 1, time.time()),
            )
        return True
    except Exception:
        logger.debug("put_back completion failed", exc_info=True)
        return False


def _prune_expired(conn: sqlite3.Connection) -> None:
    cutoff = time.time() - _ROW_TTL_S
    conn.execute("DELETE FROM completions WHERE created_at < ?", (cutoff,))
