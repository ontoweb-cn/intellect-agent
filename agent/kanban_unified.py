"""Unified kanban storage routing (T1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from intellect_constants import get_intellect_home


def kanban_storage_mode(config: dict | None = None) -> str:
    """``legacy`` (separate kanban.db) or ``unified`` (state.db)."""
    if config is None:
        try:
            from intellect_cli.config import load_config

            config = load_config()
        except Exception:
            return "legacy"
    mode = str((config.get("kanban") or {}).get("storage") or "legacy").strip().lower()
    return mode if mode in {"legacy", "unified"} else "legacy"


def _resolve_sqlite_state_path(config: dict) -> Path:
    from agent.storage.migrate_kanban import _resolve_sqlite_state_path as _path

    return _path(config, get_intellect_home())


def unified_db_path(*, board: Optional[str] = None, config: dict | None = None) -> Path:
    """SQLite unified path (``state.db``)."""
    if config is None:
        from intellect_cli.config import load_config

        config = load_config()
    return _resolve_sqlite_state_path(config)


def _ensure_unified_schema(conn) -> None:
    """Idempotent kanban DDL + additive migrations on unified storage."""
    from intellect_cli.kanban_db import SCHEMA_SQL, _migrate_add_optional_columns

    for statement in _strip_comments(SCHEMA_SQL).split(";"):
        stmt = statement.strip()
        if stmt:
            conn.execute(stmt)
    _migrate_add_optional_columns(conn)


def _strip_comments(sql: str) -> str:
    """Remove SQL line comments (``--``)."""
    lines = []
    for line in sql.splitlines():
        if "--" in line:
            line = line.split("--", 1)[0]
        lines.append(line)
    return "\n".join(lines)


def connect_unified(*, board: Optional[str] = None) -> sqlite3.Connection:
    """Open kanban tables on the unified storage backend."""
    from intellect_cli.config import load_config
    from intellect_cli.kanban_db import DEFAULT_BOARD, _normalize_board_slug

    config = load_config()
    slug = _normalize_board_slug(board) or DEFAULT_BOARD

    from intellect_cli import kanban_db as kb

    conn = kb.connect(db_path=unified_db_path(board=slug, config=config))

    conn._kanban_board_id = slug  # type: ignore[attr-defined]
    return conn
