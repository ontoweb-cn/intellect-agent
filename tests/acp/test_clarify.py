"""Tests for acp_adapter.clarify — the ''clarify'' tool over ACP elicitation.

The bridge turns ``agent.clarify_callback(question, choices)`` into an ACP
``elicitation/create`` request:

* multiple-choice questions become an ``enum`` string property so the client
  can render a picker;
* open-ended questions send a bare string property;
* the accepted answer is returned verbatim, because the UI also offers an
  "Other" affordance whose value need not match any enum entry;
* decline / cancel / timeout degrade to a sentinel so the agent adapts
  instead of hanging on a client that ignores elicitation.
"""

import asyncio
import inspect
from concurrent.futures import Future
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acp.schema import (
    AcceptElicitationResponse,
    CancelElicitationResponse,
    DeclineElicitationResponse,
)

from acp_adapter.clarify import (
    DEFAULT_CLARIFY_TIMEOUT,
    _extract_answer,
    make_clarify_callback,
)


def _invoke_callback(
    response,
    *,
    question="Which approach?",
    choices=("A", "B"),
    timeout=DEFAULT_CLARIFY_TIMEOUT,
    schedule=None,
    result_error=None,
):
    """Drive the clarify callback with a stubbed elicitation request."""
    loop = MagicMock(spec=asyncio.AbstractEventLoop)
    create_elicitation = AsyncMock(name="create_elicitation")
    future = MagicMock(spec=Future)
    if result_error is not None:
        future.result.side_effect = result_error
    else:
        future.result.return_value = response

    scheduled = {}

    def _schedule(coro, passed_loop):
        scheduled["coro"] = coro
        scheduled["loop"] = passed_loop
        return future

    with patch(
        "agent.async_utils.asyncio.run_coroutine_threadsafe",
        side_effect=schedule or _schedule,
    ):
        cb = make_clarify_callback(
            create_elicitation, loop, session_id="s1", timeout=timeout
        )
        result = cb(question, list(choices) if choices is not None else None)

    if "coro" in scheduled and inspect.iscoroutine(scheduled["coro"]):
        scheduled["coro"].close()
    return result, create_elicitation, scheduled, loop, future


def _accept(answer):
    return AcceptElicitationResponse(action="accept", content={"answer": answer})


class TestClarifyBridge:
    def test_schedules_elicitation_on_the_given_loop(self):
        result, create_elicitation, scheduled, loop, _ = _invoke_callback(_accept("B"))

        assert result == "B"
        assert scheduled["loop"] is loop
        assert inspect.iscoroutine(scheduled["coro"])

    def test_sends_choices_as_enum_for_picker_rendering(self):
        _, create_elicitation, _, _, _ = _invoke_callback(_accept("A"))

        _, kwargs = create_elicitation.call_args
        assert kwargs["message"] == "Which approach?"
        schema = kwargs["mode"].requested_schema
        assert schema.type == "object"
        assert schema.required == ["answer"]
        prop = schema.properties["answer"]
        assert prop.type == "string"
        assert prop.enum == ["A", "B"]
        assert prop.title == "Which approach?"

    def test_open_ended_has_no_enum(self):
        _, create_elicitation, _, _, _ = _invoke_callback(
            _accept("free text"), choices=None
        )

        _, kwargs = create_elicitation.call_args
        prop = kwargs["mode"].requested_schema.properties["answer"]
        assert prop.enum is None

    def test_accepts_answer_outside_the_enum(self):
        """The UI offers an "Other" affordance, so free text must pass through."""
        result, _, _, _, _ = _invoke_callback(_accept("something custom"))
        assert result == "something custom"

    def test_answer_is_stripped(self):
        result, _, _, _, _ = _invoke_callback(_accept("  padded  "))
        assert result == "padded"

    def test_session_id_is_propagated(self):
        _, create_elicitation, _, _, _ = _invoke_callback(_accept("A"))
        _, kwargs = create_elicitation.call_args
        assert kwargs["mode"].session_id == "s1"

    def test_decline_returns_sentinel(self):
        result, _, _, _, _ = _invoke_callback(
            DeclineElicitationResponse(action="decline")
        )
        assert result == "[user declined to answer]"

    def test_cancel_returns_sentinel(self):
        result, _, _, _, _ = _invoke_callback(
            CancelElicitationResponse(action="cancel")
        )
        assert result == "[user declined to answer]"

    def test_accept_without_content_returns_sentinel(self):
        result, _, _, _, _ = _invoke_callback(
            AcceptElicitationResponse(action="accept", content=None)
        )
        assert result == "[user declined to answer]"

    def test_timeout_returns_bounded_sentinel(self):
        from concurrent.futures import TimeoutError as FutureTimeout

        result, _, _, _, future = _invoke_callback(
            None, timeout=120.0, result_error=FutureTimeout()
        )
        assert result == "[user did not respond within 2m]"
        future.cancel.assert_called_once()

    def test_schedule_failure_degrades_without_hanging(self):
        result, _, _, _, _ = _invoke_callback(_accept("A"), schedule=lambda *a, **k: None)
        assert result.startswith("[user did not respond")

    def test_unexpected_error_degrades(self):
        result, _, _, _, _ = _invoke_callback(None, result_error=RuntimeError("boom"))
        assert result.startswith("[clarify unavailable")

    def test_empty_question_short_circuits(self):
        result, create_elicitation, _, _, _ = _invoke_callback(_accept("A"), question="   ")
        assert result == "[clarify: empty question]"
        create_elicitation.assert_not_called()


class TestExtractAnswer:
    def test_accept_returns_text(self):
        assert _extract_answer(_accept("hello")) == "hello"

    def test_list_value_takes_first_entry(self):
        resp = AcceptElicitationResponse(action="accept", content={"answer": ["only"]})
        assert _extract_answer(resp) == "only"

    def test_empty_list_returns_none(self):
        resp = AcceptElicitationResponse(action="accept", content={"answer": []})
        assert _extract_answer(resp) is None

    def test_whitespace_only_returns_none(self):
        assert _extract_answer(_accept("   ")) is None

    def test_missing_field_returns_none(self):
        resp = AcceptElicitationResponse(action="accept", content={"other": "x"})
        assert _extract_answer(resp) is None

    def test_non_dict_content_returns_none(self):
        resp = MagicMock()
        resp.action = "accept"
        resp.content = "not-a-dict"
        assert _extract_answer(resp) is None


def test_acp_toolset_includes_clarify():
    """The tool must be exposed or the callback is unreachable."""
    from toolsets import resolve_toolset

    assert "clarify" in resolve_toolset("intellect-acp")


class TestRealSdkWirePath:
    """Drive the bridge through the SDK's own serializers.

    The unit tests above construct response objects directly. These go through
    the adapter the SDK uses on the wire, so they also pin the request payload
    shape a client actually receives and the response shape it sends back.
    """

    def test_request_serializes_to_valid_client_payload(self):
        import acp.agent.connection as ac

        _, create_elicitation, _, _, _ = _invoke_callback(_accept("A"))
        _, kwargs = create_elicitation.call_args

        # Rebuild the same request the bridge sends and run the SDK's own
        # serializer — this is the JSON a real client would receive.
        request = ac._create_elicitation_request(
            kwargs["message"], kwargs["mode"], None
        )
        wire = ac.serialize_params(request)

        assert wire["sessionId"] == "s1"
        assert wire["message"] == "Which approach?"
        assert wire["mode"] == "form"
        assert wire["requestedSchema"]["required"] == ["answer"]
        assert wire["requestedSchema"]["properties"]["answer"]["enum"] == ["A", "B"]

    @pytest.mark.parametrize(
        "payload, expected",
        [
            ({"action": "accept", "content": {"answer": "B"}}, "B"),
            ({"action": "accept", "content": {"answer": "free text"}}, "free text"),
            ({"action": "accept"}, None),
            ({"action": "decline"}, None),
            ({"action": "cancel"}, None),
        ],
    )
    def test_wire_response_parses_to_expected_answer(self, payload, expected):
        import acp.agent.connection as ac

        # Exactly how the SDK deserializes a client's raw JSON reply.
        response = ac._CREATE_ELICITATION_RESPONSE_ADAPTER.validate_python(payload)
        assert _extract_answer(response) == expected
