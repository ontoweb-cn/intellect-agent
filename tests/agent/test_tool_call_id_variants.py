"""Tests for tool_call_id variant matching (G-08 / A1-5)."""

from agent.tool_call_id import (
    canonical_tool_call_id,
    find_matching_call_id,
    ids_match,
    result_matches_any,
)


def test_exact_match_fast_path():
    assert ids_match("call_abc", "call_abc")


def test_provider_prefix_variants_match():
    assert ids_match("call_abc123", "abc123")
    assert ids_match("toolu_abc123", "call_abc123")
    assert ids_match("fc_abc123", "call_abc123")
    assert ids_match("functions.abc123", "call_abc123")
    assert ids_match("srvtoolu_abc123", "toolu_abc123")


def test_case_flip_matches():
    assert ids_match("call_ABC123", "call_abc123")


def test_uuid_under_different_wrappers():
    u = "3f2a9c8e-1b4d-4e5f-8a7b-9c0d1e2f3a4b"
    assert ids_match(f"call_{u}", f"toolu_{u.replace('-', '')}")


def test_empty_ids_never_match():
    assert not ids_match("", "call_x")
    assert not ids_match("call_x", "")
    assert not ids_match(None, None)


def test_genuinely_different_ids_do_not_match():
    assert not ids_match("call_111", "call_222")
    assert not ids_match("call_aaa", "call_bbb")


def test_canonical_form_stable():
    assert canonical_tool_call_id("CALL_ABC") == canonical_tool_call_id("call_abc")
    assert canonical_tool_call_id("") == ""
    assert canonical_tool_call_id(None) == ""


def test_result_matches_any():
    assert result_matches_any("abc123", ["call_xyz", "call_abc123"])
    assert result_matches_any("call_abc123", ["abc123"])
    assert not result_matches_any("zzz", ["call_abc123"])
    assert not result_matches_any("", ["call_abc123"])


def test_find_matching_call_id_returns_original():
    calls = ["call_xyz", "call_abc123"]
    assert find_matching_call_id("abc123", calls) == "call_abc123"
    assert find_matching_call_id("nope", calls) == ""


# ── sanitize_api_messages integration ─────────────────────────────────

def _make_agent_stub():
    """sanitize_api_messages reads AIAgent statics via _ra(); stub them."""

    class _Static:
        _VALID_API_ROLES = {"system", "user", "assistant", "tool"}

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

        logger = __import__("logging").getLogger("test")

    class _RA:
        AIAgent = _Static
        logger = __import__("logging").getLogger("test")


    return _RA


def test_sanitize_keeps_variant_paired_result(monkeypatch):
    """A result whose id differs from the call id only by prefix/case must
    survive (old code dropped it as an orphan AND stubbed the call)."""
    import agent.agent_runtime_helpers as arh

    ra_stub = _make_agent_stub()
    monkeypatch.setattr(arh, "_ra", lambda: ra_stub)

    messages = [
        {"role": "user", "content": "run it"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_ABC123",
                "type": "function",
                "function": {"name": "terminal", "arguments": "{}"},
            }],
        },
        # Result side: provider stripped the prefix and lowercased.
        {"role": "tool", "tool_call_id": "abc123", "content": "exit 0"},
    ]
    out = arh.sanitize_api_messages(messages)
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == "exit 0"  # the REAL result survived
    assert "Result unavailable" not in tool_msgs[0]["content"]


def test_sanitize_stubs_truly_missing_result(monkeypatch):
    import agent.agent_runtime_helpers as arh

    ra_stub = _make_agent_stub()
    monkeypatch.setattr(arh, "_ra", lambda: ra_stub)

    messages = [
        {"role": "user", "content": "run it"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "terminal", "arguments": "{}"},
            }],
        },
        # No tool result at all.
    ]
    out = arh.sanitize_api_messages(messages)
    stubs = [m for m in out if m.get("role") == "tool"]
    assert len(stubs) == 1
    assert stubs[0]["tool_call_id"] == "call_1"  # original call-side id
    assert "Result unavailable" in stubs[0]["content"]


def test_sanitize_still_drops_true_orphans(monkeypatch):
    import agent.agent_runtime_helpers as arh

    ra_stub = _make_agent_stub()
    monkeypatch.setattr(arh, "_ra", lambda: ra_stub)

    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "done"},
        # Result for a call that does not exist anywhere in history.
        {"role": "tool", "tool_call_id": "call_ghost", "content": "ghost"},
    ]
    out = arh.sanitize_api_messages(messages)
    assert not [m for m in out if m.get("role") == "tool"]
