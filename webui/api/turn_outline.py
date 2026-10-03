"""Question outline for the WebUI turn navigator.

Pure helpers mirrored by ``webui/static/turn-outline.js``.
``row_top_in_container`` is the row top minus the scrollport top
(a getBoundingClientRect delta), not ``offsetTop``.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

TITLE_LIMIT = 96
REPLY_LIMIT = 160
ACTIVE_THRESHOLD = 140
TURN_TOP_OFFSET = 56

_WS_V1 = re.compile(r"^\s*\[Workspace::v1:\s*(?:\\.|[^\]\\])+\]\s*")
_WS_LEGACY = re.compile(r"^\s*\[Workspace:[^\]]+\]\s*")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_PRESERVED = re.compile(
    r"^\s*\[your active task list was preserved across context compression\]",
    re.IGNORECASE,
)
_COMPACT_BRACKET = re.compile(r"^\s*\[context compaction", re.IGNORECASE)
_COMPACT_PLAIN = re.compile(r"^\s*context compaction", re.IGNORECASE)
_MARKS = re.compile(r"[`*#]")
_SPACE = re.compile(r"\s+")


def message_text(message: Mapping[str, Any] | None) -> str:
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content") or ""
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return "".join(parts).strip()
    return str(content).strip()


def strip_workspace_prefix(text: str) -> str:
    value = str(text or "")
    stripped, count = _WS_V1.subn("", value, count=1)
    if count:
        return stripped.strip()
    return _WS_LEGACY.sub("", value, count=1).strip()


def plain_preview(text: str, limit: int) -> str:
    preview = strip_workspace_prefix(text)
    preview = _LINK.sub(r"\1", preview)
    preview = _MARKS.sub("", preview)
    preview = _SPACE.sub(" ", preview).strip()
    if len(preview) <= limit:
        return preview
    return preview[:limit]


def attachment_name(message: Mapping[str, Any] | None) -> str:
    if not isinstance(message, Mapping):
        return ""
    attachments = message.get("attachments")
    if not isinstance(attachments, list) or not attachments:
        return ""
    first = attachments[0]
    if isinstance(first, str):
        label = first
    elif isinstance(first, Mapping):
        label = first.get("name") or first.get("filename") or first.get("path") or ""
    else:
        label = ""
    return str(label).replace("\\", "/").split("/")[-1].strip()


def is_preserved_task_list(message: Mapping[str, Any] | None) -> bool:
    if not isinstance(message, Mapping) or message.get("role") != "user":
        return False
    text = message_text(message) or str(message.get("content") or "")
    return _PRESERVED.match(text) is not None


def is_context_compaction(message: Mapping[str, Any] | None) -> bool:
    if not isinstance(message, Mapping) or not message.get("role") or message.get("role") == "tool":
        return False
    text = message_text(message) or str(message.get("content") or "")
    return _COMPACT_BRACKET.match(text) is not None or _COMPACT_PLAIN.match(text) is not None


def build_turn_outline(messages: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """One entry per in-memory user question. Fewer than three entries is still returned."""
    if not messages:
        return []
    users: list[tuple[int, str]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        if is_preserved_task_list(message) or is_context_compaction(message):
            continue
        title = plain_preview(message_text(message), TITLE_LIMIT)
        if not title:
            title = plain_preview(attachment_name(message), TITLE_LIMIT)
        if not title:
            continue
        users.append((index, title))
    entries: list[dict[str, Any]] = []
    total = len(messages)
    for position, (index, title) in enumerate(users):
        end = users[position + 1][0] if position + 1 < len(users) else total
        reply = ""
        for cursor in range(index + 1, end):
            candidate = messages[cursor]
            if not isinstance(candidate, Mapping) or candidate.get("role") != "assistant":
                continue
            if is_context_compaction(candidate):
                continue
            preview = plain_preview(message_text(candidate), REPLY_LIMIT)
            if preview:
                reply = preview
                break
        entries.append({"raw_idx": index, "title": title, "reply": reply})
    return entries


def _candidate_rel_top(item: Mapping[str, Any]) -> float:
    if "rel_top" in item:
        return float(item.get("rel_top") or 0)
    return float(item.get("relTop") or 0)


def _candidate_raw_idx(item: Mapping[str, Any]) -> int:
    if "raw_idx" in item:
        return int(item["raw_idx"])
    return int(item["rawIdx"])


def pick_active_turn(
    candidates: Sequence[Mapping[str, Any]] | None,
    threshold: int = ACTIVE_THRESHOLD,
) -> Optional[int]:
    """Last candidate at or above the line; otherwise the one closest below it."""
    if not candidates:
        return None
    passed = [item for item in candidates if _candidate_rel_top(item) <= threshold]
    if passed:
        return _candidate_raw_idx(passed[-1])
    best = min(candidates, key=_candidate_rel_top)
    return _candidate_raw_idx(best)


def scroll_top_for_turn(
    scroll_top: float,
    row_top_in_container: float,
    top_offset: float = TURN_TOP_OFFSET,
) -> float:
    """New scrollTop that places the row ``top_offset`` px below the scrollport top."""
    next_top = float(scroll_top or 0) + float(row_top_in_container or 0) - float(top_offset)
    return next_top if next_top > 0 else 0.0
