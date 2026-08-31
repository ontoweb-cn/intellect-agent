"""Tests for the unified deadline layer (G-02 / A1-1)."""

import asyncio
import time

import pytest

from agent.deadline import (
    DeadlineExpired,
    clamp_timeout,
    resolve_timeout,
    run_bounded_async,
    run_bounded_sync,
)


# ── clamp_timeout ──────────────────────────────────────────────────────

def test_clamp_none_and_nonpositive_are_unbounded():
    assert clamp_timeout(None) is None
    assert clamp_timeout(0) is None
    assert clamp_timeout(-5) is None
    assert clamp_timeout(-0.001) is None


def test_clamp_garbage_is_unbounded_with_warning():
    assert clamp_timeout("banana") is None
    assert clamp_timeout(float("nan")) is None
    assert clamp_timeout(object()) is None


def test_clamp_caps_at_one_year():
    assert clamp_timeout(10**12) == 31_536_000.0
    assert clamp_timeout(30) == 30.0


# ── resolve_timeout ────────────────────────────────────────────────────

@pytest.fixture()
def clean_timeouts(monkeypatch):
    """Neutralize both resolution sources above `default`."""
    monkeypatch.setattr(
        "intellect_cli.config.load_config_readonly", lambda: {}, raising=False
    )
    monkeypatch.delenv("INTELLECT_TEST_TIMEOUT", raising=False)
    yield


def test_resolver_default(clean_timeouts):
    assert resolve_timeout("test.key", default=42) == 42.0
    assert resolve_timeout("test.key", default=None) is None


def test_resolver_env_beats_default(clean_timeouts, monkeypatch):
    monkeypatch.setenv("INTELLECT_TEST_TIMEOUT", "7")
    assert resolve_timeout("test.key", default=42, env_var="INTELLECT_TEST_TIMEOUT") == 7.0
    monkeypatch.setenv("INTELLECT_TEST_TIMEOUT", "0")
    assert resolve_timeout("test.key", default=42, env_var="INTELLECT_TEST_TIMEOUT") is None
    monkeypatch.setenv("INTELLECT_TEST_TIMEOUT", "bogus")
    assert resolve_timeout("test.key", default=42, env_var="INTELLECT_TEST_TIMEOUT") == 42.0


def test_resolver_config_beats_env(monkeypatch):
    monkeypatch.setattr(
        "intellect_cli.config.load_config_readonly",
        lambda: {"timeouts": {"test": {"key": 99}}},
        raising=False,
    )
    monkeypatch.setenv("INTELLECT_TEST_TIMEOUT", "7")
    assert resolve_timeout("test.key", default=1, env_var="INTELLECT_TEST_TIMEOUT") == 99.0


def test_resolver_explicit_zero_in_config_means_unbounded(monkeypatch):
    monkeypatch.setattr(
        "intellect_cli.config.load_config_readonly",
        lambda: {"timeouts": {"test": {"key": 0}}},
        raising=False,
    )
    assert resolve_timeout("test.key", default=42) is None


def test_resolver_bool_config_falls_through(monkeypatch):
    monkeypatch.setattr(
        "intellect_cli.config.load_config_readonly",
        lambda: {"timeouts": {"test": {"key": True}}},
        raising=False,
    )
    assert resolve_timeout("test.key", default=42) == 42.0


def test_resolver_broken_config_degrades(monkeypatch):
    def _boom():
        raise RuntimeError("config stack broken")

    monkeypatch.setattr("intellect_cli.config.load_config_readonly", _boom, raising=False)
    monkeypatch.delenv("INTELLECT_TEST_TIMEOUT", raising=False)
    assert resolve_timeout("test.key", default=42) == 42.0


# ── run_bounded_sync ───────────────────────────────────────────────────

def test_bounded_sync_returns_value():
    assert run_bounded_sync(lambda: 5, 5, label="t") == 5


def test_bounded_sync_none_is_unbounded():
    assert run_bounded_sync(lambda: "ok", None, label="t") == "ok"
    assert run_bounded_sync(lambda: "ok", 0, label="t") == "ok"


def test_bounded_sync_timeout_raises_deadline_expired():
    with pytest.raises(DeadlineExpired) as ei:
        run_bounded_sync(lambda: time.sleep(30), 0.2, label="sleeper")
    assert ei.value.label == "sleeper"


def test_bounded_sync_propagates_operation_exception_verbatim():
    class WeirdError(Exception):
        pass

    def _boom():
        raise WeirdError("original")

    with pytest.raises(WeirdError):
        run_bounded_sync(_boom, 5, label="boom")


def test_bounded_sync_on_timeout_hook_runs():
    calls = []
    with pytest.raises(DeadlineExpired):
        run_bounded_sync(
            lambda: time.sleep(30), 0.1, label="hook", on_timeout=lambda: calls.append(1)
        )
    assert calls == [1]  # hook ran in caller thread before the raise


# ── run_bounded_async ──────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def test_bounded_async_value_and_unbounded():
    async def main():
        r = await run_bounded_async(_value_coro(), None, label="u")
        assert r.timed_out is False and r.value == "ok"

    _run(main())


async def _value_coro():
    await asyncio.sleep(0.01)
    return "ok"


async def _sleeper():
    await asyncio.sleep(30)


def test_bounded_async_timeout_reifies():
    async def main():
        start = time.monotonic()
        r = await run_bounded_async(_sleeper(), 0.2, label="sleeper")
        assert r.timed_out is True and r.timeout_s == 0.2
        assert time.monotonic() - start < 5  # abandoned, not awaited

    _run(main())


def test_bounded_async_timeout_raises_if_demanded():
    """BoundedResult contract: timeout is a value; DeadlineExpired only on demand."""

    async def main():
        r = await run_bounded_async(_sleeper(), 0.2, label="s")
        assert r.timed_out is True
        with pytest.raises(DeadlineExpired):
            r.raise_if_timed_out()

    _run(main())


def test_bounded_async_operation_exception_propagates_verbatim():
    async def main():
        async def boom():
            raise ValueError("original operation error")

        with pytest.raises(ValueError, match="original operation error"):
            await run_bounded_async(boom(), 5, label="boom")

    _run(main())


def test_bounded_async_on_abandon_runs_before_expiry_return():
    async def main():
        events = []
        r = await run_bounded_async(
            _sleeper(), 0.2, label="ab", on_abandon=lambda: events.append("abandoned")
        )
        assert r.timed_out
        assert events == ["abandoned"]

    _run(main())


def test_bounded_async_operation_wins_race():
    async def main():
        r = await run_bounded_async(_value_coro(), 5.0, label="fast")
        assert r.timed_out is False and r.value == "ok"

    _run(main())


# ── DeadlineExpired typing contract ────────────────────────────────────

def test_deadline_expired_is_timeout_error():
    assert issubclass(DeadlineExpired, TimeoutError)


# ── Caller-cancellation propagation (P1-1) ─────────────────────────────

def test_bounded_async_caller_cancel_propagates_and_cancels_inner():
    """asyncio.wait does NOT propagate cancellation to waited tasks — the
    primitive must cancel-and-abandon the inner task itself (P1-1), else the
    bounded coroutine leaks forever."""
    events = {"inner_cancelled": False}

    async def tracked_sleeper():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            events["inner_cancelled"] = True
            raise

    async def main():
        outer = asyncio.ensure_future(
            run_bounded_async(tracked_sleeper(), 60, label="caller-cancel")
        )
        await asyncio.sleep(0.1)
        outer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await outer
        # Give the loop a beat to deliver cancellation to the inner task.
        for _ in range(20):
            if events["inner_cancelled"]:
                break
            await asyncio.sleep(0.05)
        assert events["inner_cancelled"], "inner task was not cancelled"

    _run(main())


def test_bounded_async_unbounded_caller_cancel_also_cancels_inner():
    events = {"inner_cancelled": False}

    async def tracked_sleeper():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            events["inner_cancelled"] = True
            raise

    async def main():
        outer = asyncio.ensure_future(
            run_bounded_async(tracked_sleeper(), None, label="u-cancel")
        )
        await asyncio.sleep(0.1)
        outer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await outer
        for _ in range(20):
            if events["inner_cancelled"]:
                break
            await asyncio.sleep(0.05)
        assert events["inner_cancelled"]

    _run(main())


# ── Completion-wins-over-expiry (P2-3) ─────────────────────────────────

def test_bounded_async_completed_result_survives_late_expiry():
    """A result that landed is returned even when the deadline timer fires
    after completion: the discriminating check is `task.cancelled()` (we
    cancelled it), not `expiry.done()` (timer fired)."""
    import asyncio as _aio

    async def main():
        # Op finishes at 0.05s; deadline at 0.25s — expiry will fire long
        # AFTER completion. The expiry callback early-returns on task.done()
        # and must not flip the outcome.
        r = await run_bounded_async(_value_coro(), 0.25, label="late-expiry")
        assert r.timed_out is False and r.value == "ok"
        # Let the (cancelled) timer's would-be moment pass to prove no
        # post-hoc corruption of the returned result object.
        await _aio.sleep(0.3)
        assert r.timed_out is False and r.value == "ok"

    _run(main())
