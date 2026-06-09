"""SQLite retire after PG migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.storage.migrate_sqlite_to_pg import retire_sqlite_db


def test_retire_sqlite_db_renames_active_file(tmp_path):
    db = tmp_path / "state.db"
    db.write_text("sqlite", encoding="utf-8")
    wal = Path(str(db) + "-wal")
    wal.write_bytes(b"wal")

    retired = retire_sqlite_db(db)

    assert retired is not None
    assert not db.exists()
    assert retired.exists()
    assert retired.read_text(encoding="utf-8") == "sqlite"
    assert Path(str(retired) + "-wal").exists()
    assert not wal.exists()


def test_retire_sqlite_db_noop_when_missing(tmp_path):
    assert retire_sqlite_db(tmp_path / "missing.db") is None
