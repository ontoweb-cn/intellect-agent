"""FTS5 search, trigger management, and index utilities."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from intellect_rust import (
    rust_drop_fts_triggers,
    rust_fts_trigger_count,
    rust_is_fts5_unavailable_error,
    rust_rebuild_fts_indexes,
)


def is_fts5_unavailable_error(exc: sqlite3.OperationalError) -> bool:
    """Return True when the error indicates FTS5 module is missing."""
    return rust_is_fts5_unavailable_error(exc)


def drop_fts_triggers(
    cursor: sqlite3.Cursor, *, db_path: str | None = None, backend: Any = None
) -> None:
    """Drop all known FTS triggers (idempotent)."""
    if backend is not None:
        backend.drop_fts_triggers()
        return
    if db_path is not None:
        rust_drop_fts_triggers(db_path)
        return
    raise ValueError("Either backend or db_path is required")


def fts_trigger_count(
    cursor: sqlite3.Cursor, *, db_path: str | None = None, backend: Any = None
) -> int:
    """Count how many of the expected FTS triggers exist."""
    if backend is not None:
        return backend.fts_trigger_count()
    if db_path is not None:
        return rust_fts_trigger_count(db_path)
    raise ValueError("Either backend or db_path is required")


def rebuild_fts_indexes(
    cursor: sqlite3.Cursor, *, db_path: str | None = None, backend: Any = None
) -> None:
    """Delete and re-populate the messages_fts index from messages table."""
    if backend is not None:
        backend.rebuild_fts_indexes()
        return
    if db_path is not None:
        rust_rebuild_fts_indexes(db_path)
        return
    raise ValueError("Either backend or db_path is required")
