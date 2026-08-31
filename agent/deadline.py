"""Unified deadline layer — bounded execution primitives + timeout resolver (G-02).

Port of Hermes ``agent/deadline.py`` trimmed to the primitives intellect
consumes. One place resolves every timeout in the tree:

    resolve_timeout("mcp.tool_call", default=120, env_var="INTELLECT_MCP_TOOL_TIMEOUT")

resolution order: ``config.yaml timeouts.<dotted.key>`` > legacy env var >
``default``; every result passes :func:`clamp_timeout` (``None``/non-positive
= unbounded, capped at one year).

Two execution primitives reify *timeouts* so abandoned work cannot wedge
the process:

- :func:`run_bounded_async` — async; the deadline is driven by a daemon
  ``threading.Timer`` scheduling onto the loop, so expiry still fires while
  the loop is busy. On expiry the task is cancelled and **abandoned** (we
  never await cancellation — cancellation-shielded teardowns in
  anyio/httpcore/MCP are a known permanent-hang source). A one-shot
  watchdog dumps all thread tracebacks if the loop never processes expiry.
- :func:`run_bounded_sync` — daemon worker thread + bounded wait. Do NOT
  use in hot loops: every timeout permanently leaks one abandoned thread.

Invariants (review contract — keep on any change):
1. Exceptions from the wrapped operation propagate unchanged; only the
   timeout outcome is reified (:class:`DeadlineExpired` / :class:`BoundedResult`),
   and :class:`DeadlineExpired` is a ``TimeoutError`` distinct from
   transport timeouts so error classification never blames a provider.
2. ``None`` / non-positive = unbounded (the ``0 disables`` convention).
3. Explicit user config always beats code-derived defaults.
"""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

# Hard ceiling: one year in seconds — prevents absurd configured values from
# overflowing time_t on macOS (Hermes #83220).
MAX_SAFE_TIMEOUT_S = 31_536_000.0

# Grace before the watchdog dumps tracebacks after an unprocessed expiry.
_LOOP_BLOCKED_DUMP_GRACE_S = 5.0

T = TypeVar("T")


class DeadlineExpired(TimeoutError):
    """A bounded operation exceeded its deadline.

    Deliberately a distinct ``TimeoutError`` subclass so error classification
    can tell "our deadline fired" (never routes to provider failover) from
    "the provider timed out" (routes to failover).
    """

    def __init__(self, label: str, timeout_s: Optional[float]) -> None:
        self.label = label
        self.timeout_s = timeout_s
        super().__init__(
            f"bounded operation {label!r} exceeded deadline ({timeout_s:.1f}s)"
        )


@dataclass(frozen=True)
class BoundedResult:
    """Outcome of a bounded operation — timeout is a value, not a surprise."""

    timed_out: bool
    value: Any = None
    elapsed_s: float = 0.0
    timeout_s: Optional[float] = None
    label: str = ""

    def raise_if_timed_out(self) -> None:
        if self.timed_out:
            raise DeadlineExpired(self.label, self.timeout_s)


def clamp_timeout(timeout: Any) -> Optional[float]:
    """Normalize any configured timeout to a safe float, or ``None`` (unbounded).

    ``None`` / NaN / non-numeric / values <= 0 → ``None`` (the ``0 disables``
    convention); everything else capped at :data:`MAX_SAFE_TIMEOUT_S`.
    """
    if timeout is None:
        return None
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        logger.warning("clamp_timeout: non-numeric timeout %r treated as unbounded", timeout)
        return None
    if math.isnan(value) or value <= 0:
        return None
    return min(value, MAX_SAFE_TIMEOUT_S)


def _lookup_dotted(config: Any, dotted: str) -> Any:
    """Walk a nested dict by dotted key; ``None`` when any level is missing."""
    node = config
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def resolve_timeout(
    key: str,
    *,
    default: Optional[float] = None,
    env_var: Optional[str] = None,
) -> Optional[float]:
    """Resolve a timeout for *key* — the single resolution point in the tree.

    Order: ``config.yaml timeouts.<key>`` > legacy env var > ``default``.
    Invalid config values (bool / NaN / non-numeric) fall through to the next
    source rather than meaning "unbounded"; an explicit ``0`` / negative in
    config or env DOES mean deliberately unbounded. Every exit passes
    :func:`clamp_timeout`. Config-stack failures degrade, never raise.
    """
    try:
        from intellect_cli.config import load_config_readonly

        timeouts_section = _lookup_dotted(load_config_readonly(), "timeouts")
        configured = _lookup_dotted(timeouts_section, key) if timeouts_section else None
        if configured is not None and not isinstance(configured, bool):
            try:
                if float(configured) <= 0:
                    return None  # explicit 0/negative = deliberately unbounded
            except (TypeError, ValueError):
                return None  # garbage config value: refuse to guess
            clamped = clamp_timeout(configured)
            if clamped is not None:
                return clamped
    except Exception as exc:  # config stack broken — degrade, don't die
        logger.debug("resolve_timeout(%r): config unavailable: %s", key, exc)

    if env_var:
        import os

        raw = os.environ.get(env_var)
        if raw:
            try:
                if float(raw) <= 0:
                    return None
                clamped = clamp_timeout(float(raw))
                if clamped is not None:
                    return clamped
            except ValueError:
                logger.debug("resolve_timeout(%r): bad env %s=%r", key, env_var, raw)

    return clamp_timeout(default)


def _consume_abandoned(task: "asyncio.Future") -> None:
    """Silence the 'exception was never retrieved' warning of an abandoned task."""

    def _drain(t: "asyncio.Future") -> None:
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            return
        except asyncio.InvalidStateError:
            return
        if exc is not None:
            logger.debug("abandoned bounded task %r raised: %s", label_of(t), exc)

    def label_of(t: "asyncio.Future") -> str:
        return getattr(t, "get_name", lambda: "?")()

    task.add_done_callback(_drain)


async def run_bounded_async(
    coro,
    timeout: Optional[float],
    *,
    label: str,
    on_abandon: Optional[Callable[[], None]] = None,
) -> BoundedResult:
    """Run *coro* on the running loop under *timeout*; abandon on expiry.

    Must be awaited from loop context. The deadline :class:`threading.Timer`
    is a daemon thread scheduling onto the loop, so expiry processing still
    gets queued while the loop is busy (and the +grace watchdog dumps all
    thread tracebacks if the loop never gets to process it — the loop being
    wedged is exactly the failure mode this primitive exists for).
    """
    loop = asyncio.get_running_loop()
    clamped = clamp_timeout(timeout)
    start = time.monotonic()

    task = loop.create_task(coro, name=f"bounded:{label}")

    if clamped is None:
        try:
            value = await task
        finally:
            elapsed = time.monotonic() - start
        return BoundedResult(False, value, elapsed, None, label)

    expiry = loop.create_future()
    state = {"watchdog_armed": False}

    def _set_expiry() -> None:
        if not expiry.done():
            expiry.set_result(None)
        if not state["watchdog_armed"]:
            state["watchdog_armed"] = True

            def _dump_if_never_processed() -> None:
                if not task.done() and not expiry.done():
                    logger.error(
                        "bounded %r: loop never processed deadline expiry within "
                        "%.0fs — dumping thread tracebacks",
                        label,
                        _LOOP_BLOCKED_DUMP_GRACE_S,
                    )
                    try:
                        faulthandler.dump_traceback()
                    except Exception:
                        pass

            watchdog = threading.Timer(_LOOP_BLOCKED_DUMP_GRACE_S, _dump_if_never_processed)
            watchdog.daemon = True
            watchdog.start()

    def _expire_on_loop() -> None:
        # Runs ON the loop (scheduled from the Timer thread) — task.cancel()
        # is only thread-safe via call_soon_threadsafe. Order matters:
        # on_abandon first (happens-before for suspect marking), then
        # cancel-then-abandon (never await the cancellation — shielded
        # teardowns can hang forever), then wake the awaiter.
        if task.done():
            return
        if on_abandon is not None:
            try:
                on_abandon()
            except Exception:
                logger.debug("bounded %r: on_abandon raised", label, exc_info=True)
        if task.cancel():
            _consume_abandoned(task)
        _set_expiry()

    def _on_expiry() -> None:
        # Runs on the Timer daemon thread: the loop may be wedged by sync
        # work, so expiry must be scheduled onto it, not executed here.
        try:
            loop.call_soon_threadsafe(_expire_on_loop)
        except RuntimeError:
            pass  # loop closed during shutdown

    timer = threading.Timer(clamped, _on_expiry)
    timer.daemon = True
    timer.start()

    try:
        done, _pending = await asyncio.wait(
            {task, expiry}, return_when=asyncio.FIRST_COMPLETED
        )
    except asyncio.CancelledError:
        # Caller cancellation: asyncio.wait deliberately does NOT propagate
        # cancellation to the waited tasks — without this handler the bounded
        # task would keep running forever (leaked coroutine + unretrieved-
        # exception warnings). Cancel-and-abandon exactly like expiry, then
        # let the caller's cancellation continue.
        if not task.done() and task.cancel():
            _consume_abandoned(task)
        raise
    finally:
        timer.cancel()

    elapsed = time.monotonic() - start
    # Completion wins over expiry (Hermes contract): a result that landed is
    # returned even if the deadline raced it. Only a task WE cancelled
    # (task.cancelled()) counts as timed out — that excludes the narrow race
    # where the operation finished between the expiry check and cancel().
    if task.cancelled() or task not in done:
        return BoundedResult(True, None, elapsed, clamped, label)
    return BoundedResult(False, task.result(), elapsed, clamped, label)


def run_bounded_sync(
    fn: Callable[[], T],
    timeout: Optional[float],
    *,
    label: str,
    on_timeout: Optional[Callable[[], None]] = None,
) -> T:
    """Run *fn* on a daemon thread under *timeout*; abandon on expiry.

    ``on_timeout`` runs in the caller thread after expiry, before the raise —
    cleanup/suspect-marking happens even though *fn* may still be running in
    the abandoned worker. Do NOT use in hot loops (see module docstring).
    """
    clamped = clamp_timeout(timeout)
    if clamped is None:
        return fn()

    result: dict[str, Any] = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            result["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised in caller verbatim
            result["exc"] = exc
        finally:
            done.set()

    worker = threading.Thread(target=_worker, name=f"bounded-sync:{label}", daemon=True)
    worker.start()
    if not done.wait(clamped):
        if on_timeout is not None:
            try:
                on_timeout()
            except Exception:
                logger.debug("bounded-sync %r: on_timeout raised", label, exc_info=True)
        raise DeadlineExpired(label, clamped)
    if "exc" in result:
        raise result["exc"]
    return result["value"]
