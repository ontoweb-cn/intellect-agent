"""FTS5 search, trigger management, and index utilities."""

from __future__ import annotations

import sqlite3
from typing import Optional

from state.schema import (
    _FTS_TABLES,
    _FTS_TRIGGERS,
    _ALLOWED_FTS_TRIGGERS,
    validate_fts_identifier,
)

# ── Opt-in Rust acceleration ────────────────────────────────────────────────
try:
    from intellect_core import (  # type: ignore[import-not-found]
        is_fts5_unavailable_error as _rust_is_fts5_unavailable_error,
        drop_fts_triggers_rs as _rust_drop_fts_triggers,
        fts_trigger_count_rs as _rust_fts_trigger_count,
        rebuild_fts_indexes_rs as _rust_rebuild_fts_indexes,
    )
    _HAS_RUST = True
except ImportError:
    _HAS_RUST = False


def is_fts5_unavailable_error(exc: sqlite3.OperationalError) -> bool:
    """Return True when the error indicates FTS5 module is missing."""
    if _HAS_RUST:
        return _rust_is_fts5_unavailable_error(exc)
    err = str(exc).lower()
    return "no such module" in err and "fts5" in err


def drop_fts_triggers(cursor: sqlite3.Cursor, *, db_path: str | None = None) -> None:
    """Drop all known FTS triggers (idempotent)."""
    if _HAS_RUST and db_path is not None:
        _rust_drop_fts_triggers(db_path)
        return
    for trigger in _FTS_TRIGGERS:
        try:
            validate_fts_identifier(trigger, _ALLOWED_FTS_TRIGGERS)
            cursor.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        except sqlite3.OperationalError:
            pass


def fts_trigger_count(cursor: sqlite3.Cursor, *, db_path: str | None = None) -> int:
    """Count how many of the expected FTS triggers exist."""
    if _HAS_RUST and db_path is not None:
        return _rust_fts_trigger_count(db_path)
    placeholders = ",".join("?" for _ in _FTS_TRIGGERS)
    row = cursor.execute(
        f"SELECT COUNT(*) FROM sqlite_master "
        f"WHERE type = 'trigger' AND name IN ({placeholders})",
        _FTS_TRIGGERS,
    ).fetchone()
    return int(row[0] if not isinstance(row, sqlite3.Row) else row[0])


def rebuild_fts_indexes(cursor: sqlite3.Cursor, *, db_path: str | None = None) -> None:
    """Delete and re-populate the messages_fts index from messages table."""
    if _HAS_RUST and db_path is not None:
        _rust_rebuild_fts_indexes(db_path)
        return
    validate_fts_identifier("messages_fts", _FTS_TABLES)
    cursor.execute("DELETE FROM messages_fts")
    cursor.execute(
        "INSERT INTO messages_fts(rowid, content) "
        "SELECT id, "
        "COALESCE(content, '') || ' ' || "
        "COALESCE(tool_name, '') || ' ' || "
        "COALESCE(tool_calls, '') "
        "FROM messages"
    )
