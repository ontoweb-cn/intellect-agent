"""Bi-temporal timeline rendering — Phase 5.2.

``graphiti_get_node_timeline`` already returns the raw bi-temporal
records (``fact`` + ``valid_at`` / ``invalid_at`` + ``observed_at``).
This module turns the raw list into something a human can scan:

- ``render_timeline_text(records)`` → a compact ASCII rendering with
  one line per fact, clearly marking the validity window and the
  invalidation point (when present).
- ``render_timeline_json(records)`` → a JSON document with the same
  data normalised into a stable schema, suitable for piping into
  other tooling.

Both renderers accept the dict shape produced by
``GraphitiClient._get_node_timeline``:

    {
      "fact":        str,
      "valid_at":    str | None  (ISO-8601 UTC)
      "invalid_at":  str | None  (ISO-8601 UTC; None = still valid)
      "observed_at": str | None  (when intellect first wrote it)
      "episode_id":  str | None
    }

Older client records may lack ``observed_at``; both renderers fall
back to ``created_at`` if present, otherwise leave the cell empty.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 string; tolerate trailing 'Z' and naive values."""
    if not s:
        return None
    try:
        # datetime.fromisoformat handles "+00:00" but historically chokes
        # on a trailing "Z"; normalize first.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fmt_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    # Minute precision; the bi-temporal value is rarely meaningful below that.
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _normalize_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one raw record into the renderer's stable schema."""
    valid_at = _parse_iso(rec.get("valid_at"))
    invalid_at = _parse_iso(rec.get("invalid_at"))
    observed_at = _parse_iso(rec.get("observed_at")) or _parse_iso(
        rec.get("created_at")
    )
    return {
        "fact": (rec.get("fact") or "").strip(),
        "valid_at": valid_at,
        "invalid_at": invalid_at,
        "observed_at": observed_at,
        "episode_id": rec.get("episode_id"),
    }


def _is_active(rec: Dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    """A fact is currently active if invalid_at is None or in the future."""
    iv = rec.get("invalid_at")
    if iv is None:
        return True
    now = now or datetime.now(timezone.utc)
    return iv > now


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def render_timeline_text(
    records: Iterable[Dict[str, Any]],
    *,
    node_id: str = "",
    now: Optional[datetime] = None,
    max_fact_len: int = 100,
) -> str:
    """Render the timeline as compact ASCII.

    Layout (one fact per line):

        ▶  2024-03-12 10:30 → still valid     | Alice prefers tea
        ✓  2024-03-12 10:30 → 2024-05-01 08:00 | Alice works at Acme  [observed 2024-03-12 10:30]
        ?  unknown          → unknown          | Free-floating fact

    Sorted by ``valid_at`` ascending; records with no ``valid_at`` come
    last (sorted by ``observed_at`` if present).  Active facts are
    grouped at the bottom under a separator so the reader sees what's
    currently true at a glance.
    """
    norm = [_normalize_record(r) for r in records]
    if not norm:
        empty_head = f"  Graphiti timeline for node {node_id}\n" if node_id else "  Graphiti timeline\n"
        return empty_head + "    (no facts found)\n"

    # Stable sort: by valid_at, then observed_at, then fact.
    def _key(r: Dict[str, Any]):
        va = r.get("valid_at") or datetime.max.replace(tzinfo=timezone.utc)
        oa = r.get("observed_at") or datetime.max.replace(tzinfo=timezone.utc)
        return (va, oa, r.get("fact") or "")

    norm.sort(key=_key)
    active = [r for r in norm if _is_active(r, now=now)]
    historical = [r for r in norm if not _is_active(r, now=now)]

    out: List[str] = []
    if node_id:
        out.append(f"  Graphiti timeline for node {node_id}")
    else:
        out.append("  Graphiti timeline")
    out.append("")

    if historical:
        out.append("  Historical:")
        for r in historical:
            out.append("    " + _format_line(r, marker="✓", max_fact_len=max_fact_len))
        out.append("")

    if active:
        out.append("  Currently valid:")
        for r in active:
            marker = "▶" if r.get("valid_at") else "?"
            out.append("    " + _format_line(r, marker=marker, max_fact_len=max_fact_len))
        out.append("")

    return "\n".join(out)


def _format_line(r: Dict[str, Any], *, marker: str, max_fact_len: int) -> str:
    fact = (r.get("fact") or "").strip()
    if max_fact_len and len(fact) > max_fact_len:
        fact = fact[: max_fact_len - 1] + "…"
    valid_at = _fmt_dt(r.get("valid_at"))
    invalid_at = r.get("invalid_at")
    invalid_str = _fmt_dt(invalid_at) if invalid_at else "still valid"
    observed_at = r.get("observed_at")
    observed_str = (
        f"  [observed {_fmt_dt(observed_at)}]" if observed_at else ""
    )
    return f"{marker}  {valid_at} → {invalid_str:<18}  {fact}{observed_str}"


def render_timeline_json(
    records: Iterable[Dict[str, Any]],
    *,
    node_id: str = "",
    now: Optional[datetime] = None,
) -> str:
    """Render as a JSON document with normalised fields + active flag."""
    norm = [_normalize_record(r) for r in records]
    out: List[Dict[str, Any]] = []
    for r in norm:
        out.append(
            {
                "fact": r.get("fact"),
                "valid_at": r["valid_at"].isoformat() if r.get("valid_at") else None,
                "invalid_at": r["invalid_at"].isoformat() if r.get("invalid_at") else None,
                "observed_at": r["observed_at"].isoformat() if r.get("observed_at") else None,
                "episode_id": r.get("episode_id"),
                "active": _is_active(r, now=now),
            }
        )
    return json.dumps(
        {"node_id": node_id or None, "records": out},
        indent=2,
        sort_keys=False,
    )
