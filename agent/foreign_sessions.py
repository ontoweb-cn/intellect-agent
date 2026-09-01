"""Foreign session import (G-17 / A3-5): Claude Code & Codex CLI JSONL →
SessionDB.

Conversion contract (deep-dive §9 — non-negotiable):

- only pure user/assistant TEXT is produced;
- tool history is NEVER fabricated into tool_calls — each tool event
  becomes a bracketed one-line text summary in the owning turn;
- ``system`` payloads and lines are never imported;
- consecutive same-role messages merge (joined with a blank line);
- a leading assistant turn gets a single user stub in front of it.

Writes go through the SessionDB write surface only: ``ensure_session`` +
``append_message`` + ``set_session_title``. intellect's session schema has
no ``origin.imported_from`` column (Hermes lineage), so provenance rides on
``source="imported"`` and a ``"<Source>: …"`` title prefix.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SOURCE_IMPORT = "imported"
_MAX_TOOL_SUMMARY = 200
_LEADING_USER_STUB = "(imported session start)"

_FORMATS = ("claude_code", "codex")


# ── detection ───────────────────────────────────────────────────────────

def detect_format(path: Path) -> Optional[str]:
    """Detect the foreign format from path shape and filename."""
    p = Path(path)
    name = p.name
    if name.startswith("rollout-") and name.endswith(".jsonl"):
        return "codex"
    parts = {seg.lower() for seg in p.parts}
    if ".claude" in parts and "projects" in parts and name.endswith(".jsonl"):
        return "claude_code"
    if name.endswith(".jsonl"):
        # Content sniff: first parseable line decides.
        try:
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    t = str(obj.get("type") or "")
                    if t.startswith("rollout") or t == "session_meta":
                        return "codex"
                    if t in {"user", "assistant", "summary"}:
                        return "claude_code"
        except OSError:
            return None
        return "claude_code"
    return None


# ── shared conversion helpers ───────────────────────────────────────────

def _summarize_tool(tool_name: str, raw_args: Any) -> str:
    args = ""
    if isinstance(raw_args, str):
        args = raw_args
    elif isinstance(raw_args, dict):
        try:
            args = json.dumps(raw_args, ensure_ascii=False)
        except (TypeError, ValueError):
            args = ""
    args = args[:_MAX_TOOL_SUMMARY]
    return f"(used tool {tool_name}: {args})" if args else f"(used tool {tool_name})"


def _summarize_tool_result(payload: Any) -> str:
    text = ""
    if isinstance(payload, str):
        text = payload
    elif isinstance(payload, dict):
        content = payload.get("content") or payload.get("output") or ""
        text = content if isinstance(content, str) else json.dumps(
            content, ensure_ascii=False)
    text = " ".join(str(text).split())[:_MAX_TOOL_SUMMARY]
    return f"(tool result: {text})" if text else "(tool result)"


def _merge_consecutive(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Merge consecutive same-role messages (joined with a blank line)."""
    merged: List[Dict[str, str]] = []
    for msg in messages:
        if (
            merged
            and merged[-1]["role"] == msg["role"]
            and msg["role"] in {"user", "assistant"}
        ):
            merged[-1]["content"] = (
                merged[-1]["content"] + "\n\n" + msg["content"]
            ).strip()
            continue
        merged.append({"role": msg["role"], "content": msg["content"].strip()})
    return merged


def _ensure_leading_user(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """A leading assistant turn gets a single user stub in front of it."""
    if messages and messages[0]["role"] == "assistant":
        return [{"role": "user", "content": _LEADING_USER_STUB}] + messages
    return messages


def finalize_messages(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Contract post-processing: merge + leading-user + drop empties."""
    cleaned = [m for m in messages if m["content"].strip()]
    merged = _merge_consecutive(cleaned)
    return _ensure_leading_user([m for m in merged if m["content"].strip()])


# ── Claude Code (~/.claude/projects/*/*.jsonl) ──────────────────────────

def _claude_content_to_text(content: Any) -> Tuple[str, List[str]]:
    """Return (text, tool_summaries) for one Claude Code message content."""
    if isinstance(content, str):
        return content, []
    texts: List[str] = []
    tools: List[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                if block:
                    texts.append(str(block))
                continue
            btype = block.get("type")
            if btype == "text":
                texts.append(str(block.get("text") or ""))
            elif btype == "thinking":
                continue  # reasoning payload is never imported
            elif btype == "tool_use":
                tools.append(_summarize_tool(
                    block.get("name") or "tool", block.get("input")))
            elif btype in ("tool_result", "tool_result_content"):
                tools.append(_summarize_tool_result(block.get("content")))
            # everything else skipped
    return "\n".join(t for t in texts if t.strip()), tools


def parse_claude_code_jsonl(path: Path) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue  # malformed line — skip, keep the rest
            if not isinstance(obj, dict):
                continue
            mtype = obj.get("type")
            if mtype not in ("user", "assistant"):
                continue  # system / summary / meta lines are never imported
            message = obj.get("message")
            if not isinstance(message, dict):
                continue
            text, tools = _claude_content_to_text(message.get("content"))
            for summary in tools:
                messages.append({"role": "assistant", "content": summary})
            if text.strip():
                messages.append({
                    "role": "assistant" if mtype == "assistant" else "user",
                    "content": text,
                })
    return finalize_messages(messages)


# ── Codex CLI (~/.codex/sessions/**/rollout-*.jsonl) ────────────────────

def _codex_payload_to_entry(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    ptype = payload.get("type")
    if ptype == "message":
        role = payload.get("role")
        if role not in ("user", "assistant"):
            return None
        texts = []
        content = payload.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") in ("input_text", "output_text", "text"):
                        texts.append(str(block.get("text") or ""))
                elif block:
                    texts.append(str(block))
        elif isinstance(content, str):
            texts.append(content)
        text = "\n".join(t for t in texts if t.strip())
        if not text.strip():
            return None
        return {"role": role, "content": text}
    if ptype == "function_call":
        name = payload.get("name") or "tool"
        return {"role": "assistant",
                "content": _summarize_tool(name, payload.get("arguments"))}
    if ptype == "function_call_output":
        return {"role": "user",
                "content": _summarize_tool_result(payload.get("output"))}
    # reasoning / session_meta / event_msg — never imported
    return None


def parse_codex_rollout(path: Path) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                continue
            entry = _codex_payload_to_entry(payload)
            if entry is not None:
                messages.append(entry)
    return finalize_messages(messages)


# ── import ──────────────────────────────────────────────────────────────

def parse_foreign_session(path: Path) -> Optional[Tuple[str, List[Dict[str, str]]]]:
    """Detect + parse one foreign session file. None when unrecognized."""
    fmt = detect_format(Path(path))
    if fmt is None:
        return None
    path = Path(path)
    if fmt == "codex":
        return "codex", parse_codex_rollout(path)
    return "claude_code", parse_claude_code_jsonl(path)


def import_foreign_session(
    db,
    path: Path,
    *,
    label: Optional[str] = None,
) -> Optional[str]:
    """Import one foreign session file into ``db``. Returns session id.

    Writes use the SessionDB write surface only (ensure_session +
    append_message + set_session_title). The first user line seeds the
    title; provenance rides on source="imported" and the "<Source>: …"
    prefix (intellect's schema has no origin.imported_from column).
    """
    parsed = parse_foreign_session(Path(path))
    if parsed is None:
        return None
    fmt, messages = parsed
    if not messages:
        return None

    source_label = "Claude Code" if fmt == "claude_code" else "Codex"
    first_user = next(
        (m["content"] for m in messages if m["role"] == "user"), "imported session"
    )
    first_line = " ".join(first_user.split())[:60]
    title = label or f"{source_label}: {first_line}"

    session_id = f"imported_{fmt}_{Path(path).stem[:40]}"
    session_id = "".join(c for c in session_id if c.isalnum() or c in "_-")
    db.ensure_session(session_id, source=SOURCE_IMPORT)
    db.set_session_title(session_id, title)
    # Idempotent re-import: if this deterministic id already carries
    # messages, don't append them a second time.
    if not db.get_messages(session_id):
        for msg in messages:
            db.append_message(session_id, msg["role"], msg["content"])
    return session_id


def discover_foreign_sessions(explicit_home: Optional[Path] = None) -> List[Path]:
    """Scan the standard Claude Code / Codex CLI storage locations."""
    roots: List[Path] = []
    base = Path(explicit_home) if explicit_home else Path.home()
    roots.append(base / ".claude" / "projects")
    roots.append(base / ".codex" / "sessions")
    found: List[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.jsonl")):
            if detect_format(path):
                found.append(path)
    return found
