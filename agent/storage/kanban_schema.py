"""Kanban schema helpers for unified storage (T1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Canonical schema lives in kanban_db; import at runtime to avoid import cycles at module load.
KANBAN_TABLES: tuple[str, ...] = (
    "tasks",
    "task_links",
    "task_comments",
    "task_events",
    "task_runs",
    "task_attachments",
    "kanban_notify_subs",
)


def kanban_schema_sql() -> str:
    from intellect_cli.kanban_db import SCHEMA_SQL

    return SCHEMA_SQL


def ensure_kanban_schema(conn: sqlite3.Connection) -> None:
    """Create kanban tables on *conn* if missing (idempotent)."""
    conn.row_factory = sqlite3.Row
    conn.executescript(kanban_schema_sql())
    from intellect_cli.kanban_db import _migrate_add_optional_columns

    _migrate_add_optional_columns(conn)


def default_kanban_db_path(home: Path) -> Path:
    return home / "kanban.db"


def discover_legacy_kanban_sources(home: Path) -> list[tuple[str, Path]]:
    """Return ``(board_slug, kanban.db path)`` for legacy on-disk boards."""
    sources: list[tuple[str, Path]] = []
    default_db = default_kanban_db_path(home)
    if default_db.is_file() and default_db.stat().st_size > 0:
        sources.append(("default", default_db))

    boards_root = home / "kanban" / "boards"
    if boards_root.is_dir():
        for board_dir in sorted(boards_root.iterdir()):
            if not board_dir.is_dir():
                continue
            slug = board_dir.name
            if slug == "default":
                continue
            db_path = board_dir / "kanban.db"
            if db_path.is_file() and db_path.stat().st_size > 0:
                sources.append((slug, db_path))
    return sources
