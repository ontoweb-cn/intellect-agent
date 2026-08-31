"""Tool-call ID canonicalization and variant matching (G-08 / A1-5).

Providers mint tool-call ids in different shapes for the SAME logical call:

- OpenAI-compatible: ``call_`` prefix (sometimes stripped downstream)
- Anthropic: ``toolu_`` prefix
- Gemini-compat / function-calling bridges: ``fc_`` / ``functions.``, or
  bare UUIDs; case may flip through serialization round-trips

A history whose assistant ``tool_calls[].id`` and paired ``tool``
``tool_call_id`` differ by prefix or case used to be treated as an orphan
pair: the real result got dropped and a synthetic stub injected in its
place. The canonical-form matching here makes pairing insensitive to those
cosmetic variants while still treating genuinely different ids as different.

Pure Python by design (plan A1-5 revision): this runs once per pre-call
sanitize pass, not per token — a Rust port has no payoff.
"""

from __future__ import annotations

import re

# Known provider prefixes, longest first so stripping is maximal.
_KNOWN_PREFIXES = (
    "functions.",
    "srvtoolu_",
    "toolu_",
    "call_",
    "fc_",
)

# Any leading run of these separators is noise from bridging layers.
_LEADING_NOISE_RE = re.compile(r"^[\s_:.\-]+")

# A uuid-shaped tail (with or without dashes) — used to detect that two
# ids refer to the same uuid even when prefixes differ.
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.IGNORECASE
)


def canonical_tool_call_id(cid: str | None) -> str:
    """Canonical form of a tool-call id: lowercase, prefix-stripped, trimmed.

    Empty/garbage input canonicalizes to "" and never matches anything
    real (callers must keep the empty-set guard).
    """
    if not cid or not isinstance(cid, str):
        return ""
    lowered = cid.strip().lower()
    for prefix in _KNOWN_PREFIXES:
        if lowered.startswith(prefix) and len(lowered) > len(prefix):
            lowered = lowered[len(prefix):]
            break
    lowered = _LEADING_NOISE_RE.sub("", lowered)
    return lowered


def ids_match(call_id: str | None, result_id: str | None) -> bool:
    """True when a call id and a result id refer to the same logical call.

    Exact match fast-path, then canonical-form equality. Empty ids never
    match (an unnamed call cannot claim an unnamed result).
    """
    if not call_id or not result_id:
        return False
    if call_id == result_id:
        return True
    a, b = canonical_tool_call_id(call_id), canonical_tool_call_id(result_id)
    if a and a == b:
        return True
    # uuid fallback: same uuid under different wrappers/prefixes. Normalize
    # dashes before comparing — one side may keep them, the other not.
    ua, ub = _UUID_RE.search(a or ""), _UUID_RE.search(b or "")
    if ua and ub:
        if ua.group(0).replace("-", "") == ub.group(0).replace("-", ""):
            return True
    return False


def result_matches_any(result_id: str | None, call_ids) -> bool:
    """True when *result_id* pairs with ANY of the given call ids."""
    if not result_id:
        return False
    for cid in call_ids:
        if ids_match(cid, result_id):
            return True
    return False


def find_matching_call_id(result_id: str | None, call_ids) -> str:
    """Return the original call id that pairs with *result_id*, else ""."""
    if not result_id:
        return ""
    for cid in call_ids:
        if ids_match(cid, result_id):
            return cid
    return ""
