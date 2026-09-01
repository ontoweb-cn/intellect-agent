"""Tests for the durable delegation completion queue (A2-3③)."""

import pytest

from tools import delegation_persistence as dp


@pytest.fixture()
def clean_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    dp.reset_for_tests()
    yield tmp_path
    dp.reset_for_tests()


def test_persist_and_pop(clean_queue):
    assert dp.persist("sess-a", "synth one") is True
    assert dp.persist("sess-a", "synth two") is True
    assert dp.persist("sess-b", "other session") is True

    out = dp.pop_for_session("sess-a", limit=3)
    assert out == ["synth one", "synth two"]
    # Popped rows are gone.
    assert dp.pop_for_session("sess-a") == []
    # Other session untouched.
    assert dp.pop_for_session("sess-b") == ["other session"]


def test_pop_limit(clean_queue):
    for i in range(5):
        dp.persist("s", f"m{i}")
    assert len(dp.pop_for_session("s", limit=3)) == 3
    assert len(dp.pop_for_session("s")) == 2


def test_crash_recovery_flow(clean_queue):
    """persist (gateway synthesized) -> [crash] -> pop recovers the row."""
    dp.persist("sess-x", "survived restart")
    dp.reset_for_tests()  # simulate process restart (new connection)
    assert dp.pop_for_session("sess-x") == ["survived restart"]


def test_put_back_attempts_cap(clean_queue, monkeypatch):
    monkeypatch.setattr(dp, "_MAX_ATTEMPTS", 3)
    dp.persist("s", "poison")
    # attempts 1 and 2: put_back re-inserts with attempts+1.
    assert dp.put_back("s", "poison") is True
    assert dp.put_back("s", "poison") is True
    # attempt 3: dropped.
    assert dp.put_back("s", "poison") is False
    assert dp.pop_for_session("s") == []


def test_empty_inputs_are_noop(clean_queue):
    assert dp.persist("", "x") is False
    assert dp.persist("s", "") is False
    assert dp.pop_for_session("") == []
    dp.delete_for_session("")


def test_delete_for_session(clean_queue):
    dp.persist("s", "x")
    dp.delete_for_session("s")
    assert dp.pop_for_session("s") == []


# ── A2-4 every_n cadence ───────────────────────────────────────────────

from agent.moa_loop import _coerce_fanout  # noqa: E402


def test_coerce_fanout_modes():
    assert _coerce_fanout("user_turn") == ("user_turn", 0)
    assert _coerce_fanout(None) == ("user_turn", 0)
    assert _coerce_fanout("per_iteration") == ("per_iteration", 0)
    assert _coerce_fanout("every_n:3") == ("every_n", 3)
    assert _coerce_fanout({"mode": "every_n", "n": 4}) == ("every_n", 4)
    assert _coerce_fanout("every_n:1") == ("per_iteration", 0)  # N=1 folds
    assert _coerce_fanout("garbage") == ("user_turn", 0)
    assert _coerce_fanout("every_n:xx") == ("user_turn", 0)


def test_enabled_false_references_filtered():
    from agent.moa_loop import MoaRunner

    runner = MoaRunner({
        "references": [
            {"provider": "p1", "model": "m1", "enabled": True},
            {"provider": "p2", "model": "m2", "enabled": False},
            {"provider": "p3", "model": "m3"},
        ],
        "aggregator": {"provider": "p1", "model": "m1"},
    })
    assert len(runner._references) == 2
    assert all(r.get("provider") != "p2" for r in runner._references)
