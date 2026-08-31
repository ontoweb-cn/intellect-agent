"""Tests for the cross-turn stream-stale circuit breaker (G-03 / A1-3)."""

import pytest

from agent.chat_completion_helpers import (
    _bump_stale_streak,
    _check_stale_giveup,
    _reset_stale_streak,
    _stale_streak,
    _stream_stale_giveup_limit,
)


class FakeAgent:
    pass


def test_streak_starts_at_zero():
    a = FakeAgent()
    assert _stale_streak(a) == 0


def test_bump_increments_and_reason_logged():
    a = FakeAgent()
    assert _bump_stale_streak(a, "streaming stale kill") == 1
    assert _bump_stale_streak(a) == 2
    assert _stale_streak(a) == 2


def test_reset_clears_streak():
    a = FakeAgent()
    _bump_stale_streak(a)
    _bump_stale_streak(a)
    _reset_stale_streak(a)
    assert _stale_streak(a) == 0


def test_giveup_limit_default_and_env(monkeypatch):
    a = FakeAgent()
    monkeypatch.delenv("INTELLECT_STREAM_STALE_GIVEUP", raising=False)
    assert _stream_stale_giveup_limit(a) == 5
    monkeypatch.setenv("INTELLECT_STREAM_STALE_GIVEUP", "0")
    assert _stream_stale_giveup_limit(a) == 0  # 0 disables
    monkeypatch.setenv("INTELLECT_STREAM_STALE_GIVEUP", "bogus")
    assert _stream_stale_giveup_limit(a) == 5


def test_check_giveup_passes_below_limit():
    a = FakeAgent()
    for _ in range(4):
        _bump_stale_streak(a)
    _check_stale_giveup(a, {"model": "m"})  # must not raise at 4 < 5


def test_check_giveup_raises_at_limit():
    a = FakeAgent()
    for _ in range(5):
        _bump_stale_streak(a)
    with pytest.raises(RuntimeError, match="consecutive stale"):
        _check_stale_giveup(a, {"model": "m"})


def test_check_giveup_zero_disables_breaker(monkeypatch):
    a = FakeAgent()
    for _ in range(50):
        _bump_stale_streak(a)
    monkeypatch.setenv("INTELLECT_STREAM_STALE_GIVEUP", "0")
    _check_stale_giveup(a)  # disabled: no raise


def test_check_giveup_resets_fixes_future_calls():
    """After the operator resets the streak (e.g. provider swap), the gate
    opens again without any other change."""
    a = FakeAgent()
    for _ in range(5):
        _bump_stale_streak(a)
    _reset_stale_streak(a)
    _check_stale_giveup(a)


def test_swap_reset_semantics_fallback(monkeypatch):
    """try_activate_fallback success resets the streak (source-level pin)."""
    import inspect

    from agent import chat_completion_helpers as cch

    src = inspect.getsource(cch.try_activate_fallback)
    assert "_reset_stale_streak(agent)" in src
    # And it must sit BEFORE the success return, not after an exception path.
    reset_pos = src.index("_reset_stale_streak(agent)")
    success_pos = src.index("return True")
    assert reset_pos < success_pos


def test_swap_reset_semantics_switch_and_restore():
    import inspect

    from agent import agent_runtime_helpers as arh

    switch_src = inspect.getsource(arh.switch_model)
    assert "_reset_stale_streak(agent)" in switch_src
    restore_src = inspect.getsource(arh.restore_primary_runtime)
    assert "_reset_stale_streak(agent)" in restore_src
