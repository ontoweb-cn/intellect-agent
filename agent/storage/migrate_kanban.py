"""Migrate legacy ``kanban.db`` files into unified storage (T1)."""

from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.storage.kanban_schema import (
    KANBAN_TABLES,
    discover_legacy_kanban_sources,
    ensure_kanban_schema,
)
from intellect_constants import get_intellect_home

logger = logging.getLogger(__name__)


@dataclass
class KanbanTableReport:
    name: str
    board: str
    source_rows: int = 0
    copied_rows: int = 0
    skipped: bool = False
    error: str | None = None


@dataclass
class KanbanMigrationReport:
    sources: list[tuple[str, Path]] = field(default_factory=list)
    target: str = ""
    dry_run: bool = False
    tables: list[KanbanTableReport] = field(default_factory=list)
    backup_paths: list[Path] = field(default_factory=list)
    config_updated: bool = False
    warnings: list[str] = field(default_factory=list)


def _resolve_sqlite_state_path(config: dict, home: Path) -> Path:
    storage = config.get("storage") if isinstance(config.get("storage"), dict) else {}
    sqlite = storage.get("sqlite") if isinstance(storage.get("sqlite"), dict) else {}
    custom = str(sqlite.get("path") or "").strip()
    if custom:
        return Path(custom).expanduser().resolve()
    return (home / "state.db").resolve()


def _table_count(conn: Any, table: str) -> int:
    try:
        row = conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()
        if row is None:
            return 0
        return int(row[0] if not hasattr(row, "keys") else row["n"])
    except Exception:
        return 0


def _inject_board_id(
    columns: list[str], rows: list, board: str
) -> tuple[list[str], list[tuple]]:
    """Tag ``tasks`` rows with ``board_id`` when copying into unified storage."""
    if "board_id" in columns:
        return columns, [tuple(r) for r in rows]
    out_cols = columns + ["board_id"]
    out_rows = [tuple(r) + (board,) for r in rows]
    return out_cols, out_rows


def _copy_kanban_table(
    src: sqlite3.Connection,
    dest: sqlite3.Connection,
    table: str,
    *,
    board: str,
    dry_run: bool,
    replace: bool,
) -> KanbanTableReport:
    report = KanbanTableReport(name=table, board=board)
    report.source_rows = _table_count(src, table)
    if report.source_rows == 0:
        report.skipped = True
        return report

    if dry_run:
        report.copied_rows = report.source_rows
        return report

    cols_info = src.execute(f'PRAGMA table_info("{table}")').fetchall()
    columns = [row[1] for row in cols_info]
    if not columns:
        report.skipped = True
        return report

    if replace:
        if table == "tasks":
            dest.execute('DELETE FROM tasks WHERE board_id = ?', (board,))
        else:
            dest.execute(f'DELETE FROM "{table}"')

    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(f'"{c}"' for c in columns)
    rows = src.execute(f'SELECT {col_list} FROM "{table}"').fetchall()
    if table == "tasks":
        columns, rows = _inject_board_id(columns, rows, board)
        placeholders = ", ".join("?" for _ in columns)
        col_list = ", ".join(f'"{c}"' for c in columns)
    insert_sql = f'INSERT OR IGNORE INTO "{table}" ({col_list}) VALUES ({placeholders})'
    for row in rows:
        dest.execute(insert_sql, tuple(row))
    report.copied_rows = len(rows)
    return report


def _backup_legacy_db(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.pre-kanban-unify.{stamp}")
    shutil.copy2(path, backup)
    return backup


def migrate_kanban_to_unified_storage(
    *,
    config: dict | None = None,
    intellect_home: Path | None = None,
    dry_run: bool = False,
    backup: bool = True,
    update_config: bool = False,
    allow_multi_board: bool = False,
) -> KanbanMigrationReport:
    """Copy legacy ``kanban.db`` into ``state.db`` or PostgreSQL (default board only)."""
    if config is None:
        from intellect_cli.config import load_config

        config = load_config()

    home = Path(intellect_home or get_intellect_home()).expanduser().resolve()
    report = KanbanMigrationReport(dry_run=dry_run)
    sources = discover_legacy_kanban_sources(home)
    report.sources = sources

    if not sources:
        report.warnings.append("no legacy kanban.db files found — nothing to migrate")
        return report

    extra_boards = [slug for slug, _ in sources if slug != "default"]
    if extra_boards and not allow_multi_board:
        report.warnings.append(
            "multi-board sources detected "
            f"({', '.join(extra_boards)}); migrating all boards into unified "
            "storage with board_id tags (use --allow-multi-board to silence)"
        )

    target_path = _resolve_sqlite_state_path(config, home)
    report.target = str(target_path)
    for board, src_path in sources:
        if backup and not dry_run:
            report.backup_paths.append(_backup_legacy_db(src_path))
        with sqlite3.connect(str(src_path)) as src_conn:
            if dry_run:
                for table in KANBAN_TABLES:
                    count = _table_count(src_conn, table)
                    report.tables.append(
                        KanbanTableReport(
                            name=table,
                            board=board,
                            source_rows=count,
                            copied_rows=count,
                        )
                    )
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(target_path)) as dest_conn:
                ensure_kanban_schema(dest_conn)
                for table in KANBAN_TABLES:
                    report.tables.append(
                        _copy_kanban_table(
                            src_conn,
                            dest_conn,
                            table,
                            board=board,
                            dry_run=False,
                            replace=False,
                        )
                    )
                dest_conn.commit()

    if update_config and not dry_run:
        kanban = dict(config.get("kanban") or {})
        kanban["storage"] = "unified"
        config["kanban"] = kanban
        from intellect_cli.config import save_config

        save_config(config)
        report.config_updated = True

    return report
