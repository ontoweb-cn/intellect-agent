"""W1 B1: StreamChannel offline buffer bounds (Session SSE RFC S9)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))


@pytest.fixture
def stream_channel_cls():
    import importlib

    import api.config as config

    importlib.reload(config)
    return config.StreamChannel


def test_offline_buffer_drops_oldest_by_event_cap(stream_channel_cls):
    ch = stream_channel_cls(max_events=3, max_bytes=10_000_000)
    for i in range(5):
        ch.put_nowait(("token", {"seq": i + 1, "t": i}))

    snap = ch.diagnostic_snapshot()
    assert snap["offline_buffered_events"] == 3
    assert snap["dropped_offline_events"] == 2
    assert snap["lowest_retained_seq"] == 3

    q = ch.subscribe()
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert [d["seq"] for _e, d in items] == [3, 4, 5]


def test_offline_buffer_drops_oldest_by_byte_cap(stream_channel_cls):
    # Tiny byte budget forces drop-oldest; newest retained only if it fits.
    ch = stream_channel_cls(max_events=100, max_bytes=120)
    ch.put_nowait(("token", {"seq": 1, "pad": "x" * 40}))
    ch.put_nowait(("token", {"seq": 2, "pad": "y" * 40}))
    ch.put_nowait(("token", {"seq": 3, "pad": "z" * 40}))

    snap = ch.diagnostic_snapshot()
    assert snap["offline_buffered_events"] >= 1
    assert snap["offline_buffered_events"] < 3
    assert snap["dropped_offline_events"] >= 1
    assert snap["offline_buffered_bytes"] <= 120

    q = ch.subscribe()
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert items
    assert items[-1][1]["seq"] == 3


def test_oversize_event_is_rejected(stream_channel_cls):
    """Hard ceiling: single payload > max_bytes must not pin RAM."""
    ch = stream_channel_cls(max_events=10, max_bytes=64)
    ch.put_nowait(("token", {"seq": 9, "pad": "w" * 400}))
    snap = ch.diagnostic_snapshot()
    assert snap["offline_buffered_events"] == 0
    assert snap["offline_buffered_bytes"] == 0
    assert snap["dropped_offline_events"] == 1
    assert snap["lowest_retained_seq"] is None


def test_circular_payload_fail_closed_as_drop(stream_channel_cls):
    """Non-JSON / cyclic payloads must not under-count as 256 bytes."""
    ch = stream_channel_cls(max_events=10, max_bytes=10_000_000)
    cyclic: dict = {"seq": 1}
    cyclic["self"] = cyclic
    ch.put_nowait(("token", cyclic))
    snap = ch.diagnostic_snapshot()
    assert snap["offline_buffered_events"] == 0
    assert snap["dropped_offline_events"] == 1


def test_non_json_object_fail_closed_as_drop(stream_channel_cls):
    class _Blob:
        def __init__(self):
            self.blob = b"x" * 50_000

        def __str__(self):
            return "tiny"

    ch = stream_channel_cls(max_events=10, max_bytes=10_000_000)
    ch.put_nowait(("token", {"seq": 2, "obj": _Blob()}))
    snap = ch.diagnostic_snapshot()
    assert snap["offline_buffered_events"] == 0
    assert snap["dropped_offline_events"] == 1


def test_live_subscribers_clear_offline_and_do_not_drop_counter(stream_channel_cls):
    ch = stream_channel_cls(max_events=2, max_bytes=10_000_000)
    ch.put_nowait(("token", {"seq": 1}))
    ch.put_nowait(("token", {"seq": 2}))
    ch.put_nowait(("token", {"seq": 3}))
    assert ch.diagnostic_snapshot()["dropped_offline_events"] == 1

    q = ch.subscribe()
    ch.put_nowait(("token", {"seq": 4}))
    snap = ch.diagnostic_snapshot()
    assert snap["subscriber_count"] == 1
    assert snap["offline_buffered_events"] == 0
    # Drop counter is cumulative and must not reset on live attach.
    assert snap["dropped_offline_events"] == 1
    # Live event goes to subscriber only (not re-buffered).
    live = []
    while not q.empty():
        live.append(q.get_nowait())
    assert ("token", {"seq": 4}) in live


def test_default_limits_match_rfc_s9(stream_channel_cls):
    ch = stream_channel_cls()
    snap = ch.diagnostic_snapshot()
    assert snap["offline_max_events"] == 500
    assert snap["offline_max_bytes"] == 2 * 1024 * 1024


def test_no_session_snapshot_side_effect(stream_channel_cls):
    """B1 must not invent control frames — only bound the buffer."""
    ch = stream_channel_cls(max_events=1, max_bytes=10_000_000)
    ch.put_nowait(("token", {"seq": 1}))
    ch.put_nowait(("token", {"seq": 2}))
    q = ch.subscribe()
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert items == [("token", {"seq": 2})]
    assert all(e != "session_snapshot" for e, _ in items)
    assert ch.diagnostic_snapshot()["dropped_offline_events"] == 1


def test_health_diagnostics_omit_lowest_retained_seq(stream_channel_cls, monkeypatch):
    """Deep health must not leak seq progress hints (review Important #4)."""
    import threading

    import api.routes as routes

    ch = stream_channel_cls(max_events=5, max_bytes=10_000_000)
    ch.put_nowait(("token", {"seq": 7, "t": "a"}))
    monkeypatch.setattr(routes, "STREAMS", {"strm_test": ch})
    monkeypatch.setattr(routes, "STREAMS_LOCK", threading.Lock())

    payload = routes._stream_runtime_diagnostics()
    assert payload["total_dropped_offline_events"] == 0
    assert payload["streams"]
    row = payload["streams"][0]
    assert "lowest_retained_seq" not in row
    assert row["offline_buffered_events"] == 1
    # Channel-local snapshot still exposes seq for authenticated/internal use.
    assert ch.diagnostic_snapshot()["lowest_retained_seq"] == 7
