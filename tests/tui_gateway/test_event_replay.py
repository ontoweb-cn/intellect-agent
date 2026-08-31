"""Tests for the TUI gateway event replay buffer + seq/epoch plumbing."""

import asyncio

import pytest

from tui_gateway import event_replay
from tui_gateway.event_replay import EVENT_EPOCH, SessionEventLog


def test_seq_monotonic_and_stamped():
    log = SessionEventLog(capacity=8)
    seen = []
    for _ in range(5):
        params = {"type": "message.delta", "session_id": "s1"}
        seq = log.note_event("s1", params)
        seen.append(seq)
        assert params["seq"] == seq
    assert seen == [1, 2, 3, 4, 5]


def test_sessions_are_independent():
    log = SessionEventLog()
    log.note_event("a", {"type": "t"})
    log.note_event("a", {"type": "t"})
    log.note_event("b", {"type": "t"})
    assert log.last_seq("a") == 2
    assert log.last_seq("b") == 1


def test_events_since_replays_gap():
    log = SessionEventLog(capacity=8)
    for i in range(6):
        log.note_event("s", {"type": "e", "payload": {"i": i}})
    events, truncated = log.events_since("s", 3)
    assert not truncated
    assert [e["payload"]["i"] for e in events] == [3, 4, 5]  # seq 4,5,6
    assert all(e["seq"] > 3 for e in events)


def test_ring_truncation_flag():
    log = SessionEventLog(capacity=3)
    for i in range(6):
        log.note_event("s", {"type": "e", "payload": {"i": i}})
    # Oldest retained seq is 4; watermark 1 predates it → truncated.
    events, truncated = log.events_since("s", 1)
    assert truncated
    assert [e["seq"] for e in events] == [4, 5, 6]
    # Watermark at the ring's oldest-1 boundary is a clean replay.
    events, truncated = log.events_since("s", 3)
    assert not truncated and len(events) == 3


def test_drop_session_and_unknown_session():
    log = SessionEventLog()
    log.note_event("s", {"type": "e"})
    log.drop_session("s")
    assert log.last_seq("s") == 0
    assert log.events_since("s", 0) == ([], False)


def test_bad_inputs_are_silent():
    log = SessionEventLog()
    assert log.note_event("", {"type": "t"}) is None
    assert log.note_event("s", None) is None


def test_epoch_is_stable_hex():
    assert len(EVENT_EPOCH) == 32
    int(EVENT_EPOCH, 16)  # hex uuid


# ── RPC surface ────────────────────────────────────────────────────────

@pytest.fixture()
def server():
    import tui_gateway.server as srv

    return srv


def _call(server, name, rid, params):
    resp = server.dispatch({"jsonrpc": "2.0", "id": rid, "method": name,
                            "params": params}, None)
    if resp is None:
        raise AssertionError("handler dispatched to background pool")
    return resp


def test_gateway_ping_rpc(server):
    resp = _call(server, "gateway.ping", 1, {})
    assert resp["result"]["epoch"] == EVENT_EPOCH
    assert "server_time" in resp["result"]
    assert isinstance(resp["result"]["sessions"], dict)


def test_events_since_rpc(server):
    server._emit("message.delta", "replay-sess", {"text": "one"})
    server._emit("message.delta", "replay-sess", {"text": "two"})
    watermark = event_replay.event_log().last_seq("replay-sess") - 1
    server._emit("message.delta", "replay-sess", {"text": "three"})

    resp = _call(server, "session.events.since", 2,
                 {"session_id": "replay-sess", "since": watermark})
    result = resp["result"]
    assert result["epoch"] == EVENT_EPOCH
    assert result["is_truncated"] is False
    assert [e["payload"]["text"] for e in result["events"]] == ["two", "three"]
    assert result["next_seq"] == watermark + 2


def test_events_since_rpc_validation(server):
    resp = _call(server, "session.events.since", 3, {})
    assert "error" in resp
    resp = _call(server, "session.events.since", 4,
                 {"session_id": "x", "since": "zzz"})
    assert "error" in resp


def test_session_close_drops_replay_ring(server):
    # The ring must survive WS reconnects but NOT session close — otherwise
    # a long-lived server accumulates buffers for every session ever opened.
    server._emit("message.delta", "close-sess", {"text": "bye"})
    assert event_replay.event_log().last_seq("close-sess") == 1

    resp = _call(server, "session.close", 5, {"session_id": "close-sess"})
    assert resp["result"]["closed"] is False  # not a tracked session dict…

    # Register a real minimal session so close takes the finalize path.
    server._sessions["close-sess"] = {
        "agent": None,
        "session_key": "close-sess",
        "history": [],
        "history_lock": __import__("threading").Lock(),
        "history_version": 0,
    }
    server._emit("message.delta", "close-sess", {"text": "bye2"})
    assert event_replay.event_log().last_seq("close-sess") == 2

    resp = _call(server, "session.close", 6, {"session_id": "close-sess"})
    assert resp["result"]["closed"] is True
    assert event_replay.event_log().last_seq("close-sess") == 0
    events, _ = event_replay.event_log().events_since("close-sess", 0)
    assert events == []


# ── WS surface: profile fail-closed + ready epoch ─────────────────────

class _FakeWS:
    def __init__(self, path="/api/ws"):
        self.scope = {"path": path}
        self.closed = None
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def close(self, code=None, reason=None):
        self.closed = (code, reason)

    async def send_text(self, line):
        self.sent.append(line)

    async def receive_text(self):
        await asyncio.sleep(3600)
        return ""


def test_profile_route_rejected():
    from tui_gateway.ws import handle_ws

    ws = _FakeWS(path="/p/coder/api/ws")
    asyncio.run(asyncio.wait_for(handle_ws(ws), timeout=5))
    # Accept-then-close so the client gets a real WS close frame.
    assert ws.accepted is True
    assert ws.closed == (4404, "profile routing not implemented")


def test_embedded_p_segment_route_not_rejected():
    # The guard is anchored to the ^/p/ prefix: a route that merely
    # CONTAINS "/p/" (e.g. /api/p/...) must not be swept up.
    from tui_gateway.ws import handle_ws

    ws = _FakeWS(path="/api/p/x")

    async def main():
        task = asyncio.create_task(handle_ws(ws))
        await asyncio.sleep(0.5)
        assert ws.closed is None
        task.cancel()

    asyncio.run(asyncio.wait_for(main(), timeout=5))


def test_normal_route_not_rejected():
    from tui_gateway.ws import handle_ws

    ws = _FakeWS(path="/api/ws")

    async def main():
        task = asyncio.create_task(handle_ws(ws))
        await asyncio.sleep(0.5)
        assert getattr(ws, "accepted", False) is True
        assert ws.closed is None
        # ready frame carries the epoch
        import json as _json

        ready = _json.loads(ws.sent[0])
        assert ready["params"]["type"] == "gateway.ready"
        assert ready["params"]["payload"]["epoch"] == EVENT_EPOCH
        task.cancel()

    asyncio.run(asyncio.wait_for(main(), timeout=5))
