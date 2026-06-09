"""Conversation ingest helpers — summary via auxiliary LLM."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_SUMMARY_PROMPT = (
    "Summarize the following conversation exchange in 3-5 concise sentences "
    "suitable for a document knowledge base. Include key facts, decisions, "
    "and terminology. Do not include greetings or filler.\n\n"
)


def _call_summary_llm(body: str, *, max_tokens: int) -> str:
    try:
        from agent.auxiliary_client import call_llm

        response = call_llm(
            task="lightrag",
            messages=[{"role": "user", "content": _SUMMARY_PROMPT + body}],
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning("lightrag: summary LLM call failed: %s", exc)
        return ""

    choices = getattr(response, "choices", None) or []
    if not choices:
        logger.warning("lightrag: summary LLM returned no choices")
        return ""

    try:
        content = choices[0].message.content
    except (AttributeError, IndexError) as exc:
        logger.warning("lightrag: summary LLM response malformed: %s", exc)
        return ""

    if not isinstance(content, str):
        content = str(content) if content else ""
    return content.strip()


def summarize_text(text: str, *, max_tokens: int = 256) -> str:
    """Summarize an arbitrary text blob for knowledge-base ingest."""
    if not text.strip():
        return ""
    return _call_summary_llm(text.strip(), max_tokens=max_tokens)


def summarize_exchange(
    user_content: str,
    assistant_content: str,
    *,
    max_tokens: int = 256,
) -> str:
    """Generate a short summary via the auxiliary LLM chain."""
    if not user_content.strip() and not assistant_content.strip():
        return ""
    parts = []
    if user_content.strip():
        parts.append(f"User: {user_content.strip()}")
    if assistant_content.strip():
        parts.append(f"Assistant: {assistant_content.strip()}")
    return summarize_text("\n".join(parts), max_tokens=max_tokens)


def serialize_messages(messages: List[Dict[str, Any]]) -> str:
    out: List[str] = []
    for m in messages:
        role = m.get("role") or ""
        content = m.get("content")
        if isinstance(content, list):
            parts: List[str] = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    parts.append(str(c.get("text") or ""))
            content = "\n".join(parts)
        if not content:
            continue
        out.append(f"{role}: {content}")
    return "\n".join(out)
