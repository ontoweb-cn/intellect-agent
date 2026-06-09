"""SQLite → PG migration dry-run tests (no PostgreSQL required)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from intellect_state import SessionDB


@pytest.fixture()
def sqlite_state_db(tmp_path):
    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    db.create_session(session_id="mig-1", source="cli", model="test")
    db.close()
    return path


def test_migrate_dry_run_reports_counts(sqlite_state_db, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(sqlite_state_db.parent))
    from agent.storage.migrate_sqlite_to_pg import migrate_sqlite_to_postgresql

    report = migrate_sqlite_to_postgresql(
        sqlite_path=sqlite_state_db,
        dsn="postgresql://user:pass@localhost:5432/testdb",
        dry_run=True,
        backup=False,
    )
    assert report.dry_run is True
    names = {t.name for t in report.tables}
    assert "sessions" in names
    session_row = next(t for t in report.tables if t.name == "sessions")
    assert session_row.source_rows >= 1
    assert session_row.copied_rows == session_row.source_rows


def test_discover_tables_includes_schema_tables(sqlite_state_db):
    from agent.storage.migrate_tables import discover_sqlite_tables

    conn = sqlite3.connect(sqlite_state_db)
    try:
        tables = discover_sqlite_tables(conn)
    finally:
        conn.close()
    assert "sessions" in tables
    assert "oauth_providers" in tables
