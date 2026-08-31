"""Shared code-review subagent runner for the ``/review`` slash command.

A single implementation shared by the CLI, TUI (via the CLI slash worker), and
gateway (webui/desktop) so the review prompt and run/close lifecycle stay in one
place.  Each interface constructs its own :class:`AIAgent` (config resolution is
interface-specific) and delegates the actual run here.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def build_review_prompt(topic: str = "") -> str:
    """Return the code-review instructions for the subagent."""
    prompt = (
        "Perform a focused code review of the recent work in this project. "
        "Use your tools to inspect what changed: run `git status` and `git diff`, "
        "read the modified files, and run relevant tests. Report concrete findings "
        "— correctness bugs, security concerns, and improvement suggestions — with "
        "file/line references where possible. Be specific and actionable; skip "
        "trivial or generic observations."
    )
    if topic:
        prompt += f"\n\nFocus area requested by the user: {topic}"
    return prompt


def run_code_review(
    agent: Any,
    topic: str = "",
    *,
    parent_agent: Optional[Any] = None,
) -> str:
    """Run a code-review conversation on *agent* and return the final response.

    *agent* must already be constructed (each interface resolves its own model,
    provider, toolsets).  When *parent_agent* is provided, the review subagent
    inherits the parent's cached system prompt / session pins so it carries the
    parent's skills and produces a byte-identical system prompt (the same trick
    ``agent/background_review.py`` uses for its memory/skill fork).

    Returns the review text, or an ``Error: ...`` string on failure, or ``""``
    when nothing was generated.  Always closes *agent*.
    """
    prompt = build_review_prompt(topic)
    try:
        if parent_agent is not None:
            _pin_parent_context(agent, parent_agent)

        result = agent.run_conversation(user_message=prompt)
        response = result.get("final_response", "") if result else ""
        if not response and result and result.get("error"):
            response = f"Error: {result['error']}"
        return response or ""
    finally:
        try:
            agent.close()
        except Exception:
            logger.debug("code-review agent close failed", exc_info=True)


def _pin_parent_context(agent: Any, parent_agent: Any) -> None:
    """Carry the parent's cached system prompt to the review agent.

    Best-effort: pinning the byte-identical cached system prompt means the review
    subagent sees the same skills/system prompt as the parent (instead of
    rebuilding it with a fresh timestamp / narrower toolset).

    Deliberately NOT pinning ``session_id``: the review subagent shares the
    parent's ``session_db`` but keeps its own unique session id, so pinning the
    parent's id would flush the review transcript into the parent's session.
    """
    try:
        cached = getattr(parent_agent, "_cached_system_prompt", None)
        if cached:
            agent._cached_system_prompt = cached
    except Exception:
        logger.debug("code-review: failed to pin cached system prompt", exc_info=True)
