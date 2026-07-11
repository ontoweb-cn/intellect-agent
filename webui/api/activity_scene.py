"""activity_scene_v1 helpers (P1-A / Turn Anchors RFC A3–A6).

Shared by WebUI settings (display alias) and W3 #3 run_journal writers.
Client JS in ``webui/static/ui.js`` mirrors ``compact_activity_scene_v1`` —
keep the drop-oldest tool/thinking (keep newest text) rules in sync.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Iterable

ACTIVITY_SCENE_V = 1
ACTIVITY_SCENE_MAX_SEGMENTS = 40
# Match StreamChannel.DEFAULT_MAX_BYTES — shrink detail, never skip the seq row.
ACTIVITY_SCENE_MAX_EVENT_BYTES = 2 * 1024 * 1024

DISPLAY_COMPACT_WORKLOG = "compact_worklog"
DISPLAY_TRANSPARENT_STREAM = "transparent_stream"
_VALID_DISPLAY_MODES = frozenset({DISPLAY_COMPACT_WORKLOG, DISPLAY_TRANSPARENT_STREAM})

_DROPPABLE_KINDS = frozenset({"tool", "thinking"})

# Terminal SSE events that must be preceded by exactly one activity_scene (A2).
SCENE_PRECEDED_TERMINALS = frozenset({"done", "cancel", "apperror", "error", "stream_end"})

DEFAULT_DISCLOSURE: dict[str, Any] = {"expanded": False, "user_intent": None}


def display_mode_from_simplified(simplified: bool) -> str:
    """Map today's ``simplified_tool_calling`` bool → display alias (A6)."""
    return DISPLAY_COMPACT_WORKLOG if simplified else DISPLAY_TRANSPARENT_STREAM


def simplified_from_display_mode(mode: str | None) -> bool:
    """Map display alias → ``simplified_tool_calling`` (A6)."""
    if mode == DISPLAY_TRANSPARENT_STREAM:
        return False
    return True


def normalize_display_mode(mode: Any) -> str | None:
    if isinstance(mode, str) and mode in _VALID_DISPLAY_MODES:
        return mode
    return None


def resolve_chat_activity_display_mode(settings: dict[str, Any] | None) -> str:
    """Prefer ``chat_activity_display_mode``; else derive from simplified (A6 read)."""
    settings = settings or {}
    mode = normalize_display_mode(settings.get("chat_activity_display_mode"))
    if mode is not None:
        return mode
    simplified = settings.get("simplified_tool_calling", True)
    return display_mode_from_simplified(simplified is not False)


def sync_display_mode_alias(settings: dict[str, Any]) -> dict[str, Any]:
    """Ensure both keys are present and consistent (prefer new alias on conflict)."""
    mode = resolve_chat_activity_display_mode(settings)
    settings["chat_activity_display_mode"] = mode
    settings["simplified_tool_calling"] = simplified_from_display_mode(mode)
    return settings


def apply_display_mode_alias_on_write(
    patch: dict[str, Any],
    current: dict[str, Any],
) -> None:
    """Dual-write both keys for one release (I7).

    Prefer an explicit ``chat_activity_display_mode`` in *patch*; otherwise
    derive the alias from ``simplified_tool_calling`` when that was written.
    Mutates *current* (already merged) in place.
    """
    if "chat_activity_display_mode" in patch:
        mode = normalize_display_mode(patch.get("chat_activity_display_mode"))
        if mode is None:
            mode = resolve_chat_activity_display_mode(current)
        current["chat_activity_display_mode"] = mode
        current["simplified_tool_calling"] = simplified_from_display_mode(mode)
        return
    if "simplified_tool_calling" in patch:
        # Prefer the patch value (save_settings may have already merged it into
        # current; patch is the source of truth for what the client wrote).
        simplified = bool(patch.get("simplified_tool_calling"))
        current["simplified_tool_calling"] = simplified
        current["chat_activity_display_mode"] = display_mode_from_simplified(simplified)


def sync_display_mode_alias_from_stored(
    settings: dict[str, Any],
    stored: dict[str, Any] | None,
) -> dict[str, Any]:
    """Load-path sync: only prefer display when it was present on disk.

    Defaults inject ``chat_activity_display_mode``; without this, a legacy
    settings.json that only has ``simplified_tool_calling: false`` would keep
    the default ``compact_worklog`` and incorrectly flip simplified back on.
    """
    had_display = isinstance(stored, dict) and "chat_activity_display_mode" in stored
    had_simplified = isinstance(stored, dict) and "simplified_tool_calling" in stored
    if had_display:
        return sync_display_mode_alias(settings)
    if had_simplified:
        mode = display_mode_from_simplified(
            settings.get("simplified_tool_calling") is not False
        )
        settings["chat_activity_display_mode"] = mode
        settings["simplified_tool_calling"] = simplified_from_display_mode(mode)
        return settings
    return sync_display_mode_alias(settings)


def compact_activity_scene_v1(
    scene: dict[str, Any] | None,
    *,
    max_segments: int = ACTIVITY_SCENE_MAX_SEGMENTS,
) -> dict[str, Any]:
    """Cap ``segments`` at *max_segments* (A4).

    Drop oldest ``tool`` / ``thinking`` segments first; keep newest ``text``.
    If only text remains and still over cap, drop oldest text.
    Returns a shallow-copied scene dict (segments list is new).
    """
    if not isinstance(scene, dict):
        return {"v": ACTIVITY_SCENE_V, "segments": []}
    out = deepcopy(scene)
    out.setdefault("v", ACTIVITY_SCENE_V)
    segments = out.get("segments")
    if not isinstance(segments, list):
        out["segments"] = []
        return out
    segs = [s for s in segments if isinstance(s, dict)]
    limit = max(1, int(max_segments))
    while len(segs) > limit:
        drop_idx = None
        for i, seg in enumerate(segs):
            kind = seg.get("kind")
            if kind in _DROPPABLE_KINDS:
                drop_idx = i
                break
        if drop_idx is None:
            segs.pop(0)
        else:
            segs.pop(drop_idx)
    out["segments"] = segs
    return out


def validate_activity_scene_shape(scene: dict[str, Any] | None) -> bool:
    """True if *scene* has the frozen A3 minimum field set (types loose)."""
    if not isinstance(scene, dict):
        return False
    if scene.get("v") != ACTIVITY_SCENE_V:
        return False
    for key in (
        "turn_id",
        "stream_id",
        "session_id",
        "mode",
        "display",
        "disclosure",
        "segments",
        "elapsed_ms",
    ):
        if key not in scene:
            return False
    if not isinstance(scene["segments"], list):
        return False
    if not isinstance(scene["disclosure"], dict):
        return False
    return True


def should_emit_activity_scene_before(event_name: str, already_emitted: bool) -> bool:
    """True when *event_name* is a terminal that still needs a preceding scene."""
    if already_emitted:
        return False
    return str(event_name or "") in SCENE_PRECEDED_TERMINALS


def scene_mode_for_terminal(event_name: str, payload: Any = None) -> str:
    """Map terminal event → activity_scene ``mode`` (interrupted vs settled)."""
    _ = payload
    name = str(event_name or "")
    if name in {"cancel", "apperror", "error"}:
        return "interrupted"
    return "settled"


def _preview_from_args(args: Any, limit: int = 500) -> str:
    if isinstance(args, dict):
        for key in ("command", "query", "path", "url", "prompt", "code"):
            val = args.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:limit]
        try:
            return json.dumps(args, ensure_ascii=False, separators=(",", ":"))[:limit]
        except (TypeError, ValueError):
            return str(args)[:limit]
    if args is None:
        return ""
    return str(args)[:limit]


def segments_from_journal_events(events: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Build A3 segments from already-journaled tool/thinking/text rows (A3a)."""
    segments: list[dict[str, Any]] = []
    thinking_parts: list[str] = []
    text_anchor_idx = 0
    open_tools: list[dict[str, Any]] = []

    def _flush_thinking() -> None:
        nonlocal thinking_parts
        text = "".join(thinking_parts).strip()
        thinking_parts = []
        if text and text != "Thinking…":
            segments.append({"kind": "thinking", "text": text[:4000]})

    for raw in events or ():
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("event") or raw.get("type") or "").strip()
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        if name == "activity_scene":
            continue
        if name == "reasoning":
            delta = payload.get("text")
            if isinstance(delta, str) and delta:
                thinking_parts.append(delta)
            continue
        if name == "tool":
            _flush_thinking()
            tool_name = str(payload.get("name") or "tool").strip() or "tool"
            preview = str(payload.get("preview") or "").strip()
            if not preview:
                preview = _preview_from_args(payload.get("args"))
            seg = {
                "kind": "tool",
                "tid": str(payload.get("tid") or ""),
                "name": tool_name,
                "status": "waiting",
                "summary": preview[:500],
            }
            segments.append(seg)
            open_tools.append(seg)
            continue
        if name == "tool_complete":
            _flush_thinking()
            tool_name = str(payload.get("name") or "").strip()
            is_error = bool(payload.get("is_error", False))
            status = "error" if is_error else "done"
            preview = str(payload.get("preview") or "").strip()
            matched = None
            for seg in reversed(open_tools):
                if seg.get("status") != "waiting":
                    continue
                if not tool_name or seg.get("name") == tool_name:
                    matched = seg
                    break
            if matched is not None:
                matched["status"] = status
                if preview:
                    matched["summary"] = preview[:500]
            else:
                segments.append(
                    {
                        "kind": "tool",
                        "tid": str(payload.get("tid") or ""),
                        "name": tool_name or "tool",
                        "status": status,
                        "summary": preview[:500],
                    }
                )
            continue
        if name in {"token", "interim_assistant"}:
            _flush_thinking()
            text = payload.get("text") if name == "token" else payload.get("content")
            if name == "interim_assistant" and not text:
                text = payload.get("text")
            if isinstance(text, str) and text.strip():
                segments.append({"kind": "text", "anchor": f"j-{text_anchor_idx}"})
                text_anchor_idx += 1
            continue

    _flush_thinking()
    return segments


def segments_from_stream_state(
    *,
    tool_calls: list[Any] | None = None,
    reasoning_text: str = "",
    partial_text: str = "",
) -> list[dict[str, Any]]:
    """Fallback segment builder from in-memory stream mirrors (same shape as journal)."""
    segments: list[dict[str, Any]] = []
    think = str(reasoning_text or "").strip()
    if think and think != "Thinking…":
        segments.append({"kind": "thinking", "text": think[:4000]})
    for tc in tool_calls or []:
        if not isinstance(tc, dict) or not tc.get("name"):
            continue
        if tc.get("done") is False:
            status = "waiting"
        elif tc.get("is_error"):
            status = "error"
        else:
            status = "done"
        segments.append(
            {
                "kind": "tool",
                "tid": str(tc.get("tid") or ""),
                "name": str(tc.get("name")),
                "status": status,
                "summary": str(tc.get("preview") or tc.get("snippet") or "")[:500],
            }
        )
    if str(partial_text or "").strip():
        segments.append({"kind": "text", "anchor": "partial-0"})
    return segments


def build_activity_scene_v1(
    *,
    stream_id: str,
    session_id: str,
    mode: str,
    display: str | None = None,
    segments: list[dict[str, Any]] | None = None,
    elapsed_ms: int = 0,
    disclosure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a capped activity_scene_v1 object (A3 + A3a defaults)."""
    disp = normalize_display_mode(display) or DISPLAY_COMPACT_WORKLOG
    disc = DEFAULT_DISCLOSURE if disclosure is None else dict(disclosure)
    disc.setdefault("expanded", False)
    disc.setdefault("user_intent", None)
    sid = str(stream_id or "")
    scene = {
        "v": ACTIVITY_SCENE_V,
        "turn_id": f"live:{sid}" if sid else "",
        "stream_id": sid,
        "session_id": str(session_id or ""),
        "mode": str(mode or "settled"),
        "display": disp,
        "disclosure": disc,
        "segments": list(segments or []),
        "elapsed_ms": max(0, int(elapsed_ms or 0)),
    }
    return compact_activity_scene_v1(scene)


def build_activity_scene_for_stream(
    *,
    stream_id: str,
    session_id: str,
    mode: str,
    display: str | None = None,
    journal_events: Iterable[dict[str, Any]] | None = None,
    tool_calls: list[Any] | None = None,
    reasoning_text: str = "",
    partial_text: str = "",
    elapsed_ms: int = 0,
) -> dict[str, Any]:
    """Prefer journal-derived segments; fall back to live stream mirrors (A3a)."""
    segments = segments_from_journal_events(journal_events)
    if not segments:
        segments = segments_from_stream_state(
            tool_calls=tool_calls,
            reasoning_text=reasoning_text,
            partial_text=partial_text,
        )
    return build_activity_scene_v1(
        stream_id=stream_id,
        session_id=session_id,
        mode=mode,
        display=display,
        segments=segments,
        elapsed_ms=elapsed_ms,
    )


def _scene_wire_bytes(scene: dict[str, Any]) -> int:
    """Byte size of the SSE data payload (scene object at root)."""
    try:
        raw = json.dumps(scene, ensure_ascii=False, separators=(",", ":"))
        return len(raw.encode("utf-8"))
    except (TypeError, ValueError, OverflowError):
        return ACTIVITY_SCENE_MAX_EVENT_BYTES + 1


def bound_activity_scene_for_wire(
    scene: dict[str, Any] | None,
    *,
    max_bytes: int = ACTIVITY_SCENE_MAX_EVENT_BYTES,
) -> dict[str, Any]:
    """Shrink segments/detail until under *max_bytes*; always return a scene.

    Never skips emission — truncate only (I1 / A-J4). Leaves a minimal skeleton
    even if still oversized after aggressive shrink (pathological).
    """
    out = compact_activity_scene_v1(scene if isinstance(scene, dict) else {"v": ACTIVITY_SCENE_V})
    limit = max(1024, int(max_bytes))
    # Leave headroom for event envelope overhead on the channel tuple.
    target = max(512, limit - 256)

    def _shrink_text_fields(max_len: int) -> None:
        for seg in out.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            if seg.get("kind") == "thinking" and isinstance(seg.get("text"), str):
                seg["text"] = seg["text"][:max_len]
            if seg.get("kind") == "tool" and isinstance(seg.get("summary"), str):
                seg["summary"] = seg["summary"][: max(0, max_len // 8)]

    if _scene_wire_bytes(out) <= target:
        return out

    for max_len in (2000, 500, 100, 0):
        _shrink_text_fields(max_len)
        out = compact_activity_scene_v1(out)
        if _scene_wire_bytes(out) <= target:
            return out

    # Drop oldest droppable segments until under budget (keep newest text).
    while len(out.get("segments") or []) > 1 and _scene_wire_bytes(out) > target:
        out = compact_activity_scene_v1(out, max_segments=max(1, len(out["segments"]) - 1))

    if _scene_wire_bytes(out) > target:
        # Last resort: empty segments but keep the seq row shape.
        out["segments"] = []
    return out
