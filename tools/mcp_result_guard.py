"""G-18 / A3-6: identical-result reference stub for MCP tool results.

Reuses the G-01 concept (``tool_guardrails.build_identical_result_stub``):
when an MCP result exceeds the tool's budget threshold and byte-matches the
PREVIOUS result for the same tool + arguments, it collapses to a short
reference stub pointing back at the earlier delivery. Cross-turn (not just
same-turn), so the wording differs from the G-01 stub; the hash/dedup
pattern is the shared implementation idea ("一次实现两处复用").
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Optional

from tools.budget_config import DEFAULT_BUDGET

_MAX_TRACKED = 128

_TRACKED: "OrderedDict[tuple, dict]" = OrderedDict()
_LOCK = threading.Lock()


def reset_tracked() -> None:
    """Test helper: clear the dedup store."""
    with _LOCK:
        _TRACKED.clear()


def maybe_stub_identical_result(
    tool_name: str,
    args: dict,
    result: str,
    call_id: Optional[str] = None,
    threshold: Optional[int] = None,
) -> str:
    """Return a reference stub when ``result`` is byte-identical to the
    previous oversized result for the same tool + arguments; otherwise
    record it and pass through."""
    if not isinstance(result, str) or not result:
        return result
    limit = (
        threshold
        if threshold is not None
        else DEFAULT_BUDGET.resolve_threshold(tool_name)
    )
    if len(result) < limit:
        return result

    from agent.tool_guardrails import canonical_tool_args

    try:
        args_key = canonical_tool_args(args)
    except TypeError:
        args_key = ""
    key = (tool_name, args_key)
    digest = hashlib.sha256(result.encode("utf-8", "replace")).hexdigest()

    with _LOCK:
        prev = _TRACKED.get(key)
        if prev is not None and prev["digest"] == digest:
            preview = (args_key or "")[:200]
            return (
                f"[intellect note: this MCP result is byte-identical to your "
                f"previous {tool_name} call with the same arguments (first "
                f"delivered at tool_call_id {prev['call_id']}). Refer to that "
                f"earlier result; it has not changed. Args: {preview}\u2026]"
            )
        _TRACKED[key] = {"digest": digest, "call_id": call_id or ""}
        _TRACKED.move_to_end(key)
        while len(_TRACKED) > _MAX_TRACKED:
            _TRACKED.popitem(last=False)
    return result
