"""Tests for agent.storage.dual_write split-brain detection."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from agent.storage.dual_write import probe_dual_write_risk


def _make_sqlite_state_db(path: Path, *, sessions: int = 0, members: int = 0) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, message_count INTEGER)"
        )
        conn.execute("CREATE TABLE oauth_providers (id TEXT PRIMARY KEY, enabled INTEGER)")
        conn.execute("CREATE TABLE oauth_tokens (id TEXT PRIMARY KEY, member_id TEXT)")
        conn.execute("CREATE TABLE members (id TEXT PRIMARY KEY, display_name TEXT)")
        for i in range(sessions):
            conn.execute(
                "INSERT INTO sessions (id, title, message_count) VALUES (?, ?, 0)",
                (f"s{i}", f"Session {i}"),
            )
        for i in range(members):
            conn.execute(
                "INSERT INTO members (id, display_name) VALUES (?, ?)",
                (f"m{i}", f"Member {i}"),
            )
        conn.commit()
    finally:
        conn.close()


def test_probe_none_for_sqlite_backend(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    (home / "config.yaml").write_text("storage:\n  backend: sqlite\n", encoding="utf-8")
    (home / "state.db").write_bytes(b"x")

    report = probe_dual_write_risk(home)
    assert report.risk == "none"
    assert not report.active_sqlite_present


def test_probe_high_when_sqlite_has_rows(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    (home / "config.yaml").write_text(
        "storage:\n  backend: postgresql\n"
        "  postgresql:\n    dsn: postgresql://localhost/test\n",
        encoding="utf-8",
    )
    _make_sqlite_state_db(home / "state.db", sessions=2)

    class _FakeDB:
        def __init__(self):
            self._conn = self

        def execute(self, sql):
            class _Cur:
                def fetchone(inner):
                    if "sessions" in sql:
                        return {"c": 0}
                    return {"c": 0}

            return _Cur()

        def close(self):
            pass

    monkeypatch.setattr("intellect_state.SessionDB", _FakeDB)

    report = probe_dual_write_risk(home)
    assert report.risk == "high"
    assert report.active_sqlite_present
    assert report.sqlite_counts.get("sessions") == 2
    assert any("still holds data" in msg for msg in report.messages)


def test_probe_high_when_recently_modified(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    (home / "config.yaml").write_text(
        "storage:\n  backend: postgresql\n"
        "  postgresql:\n    dsn: postgresql://localhost/test\n",
        encoding="utf-8",
    )
    _make_sqlite_state_db(home / "state.db", sessions=0)

    class _FakeDB:
        def __init__(self):
            self._conn = self

        def execute(self, sql):
            class _Cur:
                def fetchone(inner):
                    return {"c": 0}

            return _Cur()

        def close(self):
            pass

    monkeypatch.setattr("intellect_state.SessionDB", _FakeDB)

    report = probe_dual_write_risk(home, recent_window_seconds=3600)
    assert report.risk == "high"
    assert report.sqlite_recently_modified
    assert any("modified within" in msg for msg in report.messages)


def test_probe_medium_for_empty_stale_file(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    (home / "config.yaml").write_text(
        "storage:\n  backend: postgresql\n"
        "  postgresql:\n    dsn: postgresql://localhost/test\n",
        encoding="utf-8",
    )
    _make_sqlite_state_db(home / "state.db", sessions=0)
    old = time.time() - 7200
    import os

    os.utime(home / "state.db", (old, old))

    class _FakeDB:
        def __init__(self):
            self._conn = self

        def execute(self, sql):
            class _Cur:
                def fetchone(inner):
                    return {"c": 0}

            return _Cur()

        def close(self):
            pass

    monkeypatch.setattr("intellect_state.SessionDB", _FakeDB)

    report = probe_dual_write_risk(home, recent_window_seconds=3600)
    assert report.risk == "medium"
    assert not report.sqlite_recently_modified
