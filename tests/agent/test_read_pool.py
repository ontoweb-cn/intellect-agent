"""Tests for the SessionDB read pool (G-14 / A1-6, stage 1).

Contract:
- Pool lives ONLY in WAL mode — DELETE-journal fallback (NFS/SMB) keeps the
  historical single-lock path ("database is locked" storm guard).
- `storage.sqlite.read_pool: false` / INTELLECT_STATE_READ_POOL=0 kill-switch
  degrades to the single-lock path entirely.
- Reads return the same data as the write connection sees (after commit).
"""

import sqlite3
import threading

import pytest

from agent.storage.sqlite_backend import SQLiteBackend, _read_pool_config_enabled


@pytest.fixture()
def wal_backend(tmp_path):
    be = SQLiteBackend(db_path=tmp_path / "state.db")
    be.initialize()
    assert be._journal_mode == "wal", "test fixture expects WAL (memory fs)"
    return be


def _seed_rows(be, n=5):
    def _do(conn, n=n):
        conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
        for i in range(n):
            conn.execute("INSERT INTO t (v) VALUES (?)", (f"val-{i}",))

    be.execute_write(_do)


def test_read_pool_active_on_wal(wal_backend):
    assert wal_backend.read_pool_active is True


def test_read_pool_returns_committed_data(wal_backend):
    _seed_rows(wal_backend)
    seen = wal_backend.execute_read(
        lambda conn: conn.execute("SELECT v FROM t ORDER BY id").fetchall()
    )
    assert [r["v"] for r in seen] == [f"val-{i}" for i in range(5)]


def test_kill_switch_deactivates_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_STATE_READ_POOL", "off")
    be = SQLiteBackend(db_path=tmp_path / "state.db")
    assert be.read_pool_active is False  # even before/without initialize


def test_delete_journal_deactivates_pool(tmp_path, monkeypatch):
    """Simulate the apply_wal_with_fallback DELETE fallback: pool must not
    activate even when the kill-switch is on."""
    monkeypatch.setenv("INTELLECT_STATE_READ_POOL", "")
    be = SQLiteBackend(db_path=tmp_path / "state.db")
    be.initialize()
    be._journal_mode = "delete"  # simulate NFS fallback outcome
    assert be.read_pool_active is False
    # Reads still work via the single-lock fallback path.
    _seed_rows(be)
    seen = be.execute_read(
        lambda conn: conn.execute("SELECT COUNT(*) AS c FROM t").fetchone()
    )
    assert seen["c"] == 5


def test_concurrent_reads_and_writes_no_lock_error(tmp_path):
    """WAL + pool: concurrent readers and a writer must not hit 'database is
    locked' (the failure mode the DELETE-fallback guard exists for)."""
    be = SQLiteBackend(db_path=tmp_path / "state.db")
    be.initialize()
    _seed_rows(be, 3)

    errors: list[str] = []
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set() and i < 50:
            try:
                be.execute_write(
                    lambda conn, i=i: conn.execute(
                        "INSERT INTO t (v) VALUES (?)", (f"w-{i}",)
                    )
                )
            except sqlite3.OperationalError as exc:
                errors.append(f"writer: {exc}")
            i += 1
        stop.set()

    def reader():
        while not stop.is_set():
            try:
                be.execute_read(
                    lambda conn: conn.execute("SELECT COUNT(*) AS c FROM t").fetchone()
                )
            except sqlite3.OperationalError as exc:
                errors.append(f"reader: {exc}")
                stop.set()

    threads = [threading.Thread(target=writer), *[threading.Thread(target=reader) for _ in range(4)]]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors[:5]


def test_pooled_connection_is_read_only(wal_backend):
    conn = wal_backend._get_read_conn()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO t (v) VALUES ('nope')")


def test_config_default_is_enabled():
    assert _read_pool_config_enabled({}) is True
    assert _read_pool_config_enabled({"read_pool": False}) is False
    assert _read_pool_config_enabled({"read_pool": "off"}) is False
    assert _read_pool_config_enabled({"read_pool": True}) is True


def test_close_tears_down_pooled_connections(wal_backend):
    wal_backend.execute_read(lambda conn: conn.execute("SELECT 1").fetchone())
    tid = threading.get_ident()
    assert tid in wal_backend._read_conns
    wal_backend.close()
    assert tid not in wal_backend._read_conns
