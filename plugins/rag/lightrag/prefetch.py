"""Prefetch policy helpers for LightRAG."""

from __future__ import annotations

from typing import Iterable, List


def should_prefetch(
    query: str,
    *,
    policy: str = "hybrid",
    min_chars: int = 40,
    keywords: Iterable[str] | None = None,
) -> bool:
    """Return True if this user message should trigger RAG prefetch."""
    if not query or not str(query).strip():
        return False
    policy = (policy or "hybrid").strip().lower()
    text = str(query).strip()
    if policy == "off":
        return False
    if policy == "always":
        return True
    kw_list: List[str] = list(keywords or [])
    lower = text.lower()
    if policy in ("intent", "hybrid"):
        for kw in kw_list:
            if kw and kw.lower() in lower:
                return True
    if policy == "intent":
        return False
    # hybrid: length or question mark
    if "?" in text or "？" in text:
        return True
    return len(text) >= max(1, int(min_chars or 40))
