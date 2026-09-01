"""Dirty-history sanitation end-to-end tests (G-07 / A2-2 closeout).

A2-2's pairing cleanup was delivered as ``sanitize_api_messages`` (wired
pre-call in ``conversation_loop.py`` and the compression-summary path) plus
``drop_thinking_only_and_merge_users``. These six cases inject dirty
histories in the shape each origin produces — missing results, duplicate
ids, empty assistant carriers, provider id variants, session-resume debris,
post-compression debris — and assert the provider-schema pairing invariant
on the cleaned wire copy:

* every ``tool`` result pairs (variant-aware) with a surviving call;
* every ``tool_call`` has at least one result;
* result ids are unique;
* no invalid roles, no empty assistant carriers.
"""

import pytest

import agent.agent_runtime_helpers as arh
from agent.tool_call_id import result_matches_any


STUB_TEXT = "Result unavailable"


def _make_agent_stub():
    """sanitize_api_messages / drop_thinking_only read AIAgent statics via
    _ra(); stub them with faithful minimal ports (real values mirrored from
    run_agent.py)."""

    class _Static:
        _VALID_API_ROLES = frozenset(
            {"system", "user", "assistant", "tool", "function", "developer"}
        )

        @staticmethod
        def _get_tool_call_id_static(tc):
            if isinstance(tc, dict):
                return tc.get("call_id", "") or tc.get("id", "") or ""
            return getattr(tc, "call_id", "") or getattr(tc, "id", "") or ""

        @staticmethod
        def _get_tool_call_name_static(tc):
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                return fn.get("name") or tc.get("name") or ""
            return ""

        @staticmethod
        def _is_thinking_only_assistant(msg):
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                return False
            if msg.get("tool_calls"):
                return False
            content = msg.get("content")
            if isinstance(content, str):
                return not content.strip()
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        return bool(block)
                    btype = block.get("type")
                    if btype in ("thinking", "redacted_thinking"):
                        continue
                    if btype == "text":
                        text = block.get("text", "")
                        if isinstance(text, str) and text.strip():
                            return False
                        continue
                    return False  # tool_use / image / … — real payload
                return True
            return True  # None / missing content, no tool_calls

        logger = __import__("logging").getLogger("test")

    class _RA:
        AIAgent = _Static
        logger = __import__("logging").getLogger("test")

    return _RA


@pytest.fixture(autouse=True)
def _ra_stub(monkeypatch):
    monkeypatch.setattr(arh, "_ra", lambda: _make_agent_stub())


def _call(cid, name="terminal"):
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


def _assistant(cid, name="terminal"):
    return {"role": "assistant", "content": "", "tool_calls": [_call(cid, name)]}


def _result(cid, content="ok"):
    return {"role": "tool", "tool_call_id": cid, "content": content}


def _assert_pairing_invariant(messages):
    """The provider-schema contract the cleaner must restore."""
    for msg in messages:
        assert msg.get("role") in {"system", "user", "assistant", "tool"}, (
            f"invalid role survived: {msg.get('role')!r}"
        )
        if msg.get("role") == "assistant" and not msg.get("tool_calls"):
            content = msg.get("content")
            assert content, "empty assistant carrier survived"

    call_ids = []
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            cid = tc.get("id", "") or tc.get("call_id", "")
            if cid:
                call_ids.append(cid)

    result_ids = [
        m.get("tool_call_id") for m in messages if m.get("role") == "tool"
    ]
    # unique result ids — exact comparison (ids on the wire as stored)
    assert len(result_ids) == len(set(result_ids)), (
        f"duplicate result ids survived: {result_ids}"
    )
    for rid in result_ids:
        assert result_matches_any(rid, call_ids), (
            f"orphan result {rid!r} survived (calls={call_ids})"
        )
    for cid in call_ids:
        assert result_matches_any(cid, result_ids), (
            f"call {cid!r} has no result (results={result_ids})"
        )


# ── case 1 · 缺 tool result ────────────────────────────────────────────

def test_missing_result_gets_stub():
    messages = [
        {"role": "user", "content": "run it"},
        _assistant("call_1"),
        # result lost (crash between call and result)
    ]
    out = arh.sanitize_api_messages(messages)
    _assert_pairing_invariant(out)
    stubs = [m for m in out if m.get("role") == "tool"]
    assert len(stubs) == 1
    assert stubs[0]["tool_call_id"] == "call_1"
    assert STUB_TEXT in stubs[0]["content"]


# ── case 2 · 重复 id ───────────────────────────────────────────────────

def test_duplicate_result_ids_keep_first():
    messages = [
        {"role": "user", "content": "run it"},
        _assistant("call_1"),
        _result("call_1", "first"),
        _result("call_1", "second"),  # provider-rejected duplicate
    ]
    out = arh.sanitize_api_messages(messages)
    _assert_pairing_invariant(out)
    results = [m for m in out if m.get("role") == "tool"]
    assert len(results) == 1
    assert results[0]["content"] == "first"  # first occurrence wins
    assert STUB_TEXT not in "".join(r["content"] for r in results)


def test_variant_duplicate_results_dedup():
    """The same logical result delivered under two id variants is still a
    duplicate — the later one is dropped, the earlier survives."""
    messages = [
        {"role": "user", "content": "run it"},
        _assistant("call_ABC123"),
        _result("abc123", "real"),  # variant of call_ABC123
        _result("call_abc123", "echo"),  # second variant = duplicate
    ]
    out = arh.sanitize_api_messages(messages)
    _assert_pairing_invariant(out)
    results = [m for m in out if m.get("role") == "tool"]
    assert len(results) == 1
    assert results[0]["content"] == "real"


# ── case 3 · 空 assistant carrier ──────────────────────────────────────

def test_empty_assistant_carrier_dropped_and_users_merged():
    messages = [
        {"role": "user", "content": "before"},
        {"role": "assistant", "content": ""},  # empty carrier — Anthropic 400s
        {"role": "user", "content": "after"},
    ]
    out = arh.drop_thinking_only_and_merge_users(
        arh.sanitize_api_messages(messages)
    )
    _assert_pairing_invariant(out)
    users = [m for m in out if m.get("role") == "user"]
    assert len(users) == 1
    assert users[0]["content"] == "before\n\nafter"
    assert not [m for m in out if m.get("role") == "assistant"]


# ── case 4 · 变体 id 配对 ──────────────────────────────────────────────

def test_variant_ids_pair_across_history():
    messages = [
        {"role": "user", "content": "run it"},
        _assistant("call_ABC123"),
        # provider stripped prefix + flipped case on the result side
        _result("abc123", "exit 0"),
    ]
    out = arh.sanitize_api_messages(messages)
    _assert_pairing_invariant(out)
    results = [m for m in out if m.get("role") == "tool"]
    assert len(results) == 1
    assert results[0]["content"] == "exit 0"  # the REAL result survives
    assert STUB_TEXT not in results[0]["content"]


# ── case 5 · resume 后脏历史 ───────────────────────────────────────────

def test_resume_debris_cleaned():
    messages = [
        {"role": "user", "content": "hi"},
        # session-load debris: a role the API won't accept
        {"role": "meta", "content": "session metadata"},
        {"role": "assistant", "content": "done"},
        # result for a call that was never in the restored history
        _result("call_ghost", "ghost"),
    ]
    out = arh.sanitize_api_messages(messages)
    _assert_pairing_invariant(out)
    assert not [m for m in out if m.get("role") == "meta"]
    assert not [m for m in out if m.get("role") == "tool"]
    # the healthy core survives untouched
    assert [
        (m["role"], m.get("content")) for m in out
    ] == [
        ("user", "hi"),
        ("assistant", "done"),
    ]


# ── case 6 · 压缩后脏历史 ──────────────────────────────────────────────

def test_post_compression_debris_cleaned():
    # Post-summary shape: one call of a two-call batch lost its result to
    # the summarizer, a pre-summary orphan result survived, and a
    # thinking-only assistant turn was left behind.
    messages = [
        {"role": "user", "content": "context summary of earlier turns"},
        {"role": "assistant", "content": "Continuing from the summary"},
        _assistant("call_keep"),
        _result("call_keep", "kept output"),
        _result("call_summarized_away", "orphan"),  # parent call summarized
        {
            "role": "assistant",
            "content": [{"type": "thinking", "thinking": "hmm"}],
        },
        _assistant("call_lost"),
        # call_lost's result never made it through the boundary
    ]
    out = arh.drop_thinking_only_and_merge_users(
        arh.sanitize_api_messages(messages)
    )
    _assert_pairing_invariant(out)
    tool_contents = {
        m["tool_call_id"]: m["content"] for m in out if m.get("role") == "tool"
    }
    assert tool_contents["call_keep"] == "kept output"
    assert "call_summarized_away" not in tool_contents  # orphan dropped
    assert STUB_TEXT in tool_contents["call_lost"]  # stub injected
    # thinking-only assistant turn dropped; the placeholder carrier (real
    # text) survives
    assert any(
        m.get("role") == "assistant"
        and m.get("content") == "Continuing from the summary"
        for m in out
    )
    assert not [
        m for m in out
        if m.get("role") == "assistant"
        and isinstance(m.get("content"), list)
    ]


# ── cleaner must be idempotent (dirty in, clean out, clean stays) ──────

def test_sanitizer_is_idempotent():
    dirty = [
        {"role": "user", "content": "run it"},
        _assistant("call_1"),
        _result("call_1", "first"),
        _result("call_1", "second"),
        _result("call_ghost", "ghost"),
        {"role": "meta", "content": "debris"},
    ]
    once = arh.sanitize_api_messages(dirty)
    twice = arh.sanitize_api_messages(once)
    assert twice == once
    _assert_pairing_invariant(twice)
