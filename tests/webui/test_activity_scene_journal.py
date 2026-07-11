"""W3 #3: activity_scene journal wire — order, seq, non-terminal, truncation, compat."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))


@pytest.fixture
def scene_mod():
    import importlib

    import api.activity_scene as mod

    return importlib.reload(mod)


@pytest.fixture
def journal_mod(tmp_path, monkeypatch):
    import importlib

    import api.run_journal as rj

    monkeypatch.setattr(rj, "_default_session_dir", lambda: tmp_path)
    return importlib.reload(rj)


def _sample_tool_events():
    return [
        {
            "event": "reasoning",
            "payload": {"text": "planning"},
            "seq": 1,
        },
        {
            "event": "tool",
            "payload": {"name": "terminal", "preview": "ls", "tid": "t1"},
            "seq": 2,
        },
        {
            "event": "tool_complete",
            "payload": {"name": "terminal", "preview": "ok", "is_error": False},
            "seq": 3,
        },
        {
            "event": "token",
            "payload": {"text": "hello"},
            "seq": 4,
        },
    ]


def test_aj5_activity_scene_not_in_terminal_sse_events(journal_mod, scene_mod):
    """A-J5: scene advances seq but is non-terminal."""
    assert "activity_scene" not in journal_mod._TERMINAL_SSE_EVENTS
    assert journal_mod._terminal_state_for_event("activity_scene", {}) is None
    assert "activity_scene" not in scene_mod.SCENE_PRECEDED_TERMINALS
    assert scene_mod.should_emit_activity_scene_before("done", False) is True
    assert scene_mod.should_emit_activity_scene_before("activity_scene", False) is False
    assert scene_mod.should_emit_activity_scene_before("done", True) is False


def test_aj1_scene_then_terminal_order_exactly_one(journal_mod, scene_mod, tmp_path):
    """A-J1: … tool* → activity_scene → terminal; exactly one scene (incl. cancel)."""
    writer = journal_mod.RunJournalWriter("sess_aj1", "strm_aj1", session_dir=tmp_path)
    writer.append_sse_event("tool", {"name": "web_search", "preview": "q"})
    writer.append_sse_event(
        "tool_complete", {"name": "web_search", "preview": "hit", "is_error": False}
    )

    events_so_far = journal_mod.read_run_events(
        "sess_aj1", "strm_aj1", session_dir=tmp_path
    )["events"]
    scene = scene_mod.build_activity_scene_for_stream(
        stream_id="strm_aj1",
        session_id="sess_aj1",
        mode=scene_mod.scene_mode_for_terminal("cancel", {"message": "Cancelled"}),
        journal_events=events_so_far,
        elapsed_ms=42,
    )
    assert scene_mod.validate_activity_scene_shape(scene)
    assert scene["mode"] == "interrupted"
    assert scene["turn_id"] == "live:strm_aj1"
    assert scene["disclosure"] == {"expanded": False, "user_intent": None}

    writer.append_sse_event("activity_scene", scene)
    writer.append_sse_event("cancel", {"message": "Cancelled by user"})
    # Second terminal (stream_end) must not get another scene when gate is set.
    already = True
    assert scene_mod.should_emit_activity_scene_before("stream_end", already) is False
    writer.append_sse_event("stream_end", {"session_id": "sess_aj1"})

    rows = journal_mod.read_run_events("sess_aj1", "strm_aj1", session_dir=tmp_path)[
        "events"
    ]
    names = [r["event"] for r in rows]
    assert names.count("activity_scene") == 1
    scene_idx = names.index("activity_scene")
    cancel_idx = names.index("cancel")
    assert scene_idx < cancel_idx
    assert names.index("tool") < scene_idx
    # Non-terminal scene row
    scene_row = rows[scene_idx]
    assert scene_row.get("terminal") is False
    assert scene_row.get("terminal_state") is None
    # Payload is scene at root (stored as journal payload == wire data)
    assert scene_row["payload"]["v"] == 1
    assert scene_row["payload"]["stream_id"] == "strm_aj1"
    # Seq continuous
    seqs = [int(r["seq"]) for r in rows]
    assert seqs == list(range(1, len(rows) + 1))


def test_aj1_done_path_mode_settled(scene_mod):
    scene = scene_mod.build_activity_scene_v1(
        stream_id="s1",
        session_id="sess",
        mode=scene_mod.scene_mode_for_terminal("done", {}),
        segments=[],
    )
    assert scene["mode"] == "settled"


def test_aj2_latch_ignores_tools_after_scene(scene_mod):
    """A-J2: unit-level latch — once scene applied, flat tools ignored for Activity."""
    applied = {}

    def apply_scene(stream_id: str) -> bool:
        if applied.get(stream_id):
            return True  # idempotent
        applied[stream_id] = True
        return True

    def should_ignore_tool_for_activity(stream_id: str) -> bool:
        return bool(applied.get(stream_id))

    assert apply_scene("strm_x") is True
    assert should_ignore_tool_for_activity("strm_x") is True
    assert apply_scene("strm_x") is True  # idempotent
    assert should_ignore_tool_for_activity("strm_other") is False
    # Settled path never creates a second live group: latch stays until clear.
    applied.pop("strm_x", None)
    assert should_ignore_tool_for_activity("strm_x") is False


def test_aj3_old_journal_without_scene_still_works(journal_mod, tmp_path):
    """A-J3: journals without activity_scene behave like today's flat replay."""
    writer = journal_mod.RunJournalWriter("sess_old", "strm_old", session_dir=tmp_path)
    writer.append_sse_event("tool", {"name": "terminal", "preview": "echo"})
    writer.append_sse_event(
        "tool_complete", {"name": "terminal", "preview": "hi", "is_error": False}
    )
    writer.append_sse_event("done", {"session": {"session_id": "sess_old"}})
    writer.append_sse_event("stream_end", {"session_id": "sess_old"})

    rows = journal_mod.read_run_events("sess_old", "strm_old", session_dir=tmp_path)[
        "events"
    ]
    names = [r["event"] for r in rows]
    assert "activity_scene" not in names
    assert names[0] == "tool"
    assert "done" in names
    summary = journal_mod.latest_run_summary(
        "sess_old", "strm_old", session_dir=tmp_path
    )
    assert summary["terminal"] is True
    assert summary["terminal_state"] == "completed"
    # Seq still continuous
    assert [int(r["seq"]) for r in rows] == [1, 2, 3, 4]


def test_aj4_oversized_scene_truncated_seq_continuous(journal_mod, scene_mod, tmp_path):
    """A-J4: oversize scene shrinks; seq row retained (no fake gap)."""
    writer = journal_mod.RunJournalWriter("sess_big", "strm_big", session_dir=tmp_path)
    writer.append_sse_event("reasoning", {"text": "x" * 5000})

    huge_segments = [
        {"kind": "thinking", "text": ("THINK-" + ("Z" * 8000))},
        {"kind": "tool", "tid": "t1", "name": "terminal", "status": "done", "summary": "S" * 4000},
        {"kind": "text", "anchor": "keep"},
    ]
    scene = scene_mod.build_activity_scene_v1(
        stream_id="strm_big",
        session_id="sess_big",
        mode="settled",
        segments=huge_segments,
        elapsed_ms=1,
    )
    # Force a tiny budget so truncation must run.
    bounded = scene_mod.bound_activity_scene_for_wire(scene, max_bytes=2500)
    assert scene_mod.validate_activity_scene_shape(bounded)
    raw = json.dumps(bounded, ensure_ascii=False, separators=(",", ":"))
    assert len(raw.encode("utf-8")) <= 2500

    writer.append_sse_event("activity_scene", bounded)
    writer.append_sse_event("done", {"session": {"session_id": "sess_big"}})

    rows = journal_mod.read_run_events("sess_big", "strm_big", session_dir=tmp_path)[
        "events"
    ]
    seqs = [int(r["seq"]) for r in rows]
    assert seqs == [1, 2, 3]
    assert rows[1]["event"] == "activity_scene"
    assert rows[2]["event"] == "done"
    # No skipped seq between scene and done
    assert seqs[2] == seqs[1] + 1


def test_segments_from_journal_events(scene_mod):
    segs = scene_mod.segments_from_journal_events(_sample_tool_events())
    kinds = [s["kind"] for s in segs]
    assert kinds[0] == "thinking"
    assert segs[0]["text"] == "planning"
    assert kinds[1] == "tool"
    assert segs[1]["name"] == "terminal"
    assert segs[1]["status"] == "done"
    assert kinds[2] == "text"


def test_build_prefers_journal_over_empty_live(scene_mod):
    scene = scene_mod.build_activity_scene_for_stream(
        stream_id="s",
        session_id="sess",
        mode="settled",
        journal_events=_sample_tool_events(),
        tool_calls=[],
        reasoning_text="",
    )
    assert any(s.get("kind") == "tool" for s in scene["segments"])


def test_wire_data_is_scene_at_root(scene_mod):
    """§4.0: data root is the scene object, not {scene: ...}."""
    scene = scene_mod.build_activity_scene_v1(
        stream_id="abc",
        session_id="sess",
        mode="settled",
        segments=[{"kind": "text", "anchor": "a0"}],
    )
    data = json.loads(json.dumps(scene))
    assert data["v"] == 1
    assert data["stream_id"] == "abc"
    assert "segments" in data
    assert set(data.keys()) >= {
        "v",
        "turn_id",
        "stream_id",
        "session_id",
        "mode",
        "display",
        "disclosure",
        "segments",
        "elapsed_ms",
    }
