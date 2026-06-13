"""Verify Rust (intellect_community_core) functions work correctly.

These tests confirm the Rust-backed functions behave as expected.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading


def _fresh_db() -> tuple[sqlite3.Connection, str]:
    """Return a temp-file SQLite database with FTS5 enabled, plus its path.

    Uses a temp file (not :memory:) so the Rust rusqlite connection
    to the same path shares the same database.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(content, tokenize='trigram')")
    conn.execute(
        "CREATE TABLE messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "content TEXT, tool_name TEXT, tool_calls TEXT"
        ")"
    )
    # Create the triggers like the real schema does
    conn.executescript("""
    CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(rowid, content) VALUES (
            new.id, COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
        );
    END;
    CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
        DELETE FROM messages_fts WHERE rowid = old.id;
    END;
    CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
        DELETE FROM messages_fts WHERE rowid = old.id;
        INSERT INTO messages_fts(rowid, content) VALUES (
            new.id, COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
        );
    END;
    """)
    return conn, path


def _fresh_sessions_db() -> tuple[sqlite3.Connection, str]:
    """Return a temp-file SQLite database with sessions table, plus its path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE sessions ("
        "id TEXT PRIMARY KEY, "
        "parent_session_id TEXT, "
        "started_at REAL, "
        "ended_at REAL, "
        "end_reason TEXT"
        ")"
    )
    return conn, path


# ── is_fts5_unavailable_error ────────────────────────────────────────────────


class TestIsFts5UnavailableError:
    """Tests for is_fts5_unavailable_error."""

    def test_detects_fts5_error(self):
        from state import fts

        exc = sqlite3.OperationalError("no such module: FTS5")
        assert fts.is_fts5_unavailable_error(exc) is True

    def test_ignores_other_error(self):
        from state import fts

        exc = sqlite3.OperationalError("table messages already exists")
        assert fts.is_fts5_unavailable_error(exc) is False

    def test_ignores_partial_match(self):
        from state import fts

        exc = sqlite3.OperationalError("no such module: json1")
        assert fts.is_fts5_unavailable_error(exc) is False


# ── drop_fts_triggers ────────────────────────────────────────────────────────


class TestDropFtsTriggers:
    """Tests for drop_fts_triggers."""

    def test_drop_all_triggers(self):
        from state import fts

        conn, db_path = _fresh_db()
        count_before = fts.fts_trigger_count(conn.cursor(), db_path=db_path)
        assert count_before == 3

        fts.drop_fts_triggers(conn.cursor(), db_path=db_path)
        count_after = fts.fts_trigger_count(conn.cursor(), db_path=db_path)
        assert count_after == 0

    def test_drop_triggers_idempotent(self):
        from state import fts

        conn, db_path = _fresh_db()
        cursor = conn.cursor()
        fts.drop_fts_triggers(cursor, db_path=db_path)
        fts.drop_fts_triggers(cursor, db_path=db_path)
        assert fts.fts_trigger_count(conn.cursor(), db_path=db_path) == 0


# ── fts_trigger_count ────────────────────────────────────────────────────────


class TestFtsTriggerCount:
    """Tests for fts_trigger_count."""

    def test_counts_triggers(self):
        from state import fts

        conn, db_path = _fresh_db()
        count = fts.fts_trigger_count(conn.cursor(), db_path=db_path)
        assert count == 3

    def test_zero_triggers(self):
        from state import fts

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        count = fts.fts_trigger_count(conn.cursor(), db_path=path)
        assert count == 0


# ── rebuild_fts_indexes ──────────────────────────────────────────────────────


class TestRebuildFtsIndexes:
    """Tests for rebuild_fts_indexes."""

    def test_rebuild_populates_fts(self):
        from state import fts

        conn, db_path = _fresh_db()
        conn.execute(
            "INSERT INTO messages (id, content, tool_name, tool_calls) "
            "VALUES (1, 'hello world', 'search', 'tool1')"
        )
        conn.execute(
            "INSERT INTO messages (id, content, tool_name, tool_calls) "
            "VALUES (2, 'foo bar', NULL, NULL)"
        )
        conn.commit()

        fts.rebuild_fts_indexes(conn.cursor(), db_path=db_path)
        conn.commit()

        rows = conn.execute(
            "SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'hello'"
        ).fetchall()
        assert len(rows) >= 1

    def test_rebuild_idempotent(self):
        from state import fts

        conn, db_path = _fresh_db()
        conn.execute(
            "INSERT INTO messages (id, content, tool_name, tool_calls) "
            "VALUES (1, 'test', NULL, NULL)"
        )
        conn.commit()

        fts.rebuild_fts_indexes(conn.cursor(), db_path=db_path)
        conn.commit()
        fts.rebuild_fts_indexes(conn.cursor(), db_path=db_path)
        conn.commit()

        rows = conn.execute("SELECT COUNT(*) as c FROM messages_fts").fetchone()
        assert rows["c"] == 1


# ── get_compression_tip ──────────────────────────────────────────────────────


class TestGetCompressionTip:
    """Tests for get_compression_tip."""

    def test_no_chain_returns_self(self):
        from state import compression

        conn, db_path = _fresh_sessions_db()
        lock = threading.Lock()

        result = compression.get_compression_tip(conn, lock, "session-1", db_path=db_path)
        assert result == "session-1"

    def test_chain_follows_parent(self):
        from state import compression

        conn, db_path = _fresh_sessions_db()
        lock = threading.Lock()
        now = 1000.0

        conn.execute(
            "INSERT INTO sessions (id, parent_session_id, started_at, ended_at, end_reason) "
            "VALUES (?, NULL, ?, ?, ?)",
            ("s1", now - 30, now - 20, "compression"),
        )
        conn.execute(
            "INSERT INTO sessions (id, parent_session_id, started_at, ended_at, end_reason) "
            "VALUES (?, ?, ?, ?, ?)",
            ("s2", "s1", now - 15, now - 10, "compression"),
        )
        conn.execute(
            "INSERT INTO sessions (id, parent_session_id, started_at, ended_at, end_reason) "
            "VALUES (?, ?, ?, NULL, NULL)",
            ("s3", "s2", now - 5),
        )
        conn.commit()

        result = compression.get_compression_tip(conn, lock, "s1", db_path=db_path)
        assert result == "s3"


# ── Rust availability ────────────────────────────────────────────────────────


class TestRustAvailability:
    """Verify Rust module is properly installed and configured."""

    def test_rust_is_importable(self):
        import intellect_community_core
        assert hasattr(intellect_community_core, "is_fts5_unavailable_error")
        assert hasattr(intellect_community_core, "drop_fts_triggers_rs")
        assert hasattr(intellect_community_core, "fts_trigger_count_rs")
        assert hasattr(intellect_community_core, "rebuild_fts_indexes_rs")
        assert hasattr(intellect_community_core, "get_compression_tip_rs")

    def test_centralized_imports_available(self):
        from intellect_rust import (
            rust_drop_fts_triggers,
            rust_fts_trigger_count,
            rust_get_compression_tip,
            rust_is_fts5_unavailable_error,
            rust_rebuild_fts_indexes,
        )
        assert rust_is_fts5_unavailable_error is not None
        assert rust_drop_fts_triggers is not None
        assert rust_fts_trigger_count is not None
        assert rust_rebuild_fts_indexes is not None
        assert rust_get_compression_tip is not None
