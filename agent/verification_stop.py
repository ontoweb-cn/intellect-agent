"""Verify-on-stop prompt — appends evidence summary at turn end (HP-303g)."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _query_session_evidence(agent) -> list[dict[str, Any]]:
    """Fetch verification evidence for the current session. Fail-open."""
    try:
        from agent.verification_evidence import query_evidence
        from intellect_constants import get_intellect_home
        db_path = str(get_intellect_home() / "state.db")
        session_id = getattr(agent, "session_id", "") or ""
        if not session_id:
            return []
        return query_evidence(db_path, session_id=session_id, limit=10)
    except Exception:
        logger.debug("verification_stop: evidence query failed", exc_info=True)
        return []


def _format_evidence_block(records: list[dict[str, Any]]) -> str:
    """Format evidence records into a compact text block for the prompt."""
    if not records:
        return ""

    lines = [
        "",
        "── Verification Evidence (this session) ──",
    ]
    for i, r in enumerate(records[:5], 1):
        kind = r.get("kind", "unknown")
        cmd = r.get("command", "")[:60]
        exit_code = r.get("exit_code")
        passed = r.get("passed")
        summary = (r.get("output_summary", "") or "")[:120]

        status = "PASSED" if passed else ("FAILED" if passed is False else f"exit={exit_code}")
        lines.append(f"  {i}. [{kind}] {cmd}")
        lines.append(f"     → {status}")
        if summary:
            lines.append(f"     {summary}")

    displayed = records[:5]
    passed_count = sum(1 for r in displayed if r.get("passed"))
    total_displayed = len(displayed)
    total_all = len(records)
    lines.append(f"  Total: {passed_count}/{total_displayed} passed"
                 + (f" (of {total_all} recorded)" if total_all > total_displayed else ""))
    lines.append("── End Evidence ──")
    return "\n".join(lines)


def apply_verification_stop_prompt(
    agent,
    final_response: Optional[str],
    interrupted: bool,
) -> Optional[str]:
    """Append a verification evidence summary to the turn-end response.

    Only fires when:
    - The turn was not interrupted
    - There is evidence for this session
    - ``agent._verification_enabled()`` returns True
    """
    if interrupted or not final_response:
        return final_response

    if not agent._verification_enabled():
        return final_response

    records = _query_session_evidence(agent)
    if not records:
        return final_response

    block = _format_evidence_block(records)
    if not block:
        return final_response

    return final_response + "\n" + block
