"""Tests for foreign session import (G-17 / A3-5).

Sample JSONL fixtures follow the documented Claude Code / Codex CLI
storage shapes. Every case asserts the conversion contract: pure
user/assistant text, never fabricated tool_calls, no system payloads,
merged consecutive roles, leading-assistant user stub."""

import json

import pytest
from intellect_state import SessionDB

from agent.foreign_sessions import (
    detect_format,
    discover_foreign_sessions,
    import_foreign_session,
    parse_claude_code_jsonl,
    parse_codex_rollout,
)


@pytest.fixture
def db(tmp_path):
    return SessionDB(db_path=tmp_path / "state.db")


# ── detection ───────────────────────────────────────────────────────────

def test_detect_format_by_shape(tmp_path):
    cc = tmp_path / "x.jsonl"
    cc.write_text(json.dumps({"type": "user", "message": {"role": "user",
                                                        "content": "hi"}}) + "\n")
    codex_dir = tmp_path / "sessions" / "2026" / "09"
    codex_dir.mkdir(parents=True)
    codex = codex_dir / "rollout-2026-09-01.jsonl"
    codex.write_text(json.dumps({"type": "session_meta", "payload": {}}) + "\n")
    assert detect_format(cc) == "claude_code"
    assert detect_format(codex) == "codex"


# ── Claude Code parsing ─────────────────────────────────────────────────

def _claude_jsonl(path, lines):
    path.write_text(
        "".join(json.dumps(obj) + "\n" for obj in lines), encoding="utf-8"
    )


def test_claude_code_text_and_tool_summary(db, tmp_path):
    path = tmp_path / "s1.jsonl"
    _claude_jsonl(path, [
        {"type": "system", "message": {"role": "system", "content": "sys"}},
        {"type": "user", "message": {"role": "user", "content": "run tests"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "tool_use", "name": "terminal",
             "input": {"command": "pytest -q"}},
            {"type": "text", "text": "all green"},
        ]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "3 passed"},
            {"type": "text", "text": "looks good"},
        ]}},
    ])
    msgs = parse_claude_code_jsonl(path)
    # system never imported; tool_use/tool_result became bracketed summaries;
    # tool events are assistant-side text (never fabricated tool_calls)
    assert all(m["role"] in ("user", "assistant") for m in msgs)
    assert all("tool_calls" not in m for m in msgs)
    assert any("(used tool terminal" in m["content"] for m in msgs)
    assert any("(tool result: 3 passed)" in m["content"] for m in msgs)
    assert not any(m["content"] == "sys" for m in msgs)
    assert any("all green" in m["content"] for m in msgs)


def test_claude_code_thinking_and_system_skipped(tmp_path):
    path = tmp_path / "s2.jsonl"
    _claude_jsonl(path, [
        {"type": "summary", "summary": "ignore me"},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "secret reasoning"},
        ]}},
        {"type": "user", "message": {"role": "user", "content": "hello"}},
    ])
    msgs = parse_claude_code_jsonl(path)
    joined = "\n".join(m["content"] for m in msgs)
    assert "secret reasoning" not in joined
    assert "ignore me" not in joined
    assert any(m["content"] == "hello" for m in msgs)


# ── contract: merge + leading stub ──────────────────────────────────────

def test_consecutive_roles_merge_and_leading_assistant_gets_stub(tmp_path):
    path = tmp_path / "s3.jsonl"
    _claude_jsonl(path, [
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": "opening"}},
        {"type": "user", "message": {"role": "user", "content": "part one"}},
        {"type": "user", "message": {"role": "user", "content": "part two"}},
    ])
    msgs = parse_claude_code_jsonl(path)
    assert msgs[0]["role"] == "user"  # leading stub
    assert "imported session start" in msgs[0]["content"]
    assert msgs[1]["role"] == "assistant"
    # consecutive users merged into one turn
    assert msgs[2]["content"] == "part one\n\npart two"
    assert len(msgs) == 3
    path.unlink()


# ── Codex rollout parsing ───────────────────────────────────────────────

def _codex_jsonl(path, lines):
    path.write_text(
        "".join(json.dumps(obj) + "\n" for obj in lines), encoding="utf-8"
    )


def test_codex_rollout_messages_and_function_calls(tmp_path):
    path = tmp_path / "rollout-2026-09-01T00-00-00.jsonl"
    _codex_jsonl(path, [
        {"type": "session_meta", "payload": {"id": "x"}},
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "deploy it"}]}},
        {"type": "response_item", "payload": {
            "type": "function_call", "name": "terminal",
            "arguments": "{\"command\": \"deploy\"}"}},
        {"type": "response_item", "payload": {
            "type": "function_call_output", "output": "deployed ok"}},
        {"type": "response_item", "payload": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "done"}]}},
        {"type": "response_item", "payload": {
            "type": "reasoning", "summary": [{"type": "summary_text",
                                              "text": "hidden"}]}},
    ])
    msgs = parse_codex_rollout(path)
    assert all(m["role"] in ("user", "assistant") for m in msgs)
    assert any("(used tool terminal" in m["content"] for m in msgs)
    assert any("(tool result: deployed ok)" in m["content"] for m in msgs)
    assert any(m["content"] == "done" for m in msgs)
    joined = "\n".join(m["content"] for m in msgs)
    assert "hidden" not in joined


# ── import round-trip ───────────────────────────────────────────────────

def test_import_foreign_session_roundtrip(db, tmp_path):
    path = tmp_path / "s1.jsonl"
    _claude_jsonl(path, [
        {"type": "user", "message": {"role": "user",
                                     "content": "fix the login bug"}},
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": "on it"}},
    ])
    sid = import_foreign_session(db, path)
    assert sid
    assert db.resolve_session_by_title("Claude Code: fix the login bug") == sid
    msgs = db.get_messages(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    # idempotent re-import resolves to the same session id, no dupes
    sid2 = import_foreign_session(db, path)
    assert sid2 == sid
    assert len(db.get_messages(sid)) == 2


def test_import_rejects_unrecognized(db, tmp_path):
    path = tmp_path / "random.txt"
    path.write_text("not jsonl", encoding="utf-8")
    assert import_foreign_session(db, path) is None
