"""Tests for gateway/delivery_ledger.py — durable outbound delivery obligations."""

import pytest

from gateway import delivery_ledger as dl


@pytest.fixture()
def ledger_db(tmp_path, monkeypatch):
    """Redirect the ledger to a temp state.db (per-test isolation)."""
    monkeypatch.setattr(dl, "_db_path", lambda: tmp_path / "state.db")


def _record(content="hello", **kw):
    oid = dl.compute_obligation_id(kw.get("session_key", "sk"), "msg1", content)
    dl.record_obligation(
        obligation_id=oid,
        session_key=kw.get("session_key", "sk"),
        platform=kw.get("platform", "telegram"),
        chat_id=kw.get("chat_id", "123"),
        thread_id=kw.get("thread_id"),
        content=content,
    )
    return oid


def test_compute_id_is_stable_and_distinct():
    a = dl.compute_obligation_id("sk", "msg1", "hello")
    b = dl.compute_obligation_id("sk", "msg1", "hello")
    c = dl.compute_obligation_id("sk", "msg2", "hello")
    assert a == b
    assert a != c


def test_delivered_row_is_not_recoverable(ledger_db):
    oid = _record()
    dl.mark_attempting(oid)
    dl.mark_delivered(oid)
    assert dl.sweep_recoverable() == []


def test_sweep_claims_dead_owner_pending(ledger_db, monkeypatch):
    oid = _record()
    monkeypatch.setattr(dl, "_owner_alive", lambda *a: False)  # dead owner
    claimed = dl.sweep_recoverable()
    assert len(claimed) == 1
    assert claimed[0]["obligation_id"] == oid
    # pending = send never started → redeliver plainly, no marker
    assert claimed[0]["needs_marker"] is False


def test_sweep_marks_ambiguous_rows(ledger_db, monkeypatch):
    oid = _record()
    dl.mark_attempting(oid)  # crashed mid-await is ambiguous
    monkeypatch.setattr(dl, "_owner_alive", lambda *a: False)
    claimed = dl.sweep_recoverable()
    assert len(claimed) == 1
    assert claimed[0]["needs_marker"] is True


def test_sweep_abandons_after_max_attempts(ledger_db, monkeypatch):
    _record()
    monkeypatch.setattr(dl, "_owner_alive", lambda *a: False)
    for _ in range(dl.MAX_ATTEMPTS):
        assert len(dl.sweep_recoverable()) == 1
    # attempts exhausted → abandoned, not returned again
    assert dl.sweep_recoverable() == []


def test_runtime_sweep_claims_allowlisted_error(ledger_db, monkeypatch):
    # Stable process fingerprint so the runtime sweep passes its fail-closed
    # (started is not None) gate and exercises the real claim path.
    monkeypatch.setattr(dl, "_owner_stamp", lambda: (12345, 67890))
    oid = _record()
    dl.mark_failed(oid, error="send_path_degraded")
    claimed = dl.sweep_failed_for_runtime("telegram")
    assert len(claimed) == 1
    assert claimed[0]["needs_marker"] is True
    assert claimed[0]["runtime_recovery"] is True


def test_runtime_sweep_skips_non_allowlisted_error(ledger_db, monkeypatch):
    monkeypatch.setattr(dl, "_owner_stamp", lambda: (12345, 67890))
    oid = _record()
    dl.mark_failed(oid, error="blocked bot")
    assert dl.sweep_failed_for_runtime("telegram") == []


def test_ledger_enabled_gate():
    assert dl.ledger_enabled({}) is True
    assert dl.ledger_enabled({"gateway": {}}) is True
    assert dl.ledger_enabled({"gateway": {"delivery_ledger": False}}) is False
    assert dl.ledger_enabled({"gateway": {"delivery_ledger": "off"}}) is False
