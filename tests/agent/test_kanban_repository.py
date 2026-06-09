"""KanbanRepository smoke tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent.storage.kanban_repository import KanbanRepository


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


def test_repository_create_and_list(kanban_home):
    from intellect_cli import kanban_db as kb

    kb.init_db()
    repo = KanbanRepository(board="default")
    with repo.connection() as conn:
        task_id = repo.create_task(conn, title="repo smoke", triage=True)
        tasks = repo.list_tasks(conn, board="default")
    assert any(t.id == task_id for t in tasks)
