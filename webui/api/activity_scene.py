"""activity_scene_v1 helpers (P1-A / Turn Anchors RFC A3–A6).

Shared by WebUI settings (display alias) and, in W3 #3, run_journal writers.
Client JS in ``webui/static/ui.js`` mirrors ``compact_activity_scene_v1`` —
keep the drop-oldest tool/thinking (keep newest text) rules in sync.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

ACTIVITY_SCENE_V = 1
ACTIVITY_SCENE_MAX_SEGMENTS = 40

DISPLAY_COMPACT_WORKLOG = "compact_worklog"
DISPLAY_TRANSPARENT_STREAM = "transparent_stream"
_VALID_DISPLAY_MODES = frozenset({DISPLAY_COMPACT_WORKLOG, DISPLAY_TRANSPARENT_STREAM})

_DROPPABLE_KINDS = frozenset({"tool", "thinking"})


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
