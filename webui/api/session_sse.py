"""Session SSE resume helpers (RFC session-sse-contract-v1, W2 B2).

Journal-first resume, strict cursor parse, and ``session_snapshot`` payloads.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

# Provisional journal replay caps (W2 plan §2.3.7) — tunable, not wire-breaking.
MAX_REPLAY_EVENTS = 2000
MAX_REPLAY_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class ParsedCursor:
    """Frozen resume cursor after S6 resolution."""

    after_seq: int | None = None
    run_id: str | None = None
    source: str = "none"  # query | last_event_id | none
    malformed: bool = False
    stale_run: bool = False


def parse_cursor_token(
    raw: str | None,
    *,
    expected_run_id: str | None = None,
) -> ParsedCursor:
    """Parse bare seq or ``{run_id}:{seq}``.

    Malformed tokens are **not** coerced to 0 (RFC S7 breaking change).
    """
    if raw in (None, ""):
        return ParsedCursor()
    text = str(raw).strip()
    if not text:
        return ParsedCursor()
    run_id: str | None = None
    seq_text = text
    if ":" in text:
        run_id, seq_text = text.rsplit(":", 1)
        run_id = run_id.strip() or None
        seq_text = seq_text.strip()
        if not run_id or not seq_text:
            return ParsedCursor(malformed=True, source="query")
    try:
        after_seq = int(seq_text)
    except (TypeError, ValueError):
        return ParsedCursor(malformed=True, source="query")
    if after_seq < 0:
        return ParsedCursor(malformed=True, source="query")
    if (
        run_id is not None
        and expected_run_id
        and run_id != expected_run_id
    ):
        return ParsedCursor(
            after_seq=after_seq,
            run_id=run_id,
            stale_run=True,
            source="query",
        )
    return ParsedCursor(after_seq=after_seq, run_id=run_id, source="query")


def resolve_resume_cursor(
    qs: Mapping[str, list[str]],
    headers: Any | None,
    *,
    expected_run_id: str | None = None,
) -> ParsedCursor:
    """S6 order: query ``after_seq`` / ``cursor`` → ``Last-Event-ID`` → none.

    Query wins when both query and header are present.
    """
    raw: str | None = None
    source = "none"

    after_raw = (qs.get("after_seq") or [None])[0]
    cursor_raw = (qs.get("cursor") or [None])[0]
    if after_raw not in (None, ""):
        raw = str(after_raw)
        source = "query"
    elif cursor_raw not in (None, ""):
        raw = str(cursor_raw)
        source = "query"
    else:
        try:
            header_raw = headers.get("Last-Event-ID") if headers is not None else None
        except Exception:
            header_raw = None
        if header_raw not in (None, ""):
            raw = str(header_raw)
            source = "last_event_id"

    if raw in (None, ""):
        return ParsedCursor(source="none")

    parsed = parse_cursor_token(raw, expected_run_id=expected_run_id)
    if parsed.malformed or parsed.stale_run:
        return ParsedCursor(
            after_seq=parsed.after_seq,
            run_id=parsed.run_id,
            source=source,
            malformed=parsed.malformed,
            stale_run=parsed.stale_run,
        )
    return ParsedCursor(
        after_seq=parsed.after_seq,
        run_id=parsed.run_id,
        source=source,
    )


def cursor_to_after_seq(cursor: str | None) -> int | None:
    """Strict seq extract for adapters. Raises ``ValueError`` on malformed."""
    parsed = parse_cursor_token(cursor)
    if parsed.malformed:
        raise ValueError("unknown_cursor")
    if parsed.stale_run:
        raise ValueError("stale_run")
    return parsed.after_seq


def build_session_snapshot(
    *,
    session_id: str | None,
    active_stream_id: str | None,
    reason: str,
    messages_reload: bool = True,
    detail: str | None = None,
    messages_tail_hint: Any = None,
) -> dict:
    payload: dict[str, Any] = {
        "v": 1,
        "session_id": session_id,
        "active_stream_id": active_stream_id,
        "messages_reload": bool(messages_reload),
        "messages_tail_hint": messages_tail_hint,
        "reason": reason,
    }
    if detail:
        payload["detail"] = detail
    return payload


def _event_accounted_bytes(event: Mapping[str, Any]) -> int:
    try:
        return len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return MAX_REPLAY_BYTES + 1


def plan_journal_replay(
    events,
    *,
    after_seq: int | None,
    max_events: int = MAX_REPLAY_EVENTS,
    max_bytes: int = MAX_REPLAY_BYTES,
) -> tuple[list[dict], str | None]:
    """Filter journal rows after ``after_seq``; detect gap / oversize.

    ``events`` may be a list or a streaming iterator (preferred for large
    journals). Append-only journals are assumed seq-monotonic in file order;
    lists are still sorted for back-compat with unit tests that pass unordered
    fixtures.

    Returns ``(emit_events, gap_reason_or_none)``.
    Continuity: first emitted seq must be ``after_seq + 1`` when ``after_seq``
    is set and the journal has any later rows. Missing middle seq → gap.
    Exceeding caps with remaining rows → gap (never silent truncate-as-success).
    """
    if events is None:
        return [], None

    # Materialize+sort only for concrete sequences; iterators stream in order.
    if isinstance(events, list):
        ordered = sorted(
            (e for e in events if isinstance(e, dict)),
            key=lambda e: int(e.get("seq") or 0),
        )
        if after_seq is not None:
            ordered = [e for e in ordered if int(e.get("seq") or 0) > int(after_seq)]
        event_iter = iter(ordered)
        prefiltered = True
    else:
        event_iter = (e for e in events if isinstance(e, dict))
        prefiltered = False

    expected = (int(after_seq) + 1) if after_seq is not None else None
    emit: list[dict] = []
    accounted = 0
    for event in event_iter:
        seq = int(event.get("seq") or 0)
        if not prefiltered and after_seq is not None and seq <= int(after_seq):
            continue
        if expected is None:
            expected = seq if seq > 0 else 1
        if seq != expected:
            return emit, "gap"
        size = _event_accounted_bytes(event)
        if size > max_bytes:
            return emit, "gap"
        if len(emit) >= max_events or accounted + size > max_bytes:
            # Cap hit with this event still pending → honest gap (and more may follow).
            return emit, "gap"
        emit.append(event)
        accounted += size
        expected = seq + 1

    # Cap path already returned. If we stopped early inside the loop via gap,
    # done. Peek: if iterator still has a next event after a full successful
    # walk, that's fine (exhausted). No extra peek needed.
    return emit, None


def seq_from_event_id(event_id: str | None) -> int | None:
    if not event_id:
        return None
    parsed = parse_cursor_token(str(event_id))
    if parsed.malformed or parsed.after_seq is None:
        return None
    return parsed.after_seq
