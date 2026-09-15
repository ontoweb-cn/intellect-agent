"""ACP stop_reason must reflect how the agent turn actually ended.

The adapter used to answer every non-cancelled prompt with ``end_turn``, so a
truncated or crashed run reached the client as a complete answer. These tests
pin the mapping against the agent's own ``completed`` / ``partial`` /
``failed`` / ``error`` fields, and against the real ``prompt()`` path so a
future refactor cannot quietly unhook it again.
"""

from __future__ import annotations

import pytest
from acp.schema import TextContentBlock

from acp_adapter.server import _stop_reason_for_result


# ---------------------------------------------------------------------------
# The pure mapping
# ---------------------------------------------------------------------------


def test_completed_run_is_end_turn() -> None:
    assert (
        _stop_reason_for_result({"final_response": "hi", "completed": True})
        == "end_turn"
    )


def test_missing_completed_key_defaults_to_end_turn() -> None:
    """Older/partial result dicts predate the flag; absence is not failure."""
    assert _stop_reason_for_result({"final_response": "hi"}) == "end_turn"


def test_truncated_run_maps_to_max_tokens() -> None:
    """The output ceiling: the reply is a prefix, not an answer.

    This is the shape ``conversation_loop`` returns when the model hits the
    token limit and a truncated tool call cannot be retried.
    """
    result = {
        "final_response": None,
        "completed": False,
        "partial": True,
        "error": "Response truncated due to output length limit",
    }
    assert _stop_reason_for_result(result) == "max_tokens"


def test_first_response_truncation_also_maps_to_max_tokens() -> None:
    result = {
        "final_response": "partial text",
        "completed": False,
        "partial": True,
        "error": "First response truncated due to output length limit",
    }
    assert _stop_reason_for_result(result) == "max_tokens"


def test_iteration_budget_maps_to_max_turn_requests() -> None:
    """Ran out of turns rather than failing — a distinct ACP value."""
    result = {
        "final_response": "summary",
        "completed": False,
        "turn_exit_reason": "max_iterations_reached(90/90)",
    }
    assert _stop_reason_for_result(result) == "max_turn_requests"


def test_budget_exhausted_message_also_maps_to_max_turn_requests() -> None:
    result = {
        "final_response": "",
        "completed": False,
        "error": "Iteration budget exhausted (90/90)",
    }
    assert _stop_reason_for_result(result) == "max_turn_requests"


def test_reasoning_budget_exhausted_maps_to_max_tokens() -> None:
    """A token ceiling whose message never says "truncated".

    ``conversation_loop`` returns this when reasoning ate the whole output
    budget. Keying off the word "truncat" (as the first version did) left it
    labelled a refusal, which reads to the user as "the agent declined".
    """
    result = {
        "final_response": "⚠️ Thinking Budget Exhausted ...",
        "completed": False,
        "partial": True,
        "error": (
            "Model used all output tokens on reasoning with none left "
            "for the response. Try lowering reasoning effort or "
            "increasing max_tokens."
        ),
    }
    assert _stop_reason_for_result(result) == "max_tokens"


@pytest.mark.parametrize(
    ("label", "result"),
    [
        (
            "scratchpad never completed",
            {
                "final_response": None,
                "completed": False,
                "partial": True,
                "error": "Incomplete REASONING_SCRATCHPAD after 2 retries",
            },
        ),
        (
            "codex stayed incomplete",
            {
                "final_response": None,
                "completed": False,
                "partial": True,
                "error": "Codex response remained incomplete after 3 continuation attempts",
            },
        ),
        (
            "context window refused to compress",
            {
                "final_response": None,
                "completed": False,
                "partial": True,
                "error": "Context length exceeded (200,000 tokens). Cannot compress further.",
            },
        ),
        (
            "payload too large",
            {
                "final_response": None,
                "completed": False,
                "partial": True,
                "error": "Request payload too large (413). Cannot compress further.",
            },
        ),
    ],
)
def test_partial_without_the_word_truncated_is_still_a_ceiling(
    label: str, result: dict
) -> None:
    """``partial`` is the signal, not the error prose.

    Every one of these stopped at a generation ceiling and streamed a
    prefix; none of their messages contains "truncat", so a text match would
    have mislabelled them all as refusals.
    """
    assert _stop_reason_for_result(result) == "max_tokens", label


@pytest.mark.parametrize(
    ("label", "result"),
    [
        (
            "provider failure",
            {
                "final_response": "",
                "completed": False,
                "failed": True,
                "error": "API down",
            },
        ),
        (
            "content policy",
            {"final_response": "", "completed": False, "failed": True},
        ),
        (
            "incomplete without a flag",
            {"final_response": "half", "completed": False},
        ),
        (
            "compression failed outright",
            {
                "final_response": "",
                "completed": False,
                "failed": True,
                "partial": True,
                "error": "Context length exceeded. Cannot compress further.",
            },
        ),
    ],
)
def test_other_incomplete_outcomes_map_to_refusal(label: str, result: dict) -> None:
    """Anything else that did not complete must not read as a clean finish.

    ``end_turn`` is the one value that tells the client "this reply is the
    answer", which is exactly what must not be said here.
    """
    assert _stop_reason_for_result(result) == "refusal", label


def test_a_failed_partial_stays_a_failure() -> None:
    """``failed`` outranks ``partial``: a broken run is not a soft ceiling.

    The context/payload-overflow paths set both flags. Reporting them as
    ``max_tokens`` would tell the user to retry something that cannot fit.
    """
    result = {
        "final_response": "",
        "completed": False,
        "partial": True,
        "failed": True,
        "error": "Context length exceeded. Cannot compress further.",
    }
    assert _stop_reason_for_result(result) == "refusal"


def test_a_failed_partial_that_also_says_truncated_is_still_a_failure() -> None:
    """Even a "truncated" message does not soften a ``failed`` run.

    ``failed`` is the stronger signal: the run broke, so the client should
    not be told it merely ran long. (No current return site sets both
    ``failed`` and a truncation message; this pins the precedence rule.)
    """
    result = {
        "final_response": None,
        "completed": False,
        "partial": True,
        "failed": True,
        "error": "Response truncated due to output length limit",
    }
    assert _stop_reason_for_result(result) == "refusal"


# ---------------------------------------------------------------------------
# Wired through prompt()
# ---------------------------------------------------------------------------


def _agent_for(result: dict):
    """The shared fixture builder, reduced to the two values these tests need."""
    from conftest import make_agent_and_state

    agent, state, _fake, _conn = make_agent_and_state(result)
    return agent, state


@pytest.mark.asyncio
async def test_prompt_reports_max_tokens_for_a_truncated_run() -> None:
    """End to end through ``prompt()``: the bug this change fixes.

    Before, this returned ``end_turn`` and the client rendered the partial
    reply as final.
    """
    agent, state = _agent_for({
        "final_response": None,
        "completed": False,
        "partial": True,
        "error": "Response truncated due to output length limit",
    })

    response = await agent.prompt(
        session_id=state.session_id,
        prompt=[TextContentBlock(type="text", text="do the thing")],
    )

    assert response.stop_reason == "max_tokens"


@pytest.mark.asyncio
async def test_prompt_reports_end_turn_for_a_completed_run() -> None:
    agent, state = _agent_for({"final_response": "done", "completed": True})

    response = await agent.prompt(
        session_id=state.session_id,
        prompt=[TextContentBlock(type="text", text="do the thing")],
    )

    assert response.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_cancelled_event_outranks_the_run_outcome() -> None:
    """A client-requested stop is authoritative over the result's own flags.

    The flag has to be set *while* the run is in flight: ``prompt()`` clears
    a stale event up front, so setting it before the call would be wiped. The
    fake agent raises the cancel from inside ``run_conversation``, which is
    where a real ``session/cancel`` lands.
    """
    cancelled_result = {
        "final_response": None,
        "completed": False,
        "partial": True,
        "error": "Response truncated due to output length limit",
    }
    agent, state = _agent_for(cancelled_result)
    fake = agent.session_manager.get_session(state.session_id).agent

    original = fake.run_conversation

    def _cancel_then_run(**kwargs):
        state.cancel_event.set()
        return original(**kwargs)

    fake.run_conversation = _cancel_then_run

    response = await agent.prompt(
        session_id=state.session_id,
        prompt=[TextContentBlock(type="text", text="do the thing")],
    )

    assert response.stop_reason == "cancelled"
