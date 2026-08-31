"""Tests for stall guards (G-01): identical-call breaker + continue-intent."""

import pytest

from agent.tool_guardrails import (
    STALL_GUARD_IDENTICAL_CALL_THRESHOLD,
    ToolCallGuardrailConfig,
    ToolCallGuardrailController,
    build_identical_result_stub,
    is_stall_guard_notice_exempt,
)


def make_controller(**kw) -> ToolCallGuardrailController:
    return ToolCallGuardrailController(
        ToolCallGuardrailConfig(hard_stop_enabled=False), **kw
    )


ARGS = {"path": "/tmp/x.py"}


def test_first_call_is_silent():
    c = make_controller()
    obs = c.observe_call("read_file", ARGS, "r" * 600, failed=False, tool_call_id="t1")
    assert not obs.has_content
    assert obs.streak_count == 1


def test_stub_on_second_identical_large_result():
    c = make_controller()
    c.observe_call("read_file", ARGS, "r" * 600, failed=False, tool_call_id="t1")
    obs = c.observe_call("read_file", ARGS, "r" * 600, failed=False, tool_call_id="t2")
    assert obs.stub
    assert "t1" in obs.stub and "read_file" in obs.stub
    assert obs.streak_count == 2


def test_notice_from_threshold_on_identical_calls():
    c = make_controller()
    for i in range(STALL_GUARD_IDENTICAL_CALL_THRESHOLD - 1):
        c.observe_call("web_search", {"q": "x"}, "same " * 20, failed=False)
    obs = c.observe_call("web_search", {"q": "x"}, "same " * 20, failed=False)
    assert obs.notice
    assert f"{STALL_GUARD_IDENTICAL_CALL_THRESHOLD}" in obs.notice
    assert "intellect note" in obs.notice


def test_small_results_never_stub_but_do_notice():
    c = make_controller()
    for _ in range(3):
        obs = c.observe_call("web_search", {"q": "x"}, "tiny", failed=False)
    assert obs.notice  # notice does not need size
    assert not obs.stub  # below 512 chars


def test_changed_args_resets_streak():
    c = make_controller()
    c.observe_call("read_file", ARGS, "r" * 600, failed=False, tool_call_id="t1")
    c.observe_call("read_file", {"path": "/tmp/other.py"}, "r" * 600, failed=False, tool_call_id="t2")
    obs = c.observe_call("read_file", ARGS, "r" * 600, failed=False, tool_call_id="t3")
    assert obs.streak_count == 1
    assert not obs.has_content


def test_changed_result_resets_streak():
    c = make_controller()
    c.observe_call("web_search", {"q": "x"}, "result-A " * 100, failed=False)
    c.observe_call("web_search", {"q": "x"}, "result-B " * 100, failed=False)
    obs = c.observe_call("web_search", {"q": "x"}, "result-B " * 100, failed=False)
    assert obs.streak_count == 2  # fresh streak on the new result


def test_failed_call_resets_streak_and_never_stubs():
    c = make_controller()
    c.observe_call("terminal", {"cmd": "make"}, "r" * 600, failed=False, tool_call_id="t1")
    obs = c.observe_call("terminal", {"cmd": "make"}, "r" * 600, failed=True, tool_call_id="t2")
    assert not obs.stub and not obs.notice
    assert obs.streak_count == 1  # failed call starts a fresh streak


def test_poller_notice_exempt_but_stub_applies():
    assert is_stall_guard_notice_exempt("process")
    assert is_stall_guard_notice_exempt("delegate_get_result")
    assert not is_stall_guard_notice_exempt("terminal")
    c = make_controller()
    for i in range(STALL_GUARD_IDENTICAL_CALL_THRESHOLD + 1):
        obs = c.observe_call(
            "process", {"id": "p1"}, "running " * 100, failed=False, tool_call_id=f"p{i}"
        )
    # 5 identical calls on an exempt poller: no notice, but stub from 2nd on.
    assert not obs.notice
    assert obs.stub  # stub is the point for pollers


def test_disable_gate_silences_everything():
    c = ToolCallGuardrailController(
        ToolCallGuardrailConfig(hard_stop_enabled=False), stall_guards_enabled=False
    )
    for _ in range(5):
        obs = c.observe_call("web_search", {"q": "x"}, "r" * 600, failed=False)
    assert not obs.has_content


def test_reset_for_turn_clears_streak():
    c = make_controller()
    c.observe_call("web_search", {"q": "x"}, "r" * 600, failed=False)
    c.observe_call("web_search", {"q": "x"}, "r" * 600, failed=False)
    c.reset_for_turn()
    obs = c.observe_call("web_search", {"q": "x"}, "r" * 600, failed=False)
    assert obs.streak_count == 1 and not obs.has_content


def test_stub_builder_content():
    stub = build_identical_result_stub("read_file", {"path": "/a"}, "call_9")
    assert "read_file" in stub and "call_9" in stub and "/a" in stub


def test_multimodal_non_string_result_is_ignored():
    c = make_controller()
    c.observe_call("vision_analyze", {"i": 1}, None, failed=False, tool_call_id="t1")
    obs = c.observe_call("vision_analyze", {"i": 1}, None, failed=False, tool_call_id="t2")
    assert not obs.has_content  # non-str results never join the streak


# ── continue-intent detection ──────────────────────────────────────────

from agent.conversation_loop import (  # noqa: E402
    CONTINUE_INTENT_MAX_NUDGES,
    _trailing_continue_intent,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Let me now run the tests to verify.", True),
        ("I'll now check the output.", True),
        ("Now I'll write the file.", True),
        ("Next, I'll update the docs.", True),
        ("Everything is done. All 12 tests pass.", False),
        ("Let me explain the architecture.", False),  # mid-text intent, tail is a statement
        ("", False),
        (None, False),
    ],
)
def test_trailing_continue_intent_detection(text, expected):
    assert _trailing_continue_intent(text) is expected


def test_trailing_intent_huge_text_ignored():
    assert not _trailing_continue_intent("Let me now run it. " * 500)


def test_nudge_budget_constant():
    assert CONTINUE_INTENT_MAX_NUDGES == 2
