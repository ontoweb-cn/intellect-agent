"""ACP clarify bridging — the ``clarify`` tool over ACP elicitation.

The ``clarify`` tool lets the agent ask the user a question (multiple-choice
or open-ended). Delivery is platform-supplied via ``agent.clarify_callback``
(``callback(question, choices) -> str``). The ACP adapter previously supplied
no callback, so agents running under an editor integration could only time
out; this module bridges the tool to ACP's ``elicitation/create`` client
method, whose semantics — "request structured information from the user" —
map directly onto a clarifying question.

Choice mapping:

* ``clarify`` offers up to 4 choices; each becomes an ``enum`` value on a
  single string property, so the client renders a picker.
* Open-ended (no choices) sends a bare string property for free-form entry.
* The UI always appends an "Other" affordance, so a text answer that matches
  no enum value is accepted verbatim rather than rejected.

Decline / cancel / timeout all degrade to a sentinel string that tells the
agent no answer arrived, matching the gateway path's behaviour (the agent
adapts instead of hanging).
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable, Optional

from acp.schema import (
    ElicitationFormSessionMode,
    ElicitationSchema,
    ElicitationStringPropertySchema,
)

logger = logging.getLogger(__name__)

# Single property name used for the answer in every elicitation we send.
_ANSWER_FIELD = "answer"

# Default seconds to wait for the client to answer. Long enough for a human to
# read a question and type, but bounded so a client that ignores elicitation
# cannot wedge the agent thread forever.
DEFAULT_CLARIFY_TIMEOUT = 600.0


def _build_requested_schema(
    question: str,
    choices: Optional[list[str]],
) -> ElicitationSchema:
    """Build the form schema describing the clarify question.

    Choices become the ``enum`` of the answer property so a capable client
    renders picker affordances; without choices the property is free-form.
    """
    prop = ElicitationStringPropertySchema(
        type="string",
        title=question,
        enum=list(choices) if choices else None,
    )
    return ElicitationSchema(
        type="object",
        properties={_ANSWER_FIELD: prop},
        required=[_ANSWER_FIELD],
    )


def _extract_answer(response: Any) -> Optional[str]:
    """Pull the answer text out of an elicitation response.

    Returns ``None`` when the user declined, cancelled, or the client sent a
    response without usable content — the caller turns that into a sentinel.
    """
    action = getattr(response, "action", None)
    if action != "accept":
        # "decline" / "cancel" are both "no answer" from the agent's side.
        return None

    content = getattr(response, "content", None)
    if not isinstance(content, dict):
        return None

    value = content.get(_ANSWER_FIELD)
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        # Defensive: a client may echo a list for a single-select property.
        value = value[0] if value else None
        if value is None:
            return None
    text = str(value).strip()
    return text or None


def make_clarify_callback(
    create_elicitation_fn: Callable,
    loop: asyncio.AbstractEventLoop,
    session_id: str,
    timeout: float = DEFAULT_CLARIFY_TIMEOUT,
) -> Callable[[str, Optional[list]], str]:
    """Return an ``agent.clarify_callback`` that bridges to ACP elicitation.

    Args:
        create_elicitation_fn: The ACP connection's ``create_elicitation``
            coroutine.
        loop: Event loop the ACP connection lives on.
        session_id: Current ACP session id.
        timeout: Seconds to wait for an answer before degrading.
    """

    def _callback(question: str, choices: Optional[list]) -> str:
        from agent.async_utils import safe_schedule_threadsafe

        question_text = (question or "").strip()
        if not question_text:
            return "[clarify: empty question]"

        choice_list = [str(c) for c in choices] if choices else None
        requested_schema = _build_requested_schema(question_text, choice_list)
        mode = ElicitationFormSessionMode(
            session_id=session_id,
            requested_schema=requested_schema,
        )

        # ``create_elicitation`` is keyword-only for the mode payload; the SDK
        # spreads the mode's fields onto the request.
        coro = create_elicitation_fn(
            message=question_text,
            mode=mode,
        )
        future = safe_schedule_threadsafe(
            coro,
            loop,
            logger=logger,
            log_message="Clarify elicitation: failed to schedule on loop",
        )
        if future is None:
            return "[user did not respond: ACP client unavailable]"

        try:
            response = future.result(timeout=timeout)
        except FutureTimeout:
            future.cancel()
            logger.warning(
                "Clarify elicitation for session %s timed out after %ss",
                session_id,
                int(timeout),
            )
            return f"[user did not respond within {int(timeout / 60)}m]"
        except Exception as exc:
            logger.warning("Clarify elicitation failed: %s", exc)
            return f"[clarify unavailable: {exc}]"

        answer = _extract_answer(response)
        if answer is None:
            return "[user declined to answer]"
        return answer

    return _callback
