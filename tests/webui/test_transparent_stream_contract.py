"""W6 transparent_stream contract — coexistence with W5 + config defaults."""

from __future__ import annotations

from webui.api.config import _SETTINGS_BOOL_KEYS, _SETTINGS_DEFAULTS
from webui.api.deferred_worklog import should_defer_activity_worklog


def test_default_display_mode_is_compact_worklog():
    assert _SETTINGS_DEFAULTS.get("chat_activity_display_mode") == "compact_worklog"
    assert _SETTINGS_DEFAULTS.get("simplified_tool_calling") is True


def test_transparent_never_defers_worklog_d_m11():
    """D-M11 / DW6: transparent_stream must not use deferred Activity shells."""
    assert (
        should_defer_activity_worklog(
            enabled=True,
            compact_worklog=False,  # transparent
            tool_count=20,
            settled=True,
        )
        is False
    )
    assert (
        should_defer_activity_worklog(
            enabled=True,
            compact_worklog=True,
            tool_count=20,
            settled=True,
        )
        is True
    )


def test_deferred_flag_still_default_off():
    assert _SETTINGS_DEFAULTS.get("deferred_activity_worklog") is False
    assert "deferred_activity_worklog" in _SETTINGS_BOOL_KEYS


def test_normalize_transparent_events_skips_text_segments():
    """Mirror of JS normalizeTransparentEvents (R3 / C4) — thinking+tool only."""
    segments = [
        {"kind": "thinking", "text": "plan"},
        {"kind": "text", "anchor": "hello"},
        {"kind": "tool", "tid": "t1", "name": "web_search", "status": "done", "summary": "ok"},
        {"kind": "thinking", "text": "next"},
        {"kind": "tool", "tid": "t2", "name": "terminal", "status": "done", "summary": "ls"},
    ]
    events = []
    for seg in segments:
        kind = seg.get("kind")
        if kind == "thinking":
            events.append(("thinking", seg.get("text", "")))
        elif kind == "tool":
            events.append(("tool", seg.get("tid"), seg.get("name")))
        # text skipped
    assert events == [
        ("thinking", "plan"),
        ("tool", "t1", "web_search"),
        ("thinking", "next"),
        ("tool", "t2", "terminal"),
    ]
    assert all(e[0] != "text" for e in events)
