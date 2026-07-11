"""P1-A activity_scene_v1 contract tests (serialize, A4 cap, A6 display alias)."""

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


def _sample_scene(**overrides):
    base = {
        "v": 1,
        "turn_id": "live:strm_abc",
        "stream_id": "strm_abc",
        "session_id": "sess_1",
        "mode": "inflight",
        "display": "compact_worklog",
        "disclosure": {"expanded": False, "user_intent": None},
        "segments": [
            {"kind": "thinking", "text": "plan"},
            {"kind": "tool", "tid": "t1", "name": "terminal", "status": "done", "summary": "ls"},
            {"kind": "text", "anchor": "a0"},
        ],
        "elapsed_ms": 1200,
    }
    base.update(overrides)
    return base


def test_serialize_deserialize_roundtrip(scene_mod):
    scene = _sample_scene()
    raw = json.dumps(scene)
    loaded = json.loads(raw)
    assert scene_mod.validate_activity_scene_shape(loaded)
    assert loaded["v"] == 1
    assert loaded["display"] == "compact_worklog"
    assert len(loaded["segments"]) == 3
    assert loaded["disclosure"]["expanded"] is False


def test_validate_rejects_missing_fields(scene_mod):
    bad = _sample_scene()
    del bad["segments"]
    assert not scene_mod.validate_activity_scene_shape(bad)
    assert not scene_mod.validate_activity_scene_shape({"v": 2, "segments": []})
    assert not scene_mod.validate_activity_scene_shape(None)


def test_cap_drops_oldest_tool_thinking_keeps_text(scene_mod):
    segments = []
    for i in range(30):
        segments.append({"kind": "tool", "tid": f"t{i}", "name": "x", "status": "done", "summary": ""})
        segments.append({"kind": "thinking", "text": f"th{i}"})
    segments.append({"kind": "text", "anchor": "keep-me"})
    segments.append({"kind": "text", "anchor": "keep-me-2"})
    # 62 segments; cap 40 → drop oldest tool/thinking first; both text survive.
    scene = _sample_scene(segments=segments)
    out = scene_mod.compact_activity_scene_v1(scene, max_segments=40)
    assert len(out["segments"]) == 40
    kinds = [s["kind"] for s in out["segments"]]
    assert kinds.count("text") == 2
    assert out["segments"][-1]["anchor"] == "keep-me-2"
    assert out["segments"][-2]["anchor"] == "keep-me"
    # Oldest tools/thinking dropped; remaining are the newest droppables + text.
    assert "t0" not in {s.get("tid") for s in out["segments"] if s.get("kind") == "tool"}


def test_cap_default_is_40(scene_mod):
    assert scene_mod.ACTIVITY_SCENE_MAX_SEGMENTS == 40
    segs = [{"kind": "tool", "tid": str(i), "name": "n", "status": "done", "summary": ""} for i in range(45)]
    out = scene_mod.compact_activity_scene_v1(_sample_scene(segments=segs))
    assert len(out["segments"]) == 40


def test_cap_all_text_drops_oldest(scene_mod):
    segs = [{"kind": "text", "anchor": f"a{i}"} for i in range(5)]
    out = scene_mod.compact_activity_scene_v1(_sample_scene(segments=segs), max_segments=3)
    assert [s["anchor"] for s in out["segments"]] == ["a2", "a3", "a4"]


def test_display_alias_mapping(scene_mod):
    assert scene_mod.display_mode_from_simplified(True) == "compact_worklog"
    assert scene_mod.display_mode_from_simplified(False) == "transparent_stream"
    assert scene_mod.simplified_from_display_mode("compact_worklog") is True
    assert scene_mod.simplified_from_display_mode("transparent_stream") is False
    assert scene_mod.simplified_from_display_mode(None) is True


def test_resolve_prefers_new_key(scene_mod):
    assert (
        scene_mod.resolve_chat_activity_display_mode(
            {"chat_activity_display_mode": "transparent_stream", "simplified_tool_calling": True}
        )
        == "transparent_stream"
    )
    assert (
        scene_mod.resolve_chat_activity_display_mode({"simplified_tool_calling": False})
        == "transparent_stream"
    )
    assert (
        scene_mod.resolve_chat_activity_display_mode({"simplified_tool_calling": True})
        == "compact_worklog"
    )
    assert scene_mod.resolve_chat_activity_display_mode({}) == "compact_worklog"


def test_dual_write_on_patch(scene_mod):
    current = {"simplified_tool_calling": True, "chat_activity_display_mode": "compact_worklog"}
    scene_mod.apply_display_mode_alias_on_write(
        {"chat_activity_display_mode": "transparent_stream"}, current
    )
    assert current["chat_activity_display_mode"] == "transparent_stream"
    assert current["simplified_tool_calling"] is False

    current2 = {"simplified_tool_calling": True, "chat_activity_display_mode": "compact_worklog"}
    scene_mod.apply_display_mode_alias_on_write({"simplified_tool_calling": False}, current2)
    assert current2["simplified_tool_calling"] is False
    assert current2["chat_activity_display_mode"] == "transparent_stream"


def test_load_sync_legacy_simplified_only(scene_mod):
    settings = {
        "simplified_tool_calling": False,
        "chat_activity_display_mode": "compact_worklog",  # default bleed-in
    }
    scene_mod.sync_display_mode_alias_from_stored(
        settings, {"simplified_tool_calling": False}
    )
    assert settings["chat_activity_display_mode"] == "transparent_stream"
    assert settings["simplified_tool_calling"] is False


def test_config_save_dual_write(tmp_path, monkeypatch):
    """Integration: save_settings dual-writes alias ↔ simplified (I7)."""
    import importlib

    # config import discovers DEFAULT_WORKSPACE at module load — pin a writable path.
    monkeypatch.setenv("INTELLECT_WEBUI_DEFAULT_WORKSPACE", str(tmp_path / "ws"))
    (tmp_path / "ws").mkdir(parents=True, exist_ok=True)

    import api.config as config

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", settings_file)
    # Avoid touching real workspace defaults during write.
    monkeypatch.setattr(config, "resolve_default_workspace", lambda v: tmp_path / "ws")
    monkeypatch.setattr(config, "get_effective_default_model", lambda: "test-model")

    config = importlib.reload(config)
    monkeypatch.setattr(config, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(config, "resolve_default_workspace", lambda v: tmp_path / "ws")
    monkeypatch.setattr(config, "get_effective_default_model", lambda: "test-model")

    saved = config.save_settings({"chat_activity_display_mode": "transparent_stream"})
    assert saved["chat_activity_display_mode"] == "transparent_stream"
    assert saved["simplified_tool_calling"] is False

    saved2 = config.save_settings({"simplified_tool_calling": True})
    assert saved2["simplified_tool_calling"] is True
    assert saved2["chat_activity_display_mode"] == "compact_worklog"

    # Read path: only legacy key on disk → derive alias (ignore default bleed-in).
    settings_file.write_text(
        json.dumps({"simplified_tool_calling": False}, indent=2),
        encoding="utf-8",
    )
    loaded = config.load_settings()
    assert loaded["chat_activity_display_mode"] == "transparent_stream"
    assert loaded["simplified_tool_calling"] is False
