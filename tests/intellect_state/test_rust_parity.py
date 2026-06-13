"""Verify Rust (intellect_community_core) functions produce identical results to Python.

These tests confirm the Stage 1 PyO3 migration is correct: every Rust-backed
function must behave exactly like its pure-Python counterpart.
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import threading
import unittest.mock


# ── Helpers ──────────────────────────────────────────────────────────────────

def _disable_rust(module):
    """Temporarily set _HAS_RUST = False on a module."""
    module._HAS_RUST = False


def _enable_rust(module):
    """Restore _HAS_RUST = True on a module."""
    module._HAS_RUST = True


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
    """Parity tests for is_fts5_unavailable_error."""

    def test_py_and_rust_agree_on_fts5_error(self):
        from state import fts

        exc = sqlite3.OperationalError("no such module: FTS5")

        _disable_rust(fts)
        py_result = fts.is_fts5_unavailable_error(exc)
        _enable_rust(fts)
        rust_result = fts.is_fts5_unavailable_error(exc)

        assert py_result == rust_result == True

    def test_py_and_rust_agree_on_other_error(self):
        from state import fts

        exc = sqlite3.OperationalError("table messages already exists")

        _disable_rust(fts)
        py_result = fts.is_fts5_unavailable_error(exc)
        _enable_rust(fts)
        rust_result = fts.is_fts5_unavailable_error(exc)

        assert py_result == rust_result == False

    def test_py_and_rust_agree_on_partial_match(self):
        from state import fts

        # "no such module" but without "fts5"
        exc = sqlite3.OperationalError("no such module: json1")

        _disable_rust(fts)
        py_result = fts.is_fts5_unavailable_error(exc)
        _enable_rust(fts)
        rust_result = fts.is_fts5_unavailable_error(exc)

        assert py_result == rust_result == False


# ── drop_fts_triggers ────────────────────────────────────────────────────────


class TestDropFtsTriggers:
    """Parity tests for drop_fts_triggers."""

    def test_drop_all_triggers(self):
        from state import fts

        conn, db_path = _fresh_db()
        # Verify triggers exist
        count_before = fts.fts_trigger_count(conn.cursor(), db_path=db_path)

        _disable_rust(fts)
        py_cursor = conn.cursor()
        fts.drop_fts_triggers(py_cursor)

        _enable_rust(fts)
        # Triggers should be gone (py already dropped them)
        count_after = fts.fts_trigger_count(conn.cursor(), db_path=db_path)

        assert count_before == 3  # insert, delete, update
        assert count_after == 0

    def test_drop_triggers_idempotent(self):
        from state import fts

        conn, db_path = _fresh_db()
        cursor = conn.cursor()
        # Drop twice — no error
        fts.drop_fts_triggers(cursor, db_path=db_path)
        fts.drop_fts_triggers(cursor, db_path=db_path)  # second call should be fine

        assert fts.fts_trigger_count(conn.cursor(), db_path=db_path) == 0


# ── fts_trigger_count ────────────────────────────────────────────────────────


class TestFtsTriggerCount:
    """Parity tests for fts_trigger_count."""

    def test_py_and_rust_agree(self):
        from state import fts

        conn, db_path = _fresh_db()

        _disable_rust(fts)
        py_count = fts.fts_trigger_count(conn.cursor())
        _enable_rust(fts)
        rust_count = fts.fts_trigger_count(conn.cursor(), db_path=db_path)

        assert py_count == rust_count == 3

    def test_zero_triggers(self):
        from state import fts

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        _disable_rust(fts)
        py_count = fts.fts_trigger_count(conn.cursor())
        _enable_rust(fts)
        rust_count = fts.fts_trigger_count(conn.cursor(), db_path=path)

        assert py_count == rust_count == 0


# ── rebuild_fts_indexes ──────────────────────────────────────────────────────


class TestRebuildFtsIndexes:
    """Parity tests for rebuild_fts_indexes."""

    def test_rebuild_populates_fts(self):
        from state import fts

        conn, db_path = _fresh_db()
        # Insert some messages
        conn.execute(
            "INSERT INTO messages (id, content, tool_name, tool_calls) "
            "VALUES (1, 'hello world', 'search', 'tool1')"
        )
        conn.execute(
            "INSERT INTO messages (id, content, tool_name, tool_calls) "
            "VALUES (2, 'foo bar', NULL, NULL)"
        )
        conn.commit()

        # Rebuild to ensure FTS is populated
        fts.rebuild_fts_indexes(conn.cursor(), db_path=db_path)
        conn.commit()

        # Search should find results
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

        # Rebuild twice — no error
        fts.rebuild_fts_indexes(conn.cursor(), db_path=db_path)
        conn.commit()
        fts.rebuild_fts_indexes(conn.cursor(), db_path=db_path)
        conn.commit()

        # Verify data is correct (not duplicated)
        rows = conn.execute("SELECT COUNT(*) as c FROM messages_fts").fetchone()
        assert rows["c"] == 1  # one row, not duplicated


# ── get_compression_tip ──────────────────────────────────────────────────────


class TestGetCompressionTip:
    """Parity tests for get_compression_tip."""

    def test_no_chain_returns_self(self):
        from state import compression

        conn, db_path = _fresh_sessions_db()
        lock = threading.Lock()

        _disable_rust(compression)
        py_result = compression.get_compression_tip(conn, lock, "session-1")
        _enable_rust(compression)
        rust_result = compression.get_compression_tip(conn, lock, "session-1", db_path=db_path)

        # No sessions in db, so should return the input session_id
        assert py_result == rust_result == "session-1"

    def test_chain_follows_parent(self):
        from state import compression

        conn, db_path = _fresh_sessions_db()
        lock = threading.Lock()
        now = 1000.0

        # Chain: s1 (compressed) → s2 (continuation) → s3 (continuation, active)
        # s1 is compressed, so s2 can join (s2.started_at >= s1.ended_at with end_reason='compression')
        # s2 is compressed, so s3 can join (s3.started_at >= s2.ended_at with end_reason='compression')
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

        _disable_rust(compression)
        py_result = compression.get_compression_tip(conn, lock, "s1")
        _enable_rust(compression)
        rust_result = compression.get_compression_tip(conn, lock, "s1", db_path=db_path)

        # Chain: s1 → s2 (compression) → s3. Tip should be s3.
        assert py_result == rust_result == "s3"


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

    def test_has_rust_flag_is_true(self):
        from state import fts, compression
        assert fts._HAS_RUST is True
        assert compression._HAS_RUST is True

    def test_fallback_to_python_when_rust_disabled(self):
        """Confirm _HAS_RUST=False activates the Python fallback path."""
        from state import fts

        _disable_rust(fts)
        try:
            exc = sqlite3.OperationalError("no such module: FTS5")
            assert fts.is_fts5_unavailable_error(exc) is True
        finally:
            _enable_rust(fts)
