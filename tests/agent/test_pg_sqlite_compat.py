"""PostgreSQL adapter compatibility for kanban SQLite idioms."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent.storage.dialect import translate_sql
from agent.storage.pg_connection import PGConnectionAdapter, PGCursorAdapter


def test_insert_or_ignore_task_links_on_conflict():
    sql = "INSERT OR IGNORE INTO task_links (parent_id, child_id) VALUES (?, ?)"
    out = translate_sql(sql)
    assert "ON CONFLICT (parent_id, child_id) DO NOTHING" in out


def test_insert_returning_id_for_task_runs():
    sql = (
        "INSERT INTO task_runs (task_id, status, started_at) "
        "VALUES (?, ?, ?)"
    )
    from agent.storage.pg_sqlite_compat import maybe_add_returning_id

    out = maybe_add_returning_id(translate_sql(sql))
    assert "RETURNING id" in out


def test_pragma_table_info_translated():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (0, "board_id", "text", 1, None, 0)
    cursor.description = [
        ("cid",), ("name",), ("type",), ("notnull",), ("dflt_value",), ("pk",)
    ]

    cur = PGCursorAdapter(conn)
    cur.execute("PRAGMA table_info(tasks)")
    one = cur.fetchone()
    assert one is not None
    assert one.get("name") == "board_id"
    executed_sql = cursor.execute.call_args[0][0]
    assert "information_schema.columns" in executed_sql


def test_sqlite_master_table_probe():
    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = ("task_runs",)
    conn.cursor.return_value.description = [("name",)]

    cur = PGCursorAdapter(conn)
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='task_runs'"
    )
    one = cur.fetchone()
    assert one is not None
    executed = conn.cursor.return_value.execute.call_args[0][0]
    assert "information_schema.tables" in executed


def test_execute_closes_previous_cursor():
    conn = MagicMock()
    cursors: list[MagicMock] = []

    def make_cursor() -> MagicMock:
        cur = MagicMock()
        cursors.append(cur)
        return cur

    conn.cursor.side_effect = make_cursor

    adapter = PGCursorAdapter(conn)
    adapter.execute("SELECT 1")
    adapter.execute("SELECT 2")

    assert len(cursors) == 2
    cursors[0].close.assert_called_once()
    cursors[1].close.assert_not_called()


def test_connection_reuses_cursor_adapter():
    conn = MagicMock()
    adapter = PGConnectionAdapter(conn)
    assert adapter.cursor() is adapter.cursor()
    assert adapter.execute("SELECT 1") is adapter._cursor_adapter


def test_connection_close_closes_cursor():
    conn = MagicMock()
    cursors: list[MagicMock] = []

    def make_cursor() -> MagicMock:
        cur = MagicMock()
        cursors.append(cur)
        return cur

    conn.cursor.side_effect = make_cursor
    adapter = PGConnectionAdapter(conn)
    adapter.execute("SELECT 1")
    adapter.close()
    assert len(cursors) == 1
    cursors[0].close.assert_called_once()
    conn.close.assert_called_once()
