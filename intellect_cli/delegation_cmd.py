"""Shared /delegations slash command logic (HP-203)."""

from __future__ import annotations

from typing import Optional


def _session_filter_key(session_key: Optional[str]) -> Optional[str]:
    if session_key and str(session_key).strip():
        return str(session_key).strip()
    return None


def format_delegations_list(entries: list, *, title: str = "Background delegations") -> str:
    if not entries:
        return f"{title}: (none)"
    lines = [f"{title} ({len(entries)}):"]
    for e in entries:
        hid = e.get("handle_id", "?")
        status = e.get("status", "?")
        goal = (e.get("goal") or "")[:60]
        lines.append(f"  {hid}  [{status}]  {goal}")
    lines.append("Use /delegations show <id> or /delegations cancel <id>.")
    return "\n".join(lines)


def run_delegations_subcommand(
    args: str,
    *,
    session_key: Optional[str] = None,
) -> str:
    """Execute list | show | cancel for background delegations."""
    from tools.async_delegation import (
        cancel_delegation,
        get_delegation,
        list_delegations,
    )

    tokens = (args or "").strip().split()
    sub = tokens[0].lower() if tokens else "list"
    filt = _session_filter_key(session_key)

    if sub in ("list", "ls", ""):
        entries = list_delegations(filt)
        return format_delegations_list(entries)

    if sub in ("show", "status"):
        if len(tokens) < 2:
            return "Usage: /delegations show <handle_id>"
        entry = get_delegation(tokens[1])
        if not entry:
            return f"No delegation found: {tokens[1]}"
        if filt and entry.get("parent_session_key") != filt:
            return f"No delegation found: {tokens[1]}"
        parts = [
            f"Handle: {entry.get('handle_id')}",
            f"Status: {entry.get('status')}",
            f"Goal: {entry.get('goal')}",
        ]
        if entry.get("summary"):
            parts.append(f"Summary: {entry.get('summary')}")
        if entry.get("error"):
            parts.append(f"Error: {entry.get('error')}")
        return "\n".join(parts)

    if sub == "cancel":
        if len(tokens) < 2:
            return "Usage: /delegations cancel <handle_id>"
        entry = get_delegation(tokens[1])
        if not entry:
            return f"No delegation found: {tokens[1]}"
        if filt and entry.get("parent_session_key") != filt:
            return f"No delegation found: {tokens[1]}"
        if entry.get("status") != "running":
            return f"Delegation {tokens[1]} is not running (status={entry.get('status')})."
        if cancel_delegation(tokens[1]):
            return (
                f"Cancel requested for {tokens[1]}. "
                "The subagent will stop at the next interrupt point "
                "(current tool call may finish first)."
            )
        return f"Could not cancel {tokens[1]}."

    return (
        "Usage: /delegations [list|show <id>|cancel <id>]\n"
        "Aliases: /dg, /dlgt"
    )
