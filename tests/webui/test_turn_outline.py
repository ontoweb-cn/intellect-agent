"""Behavior contract for the chat turn navigator outline."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))


@pytest.fixture
def outline():
    import importlib

    import api.turn_outline as mod

    return importlib.reload(mod)


def test_empty_and_short_transcripts_still_return_entries(outline):
    assert outline.build_turn_outline(None) == []
    assert outline.build_turn_outline([]) == []
    short = outline.build_turn_outline(
        [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "ack"},
        ]
    )
    assert len(short) == 1
    assert short[0]["raw_idx"] == 0
    assert short[0]["title"] == "one"
    assert short[0]["reply"] == "ack"


def test_skips_tool_empty_user_compaction_and_preserved_task_list(outline):
    messages = [
        {"role": "tool", "content": "tool output"},
        {"role": "user", "content": "   "},
        {
            "role": "user",
            "content": "[Your active task list was preserved across context compression]\n- stay",
        },
        {"role": "user", "content": "[context compaction finished]"},
        {"role": "user", "content": "real question"},
        {"role": "assistant", "content": "[context compaction finished]"},
        {"role": "assistant", "content": "", "tool_calls": [{"name": "search"}]},
        {"role": "tool", "content": "found it"},
        {"role": "assistant", "content": "the answer"},
    ]
    entries = outline.build_turn_outline(messages)
    assert [item["title"] for item in entries] == ["real question"]
    assert entries[0]["raw_idx"] == 4
    assert entries[0]["reply"] == "the answer"


def test_workspace_prefix_and_markdown_do_not_enter_the_title(outline):
    messages = [
        {
            "role": "user",
            "content": "[Workspace::v1: /tmp/proj]\n# See [docs](https://example.test) now",
        },
        {"role": "assistant", "content": "ok"},
    ]
    entries = outline.build_turn_outline(messages)
    assert entries[0]["title"] == "See docs now"


def test_legacy_workspace_prefix_is_stripped(outline):
    messages = [
        {"role": "user", "content": "[Workspace: /tmp/old]\nhello"},
        {"role": "assistant", "content": "ok"},
    ]
    assert outline.build_turn_outline(messages)[0]["title"] == "hello"


def test_reply_does_not_cross_the_next_user_message(outline):
    messages = [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "Q2"},
        {"role": "assistant", "content": "A2"},
    ]
    entries = outline.build_turn_outline(messages)
    assert [(item["title"], item["reply"]) for item in entries] == [("Q1", "A1"), ("Q2", "A2")]


def test_attachment_only_user_message_uses_filename(outline):
    messages = [
        {"role": "user", "content": "", "attachments": [{"path": "notes/pic.png"}]},
        {"role": "assistant", "content": "seen"},
    ]
    entries = outline.build_turn_outline(messages)
    assert entries[0]["title"] == "pic.png"
    assert entries[0]["reply"] == "seen"


def test_array_content_uses_text_parts_only(outline):
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "before "},
                {"type": "image", "url": "x"},
                {"type": "text", "text": "after"},
            ],
        }
    ]
    assert outline.build_turn_outline(messages)[0]["title"] == "before after"


def test_title_and_reply_are_cut_to_limits(outline):
    messages = [
        {"role": "user", "content": "q" * 120},
        {"role": "assistant", "content": "a" * 200},
    ]
    entry = outline.build_turn_outline(messages)[0]
    assert len(entry["title"]) == outline.TITLE_LIMIT
    assert entry["title"] == "q" * outline.TITLE_LIMIT
    assert len(entry["reply"]) == outline.REPLY_LIMIT


def test_pick_active_turn(outline):
    assert outline.pick_active_turn([]) is None
    assert outline.pick_active_turn(None) is None
    assert outline.pick_active_turn(
        [{"raw_idx": 1, "rel_top": -20}, {"raw_idx": 4, "rel_top": 10}, {"raw_idx": 8, "rel_top": 200}]
    ) == 4
    assert outline.pick_active_turn(
        [{"raw_idx": 2, "rel_top": 200}, {"raw_idx": 5, "rel_top": 400}]
    ) == 2
    assert outline.pick_active_turn(
        [{"raw_idx": 3, "rel_top": -1}, {"raw_idx": 9, "rel_top": 100}]
    ) == 9


def test_scroll_top_floors_at_zero(outline):
    assert outline.scroll_top_for_turn(10, 20, 56) == 0
    assert outline.scroll_top_for_turn(400, -80, 56) == 264
