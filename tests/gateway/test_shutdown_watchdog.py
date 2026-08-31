"""Tests for the gateway loop-liveness watchdog + shutdown floor timer."""

import asyncio
import os
import threading
import time


from gateway.shutdown_watchdog import GatewayWatchdog, watchdog_enabled


def _make_watchdog(tmp_path, **overrides):
    kills = []
    exits = []
    ready = threading.Event()

    def on_kill(reason):
        kills.append(reason)
        ready.set()

    def exit_fn(code):
        exits.append(code)
        ready.set()

    kwargs = dict(
        heartbeat_interval_s=0.2,
        stall_threshold_s=0.4,
        max_strikes=2,
        shutdown_grace_s=1.0,
        heartbeat_file=str(tmp_path / "gateway.heartbeat"),
        on_kill=on_kill,
        exit_fn=exit_fn,
    )
    kwargs.update(overrides)
    return (
        GatewayWatchdog(None, **kwargs),
        kills,
        exits,
        ready,
    )


def test_enabled_by_default_and_env_gated(monkeypatch):
    monkeypatch.delenv("INTELLECT_GATEWAY_WATCHDOG", raising=False)
    assert watchdog_enabled()
    monkeypatch.setenv("INTELLECT_GATEWAY_WATCHDOG", "0")
    assert not watchdog_enabled()
    monkeypatch.setenv("INTELLECT_GATEWAY_WATCHDOG", "off")
    assert not watchdog_enabled()


def test_stalled_loop_kills_after_strikes(tmp_path):
    wd, kills, exits, ready = _make_watchdog(tmp_path)
    # No heartbeat task: simulate a frozen loop by seeding one stale beat.
    wd._last_beat_monotonic = time.monotonic() - 999.0
    wd._last_beat_wall = time.time() - 999.0
    wd.start()
    # start() re-beats immediately, so rewind it again post-start.
    wd._last_beat_monotonic = time.monotonic() - 999.0
    assert ready.wait(timeout=10.0)
    assert kills and "stalled" in kills[0]
    assert exits == [75]  # GATEWAY_SERVICE_RESTART_EXIT_CODE
    wd._thread = None  # _kill replaced os._exit; monitor thread lingers
    wd._stop_event.set()


def test_healthy_loop_never_kills(tmp_path):
    async def main():
        loop = asyncio.get_running_loop()
        wd, kills, exits, ready = _make_watchdog(tmp_path)
        wd._loop = loop
        assert wd.start()
        await asyncio.sleep(2.0)  # many beat/check cycles
        assert kills == [] and exits == []
        # Heartbeat file refreshed off-loop by the monitor thread.
        assert os.path.exists(wd._heartbeat_file)
        wd.stop()
        assert wd._thread is None

    asyncio.run(main())


def test_shutdown_floor_kills_on_deadline(tmp_path):
    wd, kills, exits, ready = _make_watchdog(tmp_path)
    wd._last_beat_monotonic = time.monotonic() - 999.0  # loop "frozen"
    wd.start()
    # Shutdown mode must ignore beat staleness and use the floor deadline.
    wd._last_beat_monotonic = time.monotonic() - 999.0
    wd.arm_shutdown(0.0)  # 0 drain + grace 1.0s floor
    assert ready.wait(timeout=10.0)
    assert kills == ["shutdown floor exceeded"]
    assert exits == [75]
    wd._thread = None
    wd._stop_event.set()


def test_disarm_shutdown_clears_floor(tmp_path):
    wd, kills, exits, ready = _make_watchdog(tmp_path)
    wd.arm_shutdown(0.0)
    wd.disarm_shutdown()
    wd.start()
    # Keep the (loop-less) watchdog alive with manual beats: disarm must
    # have cleared the floor deadline, and fresh beats must clear strikes.
    for _ in range(10):
        wd.beat()
        time.sleep(0.15)
    assert kills == [] and exits == []
    wd._stop_event.set()


def test_disarm_shutdown_resets_strikes(tmp_path):
    wd, _, _, _ = _make_watchdog(tmp_path)
    # Charge strikes while stale, then disarm — strikes must not survive
    # into the beat-freshness regime (where they'd bias toward a kill).
    wd._strikes = wd._max_strikes - 1
    wd.arm_shutdown(10.0)
    wd.disarm_shutdown()
    assert wd._strikes == 0
    wd.stop()


def test_start_disabled_is_noop(monkeypatch, tmp_path):
    monkeypatch.setenv("INTELLECT_GATEDOG_WATCHDOG", "0")
    monkeypatch.setenv("INTELLECT_GATEWAY_WATCHDOG", "0")
    wd, _, _, _ = _make_watchdog(tmp_path)
    assert wd.start() is False
    assert wd._thread is None
    wd.stop()  # safe on never-started
