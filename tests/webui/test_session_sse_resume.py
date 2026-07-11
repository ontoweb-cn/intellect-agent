"""W2 B2: Session SSE resume cursor + journal-first gap (RFC S6–S8)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))


@pytest.fixture
def sse():
    import importlib

    import api.session_sse as mod

    return importlib.reload(mod)


class _Headers(dict):
    def get(self, key, default=None):
        for k, v in self.items():
            if str(k).lower() == str(key).lower():
                return v
        return default


def test_parse_cursor_bare_and_event_id(sse):
    assert sse.parse_cursor_token("12").after_seq == 12
    p = sse.parse_cursor_token("strm_abc:7")
    assert p.after_seq == 7 and p.run_id == "strm_abc"
    assert not p.malformed


def test_malformed_cursor_not_zero(sse):
    for raw in ("nope", "strm:", ":5", "a:b:c:x", "-1"):
        p = sse.parse_cursor_token(raw)
        assert p.malformed, raw
        assert p.after_seq is None or p.malformed


def test_stale_run_id(sse):
    p = sse.parse_cursor_token("runA:3", expected_run_id="runB")
    assert p.stale_run
    assert p.after_seq == 3
    assert not p.malformed


def test_resolve_query_wins_over_last_event_id(sse):
    qs = {"after_seq": ["10"], "cursor": ["ignored"]}
    headers = _Headers({"Last-Event-ID": "strm:99"})
    p = sse.resolve_resume_cursor(qs, headers, expected_run_id="strm")
    assert p.after_seq == 10
    assert p.source == "query"


def test_resolve_cursor_alias_and_header_fallback(sse):
    qs = {"cursor": ["strm:4"]}
    p = sse.resolve_resume_cursor(qs, None, expected_run_id="strm")
    assert p.after_seq == 4 and p.source == "query"

    qs2: dict = {}
    headers = _Headers({"Last-Event-ID": "strm:8"})
    p2 = sse.resolve_resume_cursor(qs2, headers, expected_run_id="strm")
    assert p2.after_seq == 8 and p2.source == "last_event_id"


def test_resolve_malformed_header(sse):
    p = sse.resolve_resume_cursor({}, _Headers({"Last-Event-ID": "garbage"}))
    assert p.malformed
    assert p.source == "last_event_id"


def test_cursor_to_after_seq_raises(sse):
    assert sse.cursor_to_after_seq(None) is None
    assert sse.cursor_to_after_seq("5") == 5
    with pytest.raises(ValueError, match="unknown_cursor"):
        sse.cursor_to_after_seq("bad")


def test_plan_journal_contiguous(sse):
    events = [{"seq": i, "event": "token", "payload": {"i": i}} for i in range(1, 6)]
    emit, gap = sse.plan_journal_replay(events, after_seq=2)
    assert gap is None
    assert [e["seq"] for e in emit] == [3, 4, 5]


def test_plan_journal_gap_in_middle(sse):
    events = [{"seq": 1}, {"seq": 2}, {"seq": 5}]
    emit, gap = sse.plan_journal_replay(events, after_seq=0)
    # after_seq=0 → expect seq 1 first; contiguous until hole before 5
    assert gap == "gap"
    assert [e["seq"] for e in emit] == [1, 2]


def test_plan_journal_gap_after_cursor(sse):
    events = [{"seq": 1}, {"seq": 2}, {"seq": 5}]
    emit, gap = sse.plan_journal_replay(events, after_seq=2)
    assert gap == "gap"
    assert emit == []


def test_plan_journal_replay_event_cap(sse):
    events = [{"seq": i, "pad": "x"} for i in range(1, 20)]
    emit, gap = sse.plan_journal_replay(events, after_seq=0, max_events=5, max_bytes=10_000_000)
    assert gap == "gap"
    assert len(emit) == 5


def test_plan_journal_replay_byte_cap(sse):
    events = [{"seq": i, "pad": "y" * 100} for i in range(1, 10)]
    emit, gap = sse.plan_journal_replay(events, after_seq=0, max_events=100, max_bytes=250)
    assert gap == "gap"
    assert 1 <= len(emit) < 9


def test_offline_buffer_drop_is_not_journal_gap(sse):
    """T9: continuity is journal-only; in-memory drops do not invent gaps."""
    events = [{"seq": i} for i in range(1, 6)]
    emit, gap = sse.plan_journal_replay(events, after_seq=2)
    assert gap is None
    assert [e["seq"] for e in emit] == [3, 4, 5]


def test_session_snapshot_shape(sse):
    snap = sse.build_session_snapshot(
        session_id="sess",
        active_stream_id=None,
        reason="no_active_run",
    )
    assert snap["v"] == 1
    assert snap["reason"] == "no_active_run"
    assert snap["messages_reload"] is True
    assert snap["active_stream_id"] is None


# ── Handler smoke (T2 / T6 / T10 wiring) ─────────────────────────────────────


class _FakeWfile:
    def __init__(self):
        self.buf = bytearray()

    def write(self, data: bytes):
        self.buf.extend(data)

    def flush(self):
        pass

    def text(self) -> str:
        return self.buf.decode("utf-8", errors="replace")


class _FakeHandler:
    def __init__(self, headers=None):
        self.headers = headers or _Headers()
        self.wfile = _FakeWfile()
        self.status = None
        self._hdrs: list[tuple[str, str]] = []

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self._hdrs.append((key, value))

    def end_headers(self):
        pass


def _parse_sse_events(text: str) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    event_name = "message"
    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line == "":
            if data_lines:
                events.append((event_name, "\n".join(data_lines)))
            event_name = "message"
            data_lines = []
    if data_lines:
        events.append((event_name, "\n".join(data_lines)))
    return events


@pytest.fixture
def routes_mod(tmp_path, monkeypatch):
    import importlib

    import api.config as config
    import api.routes as routes

    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / ".intellect"))
    # Point journal at tmp via SESSION_DIR if models already imported.
    monkeypatch.setattr(config, "SESSION_DIR", tmp_path / "sessions", raising=False)
    (tmp_path / "sessions").mkdir(parents=True, exist_ok=True)
    return importlib.reload(routes)


def test_handle_sse_malformed_cursor_emits_snapshot(routes_mod, monkeypatch):
    import json
    from urllib.parse import urlparse

    handler = _FakeHandler()
    monkeypatch.setattr(routes_mod, "STREAMS", {})
    monkeypatch.setattr(routes_mod, "find_run_summary", lambda *_a, **_k: {"session_id": "sess1", "terminal": True})
    parsed = urlparse("http://x/api/chat/stream?stream_id=run1&after_seq=not-a-number")
    assert routes_mod._handle_sse_stream(handler, parsed) is True
    assert handler.status == 200
    events = _parse_sse_events(handler.wfile.text())
    assert events
    assert events[0][0] == "session_snapshot"
    body = json.loads(events[0][1])
    assert body["reason"] == "unknown_cursor"
    assert body["messages_reload"] is True
    # Must not have replayed tokens as after_seq=0
    assert all(name != "token" for name, _ in events)


def test_handle_sse_query_beats_last_event_id(routes_mod, monkeypatch, tmp_path):
    """T10: after_seq query wins over Last-Event-ID."""
    import json
    from urllib.parse import urlparse

    from api import run_journal
    from api.run_journal import append_run_event

    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    sid, rid = "sess_t10", "run_t10"
    for i in range(1, 6):
        append_run_event(sid, rid, "token", {"i": i}, session_dir=session_dir, seq=i)

    monkeypatch.setattr(routes_mod, "STREAMS", {})
    monkeypatch.setattr(run_journal, "_default_session_dir", lambda: session_dir)

    handler = _FakeHandler(_Headers({"Last-Event-ID": f"{rid}:1"}))
    parsed = urlparse(f"http://x/api/chat/stream?stream_id={rid}&after_seq=3")
    assert routes_mod._handle_sse_stream(handler, parsed) is True
    events = _parse_sse_events(handler.wfile.text())
    token_payloads = [json.loads(data) for name, data in events if name == "token"]
    assert [p.get("i") for p in token_payloads] == [4, 5]


def test_handle_sse_session_bootstrap_no_active_run(routes_mod, monkeypatch):
    from urllib.parse import urlparse
    import json

    class _Sess:
        session_id = "sess_idle"
        active_stream_id = None

    monkeypatch.setattr(routes_mod, "STREAMS", {})

    def _fake_get_session(*_a, **_k):
        return _Sess()

    import api.models as models

    monkeypatch.setattr(models, "get_session", _fake_get_session)
    handler = _FakeHandler()
    parsed = urlparse("http://x/api/chat/stream?session_id=sess_idle")
    assert routes_mod._handle_sse_stream(handler, parsed) is True
    events = _parse_sse_events(handler.wfile.text())
    assert events[0][0] == "session_snapshot"
    assert json.loads(events[0][1])["reason"] == "no_active_run"

def test_handle_sse_stale_run_cursor(routes_mod, monkeypatch):
    import json
    from urllib.parse import urlparse

    handler = _FakeHandler()
    monkeypatch.setattr(routes_mod, "STREAMS", {})
    monkeypatch.setattr(
        routes_mod,
        "find_run_summary",
        lambda *_a, **_k: {"session_id": "sess1", "terminal": True, "last_seq": 3},
    )
    parsed = urlparse("http://x/api/chat/stream?stream_id=runB&cursor=runA:3")
    assert routes_mod._handle_sse_stream(handler, parsed) is True
    events = _parse_sse_events(handler.wfile.text())
    assert events[0][0] == "session_snapshot"
    assert json.loads(events[0][1])["reason"] == "stale_run"


def test_handle_sse_headers_before_body_for_snapshot(routes_mod, monkeypatch):
    """T11-ish: freeze decision completes and headers are sent before body frames."""
    from urllib.parse import urlparse

    handler = _FakeHandler()
    order = []

    _orig_begin = routes_mod._begin_sse_headers

    def _track_begin(h):
        order.append("headers")
        return _orig_begin(h)

    def _track_emit(*_a, **_k):
        order.append("snapshot")

    monkeypatch.setattr(routes_mod, "_begin_sse_headers", _track_begin)
    monkeypatch.setattr(routes_mod, "_emit_session_snapshot", _track_emit)
    monkeypatch.setattr(routes_mod, "STREAMS", {})
    parsed = urlparse("http://x/api/chat/stream?stream_id=run1&after_seq=nope")
    routes_mod._handle_sse_stream(handler, parsed)
    assert order[:2] == ["headers", "snapshot"]


def test_plan_journal_replay_streams_without_full_list(sse, tmp_path):
    """Replay caps must work on an iterator (no full-file materialization)."""
    from api.run_journal import append_run_event, iter_run_events

    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True)
    sid, rid = "sess_stream", "run_stream"
    for i in range(1, 12):
        append_run_event(sid, rid, "token", {"i": i}, session_dir=session_dir, seq=i)

    it = iter_run_events(sid, rid, after_seq=2, session_dir=session_dir)
    emit, gap = sse.plan_journal_replay(it, after_seq=2, max_events=3, max_bytes=10_000_000)
    assert gap == "gap"
    assert [e["seq"] for e in emit] == [3, 4, 5]


def test_opt_d_paths_require_auth_and_csrf():
    """D-A1: restart/resume are not public and not CSRF-exempt."""
    from api.auth import PUBLIC_PATHS
    from api.routes import _csrf_exempt_path

    for path in ("/api/health/restart", "/api/wakeup/resume", "/api/health/restart/status"):
        assert path not in PUBLIC_PATHS
        assert _csrf_exempt_path(path) is False
