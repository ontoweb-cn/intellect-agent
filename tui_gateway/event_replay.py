"""Per-session event sequence numbers + bounded replay buffer for the TUI.

The TUI gateway is a fire-and-forget event emitter: a WS client that drops
mid-turn (iOS backgrounding, laptop sleep) permanently loses every event
emitted while disconnected, with no way to ask "what did I miss".

This module adds the missing bookkeeping — deliberately *not* durable:

- **epoch** — a process-uuid regenerated at every server start. A client
  that reconnects and sees a different epoch knows its watermarks are
  meaningless and must reset (full resync), not replay.
- **seq** — a monotonically increasing per-session counter stamped onto
  every outbound event frame (``params["seq"]``).
- **bounded ring** — the last ``capacity`` event param dicts per session,
  so ``session.events.since(n)`` can replay the gap after a reconnect.
  When the client's watermark is older than the ring's tail the result is
  flagged ``is_truncated`` and the client falls back to a full resync.

Everything is in-memory, thread-safe (events are emitted from pool worker
threads, not just the loop), and best-effort: a replay failure must never
break event delivery.
"""

from __future__ import annotations

import threading
import uuid
from collections import deque
from typing import Deque, Dict, Optional, Tuple

# Regenerated per process: any change means "server restarted — reset
# watermarks". Exposed in ``gateway.ready`` and ``gateway.ping``.
EVENT_EPOCH = uuid.uuid4().hex

# Per-session replay capacity. ~500 frames covers a long tool-heavy turn
# at trivial memory cost; older gaps fall back to full resync.
DEFAULT_RING_CAPACITY = 512


class SessionEventLog:
    """Thread-safe per-session seq counter + bounded replay ring."""

    def __init__(self, capacity: int = DEFAULT_RING_CAPACITY) -> None:
        self._capacity = max(1, int(capacity))
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = {}
        self._rings: Dict[str, Deque[dict]] = {}

    def note_event(self, sid: str, params: dict) -> Optional[int]:
        """Stamp ``params["seq"]`` and buffer the frame. Returns the seq.

        Mutates ``params`` in place (callers hand the same dict to the
        transport). The buffered copy is shallow: callers must treat
        ``payload`` as owned by the emit — reusing or mutating it after
        this call would make replay diverge from what went on the wire.
        Best-effort: never raises.
        """
        if not sid or not isinstance(params, dict):
            return None
        try:
            with self._lock:
                seq = self._counters.get(sid, 0) + 1
                self._counters[sid] = seq
                params["seq"] = seq
                ring = self._rings.setdefault(sid, deque(maxlen=self._capacity))
                ring.append(dict(params))
            return seq
        except Exception:
            return None

    def events_since(self, sid: str, since: int) -> Tuple[list, bool]:
        """Replay events with ``seq > since`` for one session.

        Returns ``(events, is_truncated)``. ``is_truncated`` is True when
        the requested watermark falls before the ring's oldest retained
        frame — the client should full-resync instead of trusting the gap.
        """
        with self._lock:
            ring = self._rings.get(sid)
            if not ring:
                return [], False
            oldest = ring[0].get("seq", 0)
            truncated = int(since) < oldest - 1
            events = [dict(frame) for frame in ring if frame.get("seq", 0) > int(since)]
            return events, truncated

    def last_seq(self, sid: str) -> int:
        with self._lock:
            return self._counters.get(sid, 0)

    def drop_session(self, sid: str) -> None:
        with self._lock:
            self._counters.pop(sid, None)
            self._rings.pop(sid, None)


# Module-level singleton used by server._emit / RPC handlers.
_event_log = SessionEventLog()


def event_log() -> SessionEventLog:
    return _event_log


def epoch() -> str:
    return EVENT_EPOCH
