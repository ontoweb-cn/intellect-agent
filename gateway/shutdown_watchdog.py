"""Event-loop liveness watchdog + OS-thread shutdown floor for the gateway.

A living process is not a living gateway: if the asyncio event loop freezes
(sync IO on the loop, a wedged C extension, GC thrash), every async recovery
mechanism we ship — drain timeouts, lease release, delivery ledger sweeps —
is frozen with it. The service manager only rescues *dead* processes, not
zombies.

This module adds two independent, deliberately dumb protections:

1. **Loop liveness watchdog** — an asyncio task on the gateway loop beats a
   ``threading.Event`` every ``heartbeat_interval_s``. An OS *daemon* thread
   (off-loop by construction) watches that beat. After ``max_strikes``
   consecutive checks with no beat, it dumps all thread tracebacks via
   ``faulthandler`` and hard-exits with
   ``GATEWAY_SERVICE_RESTART_EXIT_CODE`` so the service manager restarts us.

2. **Shutdown floor timer** — when a graceful shutdown starts we arm a floor
   deadline (``restart_drain_timeout`` + grace). If shutdown exceeds it, the
   same thread dumps + hard-exits rather than hanging in limbo.

Heartbeat *file* writes and systemd ``WATCHDOG=1`` pings both happen on the
monitor thread, never on the loop (Hermes #90502: an on-loop watchdog fsync
freezes the very loop it monitors). ``WATCHDOG=1`` is only fed while the loop
is provably fresh — a frozen loop must let systemd's own watchdog fire.

Everything is best-effort: constructor/start failures disable the watchdog
silently (a monitoring failure must never take down the gateway).
"""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import os
import threading
import time
from typing import Callable, Optional

from gateway.restart import GATEWAY_SERVICE_RESTART_EXIT_CODE
from gateway.systemd_notify import notify_watchdog, watchdog_usec

logger = logging.getLogger(__name__)

# Opt-out for operators running without a service manager and preferring a
# zombie over a restart loop. Any of "0", "false", "no", "off" disables.
_DISABLE_ENV = "INTELLECT_GATEWAY_WATCHDOG"

DEFAULT_HEARTBEAT_INTERVAL_S = 5.0
DEFAULT_STALL_THRESHOLD_S = 30.0
DEFAULT_MAX_STRIKES = 3
DEFAULT_SHUTDOWN_GRACE_S = 30.0


def watchdog_enabled() -> bool:
    return os.environ.get(_DISABLE_ENV, "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


class GatewayWatchdog:
    """Loop liveness monitor + shutdown floor timer (OS thread).

    Lifecycle::

        wd = GatewayWatchdog(loop)
        wd.start()                      # after the loop is running
        ...
        wd.arm_shutdown(deadline_s)     # when graceful shutdown begins
        wd.stop()                       # clean path — disarm everything
    """

    def __init__(
        self,
        loop,
        *,
        heartbeat_interval_s: float = DEFAULT_HEARTBEAT_INTERVAL_S,
        stall_threshold_s: float = DEFAULT_STALL_THRESHOLD_S,
        max_strikes: int = DEFAULT_MAX_STRIKES,
        shutdown_grace_s: float = DEFAULT_SHUTDOWN_GRACE_S,
        exit_code: int = GATEWAY_SERVICE_RESTART_EXIT_CODE,
        heartbeat_file: Optional[str] = None,
        on_kill: Optional[Callable[[str], None]] = None,
        exit_fn: Optional[Callable[[int], None]] = None,
    ) -> None:
        self._loop = loop
        self._heartbeat_interval_s = max(0.1, float(heartbeat_interval_s))
        self._stall_threshold_s = max(self._heartbeat_interval_s, float(stall_threshold_s))
        self._max_strikes = max(1, int(max_strikes))
        self._shutdown_grace_s = max(1.0, float(shutdown_grace_s))
        self._exit_code = int(exit_code)
        self._heartbeat_file = heartbeat_file
        self._on_kill = on_kill
        self._exit_fn = exit_fn or os._exit

        self._beat_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_beat_monotonic = 0.0
        self._last_beat_wall = 0.0
        self._strikes = 0
        self._shutdown_deadline: Optional[float] = None
        self._thread: Optional[threading.Thread] = None
        self._heartbeat_task = None
        self._started = False
        self._watchdog_usec = watchdog_usec()

    # ── Loop side ──────────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            self.beat()
            await asyncio.sleep(self._heartbeat_interval_s)

    def beat(self) -> None:
        """Record a loop heartbeat. Called from the loop thread only."""
        # The lock matters for free-threaded builds; under the GIL the
        # float stores are already atomic, but we don't rely on that.
        with self._beat_lock:
            self._last_beat_monotonic = time.monotonic()
            self._last_beat_wall = time.time()

    def _schedule_heartbeat(self) -> None:
        # A None loop means monitor-only mode (no loop-side beater): the
        # monitor thread then judges liveness purely on externally-supplied
        # beats — used by tests and by pre-loop startup windows.
        if self._loop is not None:
            self._heartbeat_task = self._loop.create_task(self._heartbeat_loop())

    # ── Monitor side (OS thread) ───────────────────────────────────────

    def _monitor(self) -> None:
        check_interval = min(self._heartbeat_interval_s, 5.0)
        while not self._stop_event.wait(check_interval):
            now_monotonic = time.monotonic()

            # Shutdown floor timer takes precedence: during shutdown the
            # loop is *expected* to be busy draining — the deadline, not
            # beat freshness, is the kill condition.
            if self._shutdown_deadline is not None:
                if now_monotonic >= self._shutdown_deadline:
                    self._kill("shutdown floor exceeded")
                continue

            last_beat = self._last_beat_monotonic_read()
            loop_age = now_monotonic - last_beat if last_beat else float("inf")

            if loop_age <= self._stall_threshold_s:
                self._strikes = 0
                self._write_heartbeat_file()
                # Feed systemd only while the loop is provably fresh, and
                # only within the manager's budget (half-interval safety
                # margin handled by check_interval <= 5s).
                if self._watchdog_usec and loop_age * 1_000_000 < self._watchdog_usec / 2:
                    notify_watchdog()
                continue

            self._strikes += 1
            logger.error(
                "Gateway event loop unresponsive: no beat for %.1fs (strike %d/%d)",
                loop_age,
                self._strikes,
                self._max_strikes,
            )
            if self._strikes >= self._max_strikes:
                self._kill(f"event loop stalled {loop_age:.0f}s")

    def _last_beat_monotonic_read(self) -> float:
        with self._beat_lock:
            return self._last_beat_monotonic

    def _write_heartbeat_file(self) -> None:
        """Best-effort heartbeat mtime for external monitors. Off-loop."""
        if not self._heartbeat_file:
            return
        # Atomic replace so an external monitor never observes a truncated
        # file mid-rewrite.
        tmp_path = f"{self._heartbeat_file}.tmp"
        try:
            with self._beat_lock:
                wall = self._last_beat_wall
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(f'{{"pid": {os.getpid()}, "beat": {wall}}}\n')
            os.replace(tmp_path, self._heartbeat_file)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _kill(self, reason: str) -> None:
        logger.critical("Gateway watchdog hard-exiting (reason=%s, code=%d)",
                        reason, self._exit_code)
        if self._on_kill:
            try:
                self._on_kill(reason)
            except Exception:
                pass
        try:
            faulthandler.dump_traceback()
        except Exception:
            pass
        self._exit_fn(self._exit_code)

    # ── Public lifecycle ───────────────────────────────────────────────

    def start(self) -> bool:
        """Start monitoring. Returns False (and does nothing) if disabled."""
        if not watchdog_enabled() or self._started:
            return False
        self._started = True
        self.beat()
        self._schedule_heartbeat()
        self._thread = threading.Thread(
            target=self._monitor,
            name="gateway-watchdog",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Gateway watchdog armed (heartbeat=%.1fs stall=%.1fs strikes=%d)",
            self._heartbeat_interval_s,
            self._stall_threshold_s,
            self._max_strikes,
        )
        return True

    def arm_shutdown(self, drain_timeout_s: float) -> None:
        """Arm the shutdown floor: drain budget + grace, then hard exit."""
        self._shutdown_deadline = (
            time.monotonic() + max(0.0, float(drain_timeout_s)) + self._shutdown_grace_s
        )
        logger.info(
            "Gateway watchdog shutdown floor armed: %.1fs (drain %.1fs + grace %.1fs)",
            max(0.0, float(drain_timeout_s)) + self._shutdown_grace_s,
            drain_timeout_s,
            self._shutdown_grace_s,
        )

    def disarm_shutdown(self) -> None:
        self._shutdown_deadline = None
        # A disarm means we're back on a path where beat freshness is the
        # kill condition — don't inherit strikes charged during shutdown
        # mode (where staleness was expected, not counted).
        self._strikes = 0

    def stop(self) -> None:
        """Disarm everything. Safe to call multiple times / never started."""
        self._shutdown_deadline = None
        self._strikes = 0
        self._stop_event.set()
        if self._heartbeat_task is not None:
            task, self._heartbeat_task = self._heartbeat_task, None
            # Cancellation must happen on the owning loop (stop() may be
            # called from a signal-handler thread during shutdown).
            try:
                self._loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=6.0)
            self._thread = None
        self._started = False
