"""T1 migrate-kanban tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent.storage.kanban_schema import KANBAN_TABLES, ensure_kanban_schema
from agent.storage.migrate_kanban import migrate_kanban_to_unified_storage


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    (home / "config.yaml").write_text(
        "storage:\n  backend: sqlite\nkanban:\n  storage: legacy\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    monkeypatch.setenv("INTELLECT_CONFIG_PATH", str(home / "config.yaml"))
    return home


def _seed_kanban_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        ensure_kanban_schema(conn)
        conn.execute(
            "INSERT INTO tasks (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("t_abc12345", "Test task", "todo", 1),
        )
        conn.commit()


def test_migrate_kanban_sqlite_into_state_db(kanban_home):
    src = kanban_home / "kanban.db"
    _seed_kanban_db(src)
    config = {
        "storage": {"backend": "sqlite", "sqlite": {"path": ""}},
        "kanban": {"storage": "legacy"},
    }
    report = migrate_kanban_to_unified_storage(
        config=config,
        intellect_home=kanban_home,
        dry_run=False,
        backup=False,
        update_config=True,
    )
    assert report.config_updated is True
    state_db = kanban_home / "state.db"
    assert state_db.is_file()
    with sqlite3.connect(state_db) as conn:
        row = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
        assert row[0] == 1
    assert any(t.name == "tasks" and t.copied_rows == 1 for t in report.tables)


def test_migrate_kanban_dry_run(kanban_home):
    _seed_kanban_db(kanban_home / "kanban.db")
    config = {"storage": {"backend": "sqlite"}, "kanban": {"storage": "legacy"}}
    report = migrate_kanban_to_unified_storage(
        config=config,
        intellect_home=kanban_home,
        dry_run=True,
    )
    assert not (kanban_home / "state.db").exists()
    assert report.tables[0].copied_rows == 1
