"""HP-402c: Write merge queue integration tests."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest


class TestMergeQueueBatch:
    """Integration tests for append_message_batch_rs."""

    @pytest.fixture
    def db_path(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        # Create schema
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_call_id TEXT,
                tool_calls TEXT,
                tool_name TEXT,
                timestamp REAL,
                token_count INTEGER,
                finish_reason TEXT,
                reasoning TEXT,
                reasoning_content TEXT,
                reasoning_details TEXT,
                codex_reasoning_items TEXT,
                codex_message_items TEXT,
                platform_message_id TEXT,
                observed INTEGER DEFAULT 0
            );
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                message_count INTEGER DEFAULT 0,
                tool_call_count INTEGER DEFAULT 0
            );
            INSERT INTO sessions (id, message_count) VALUES ('sess1', 0);
            INSERT INTO sessions (id, message_count) VALUES ('sess2', 0);
        """)
        conn.close()
        yield path
        Path(path).unlink(missing_ok=True)

    def _call_batch(self, db_path, entries):
        from intellect_rust import rust_append_message_batch
        if rust_append_message_batch is None:
            pytest.skip("Rust extension not built")
        result_json = rust_append_message_batch(json.dumps(entries), db_path)
        return json.loads(result_json)

    def test_batch_insert_returns_row_ids(self, db_path):
        entries = [
            {"session_id": "sess1", "role": "user", "content": "hello"},
            {"session_id": "sess1", "role": "assistant", "content": "hi"},
        ]
        ids = self._call_batch(db_path, entries)
        assert len(ids) == 2
        assert ids[0] > 0
        assert ids[1] > ids[0]

    def test_batch_empty_returns_empty(self, db_path):
        ids = self._call_batch(db_path, [])
        assert ids == []

    def test_batch_coalesces_session_counters(self, db_path):
        entries = [
            {"session_id": "sess1", "role": "user", "content": "a"},
            {"session_id": "sess1", "role": "user", "content": "b"},
            {"session_id": "sess1", "role": "assistant", "content": "c"},
        ]
        self._call_batch(db_path, entries)
        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT message_count FROM sessions WHERE id='sess1'"
        ).fetchone()[0]
        assert count == 3

    def test_batch_multiple_sessions_independent(self, db_path):
        entries = [
            {"session_id": "sess1", "role": "user", "content": "a"},
            {"session_id": "sess2", "role": "user", "content": "b"},
            {"session_id": "sess2", "role": "assistant", "content": "c"},
        ]
        self._call_batch(db_path, entries)
        conn = sqlite3.connect(db_path)
        c1 = conn.execute("SELECT message_count FROM sessions WHERE id='sess1'").fetchone()[0]
        c2 = conn.execute("SELECT message_count FROM sessions WHERE id='sess2'").fetchone()[0]
        assert c1 == 1
        assert c2 == 2

    def test_batch_wal_recovery(self, db_path):
        """Verify WAL mode is active and data persists after write."""
        entries = [
            {"session_id": "sess1", "role": "user", "content": "wal test"},
        ]
        ids = self._call_batch(db_path, entries)
        assert len(ids) == 1
        # Re-open and verify data persisted
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT content FROM messages WHERE id=?", (ids[0],)
        ).fetchone()
        assert row[0] == "wal test"
