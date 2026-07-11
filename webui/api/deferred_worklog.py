"""Deferred Activity worklog helpers (W5 / P1-A A5).

Mirrored by gates in ``webui/static/ui.js``. Flag default is off (canary).
"""

from __future__ import annotations

DEFERRED_WORKLOG_N = 8
DEFERRED_WORKLOG_IDLE_TIMEOUT_MS = 2000


def should_defer_activity_worklog(
    *,
    enabled: bool,
    compact_worklog: bool,
    tool_count: int,
    settled: bool = True,
) -> bool:
    """Return True when settled compact Activity should use shell-first fill.

    DW6: only compact_worklog. DW7: enabled flag. DW1: N>=8 tools. DW8: settled only.
    """
    if not settled:
        return False
    if not enabled:
        return False
    if not compact_worklog:
        return False
    try:
        n = int(tool_count)
    except (TypeError, ValueError):
        return False
    return n >= DEFERRED_WORKLOG_N


def worklog_shell_label(tool_names: list[str] | None, tool_count: int) -> str:
    """Compact shell summary text (truncated name list)."""
    names = [str(n).strip() for n in (tool_names or []) if str(n).strip()]
    n = max(int(tool_count or 0), len(names))
    if not names:
        return f"{n} tools — expand for details"
    shown = names[:6]
    extra = n - len(shown)
    joined = ", ".join(shown)
    if extra > 0:
        return f"{n} tools: {joined}… (+{extra})"
    return f"{n} tools: {joined}"
