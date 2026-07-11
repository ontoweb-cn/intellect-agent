"""Variable-height transcript virtual window (W4 / P1-B).

Pure helpers mirrored by ``webui/static/virtual_window.js``.
Do **not** use fixed sidebar row heights for message pads.
"""

from __future__ import annotations

from typing import Any, Sequence

MSG_VIRTUAL_THRESHOLD = 80
DEFAULT_USER_ROW_HEIGHT = 72.0
DEFAULT_ASSISTANT_ROW_HEIGHT = 120.0


def build_prefix_sums(heights: Sequence[float]) -> list[float]:
    """Return prefix where prefix[i] = sum(heights[:i]) (length len(heights)+1)."""
    prefix = [0.0]
    for h in heights:
        prefix.append(prefix[-1] + max(0.0, float(h or 0)))
    return prefix


def variable_height_virtual_window(
    heights: Sequence[float],
    *,
    scroll_top: float = 0.0,
    viewport_height: float = 600.0,
    buffer_px: float | None = None,
    threshold: int = MSG_VIRTUAL_THRESHOLD,
    pin_index: int | None = None,
    force_start: int | None = None,
) -> dict[str, Any]:
    """Compute a variable-height virtual window over vis-row heights.

    Pads are prefix sums — never ``count * fixedRowHeight``.
    """
    total = len(heights)
    viewport = max(1.0, float(viewport_height or 1))
    buf = float(buffer_px) if buffer_px is not None else viewport * 1.5
    scroll = max(0.0, float(scroll_top or 0))

    if total <= max(1, int(threshold)):
        prefix = build_prefix_sums(heights)
        return {
            "virtualized": False,
            "start": 0,
            "end": total,
            "top_pad": 0.0,
            "bottom_pad": 0.0,
            "total": total,
            "total_height": prefix[-1] if prefix else 0.0,
        }

    prefix = build_prefix_sums(heights)
    total_h = prefix[-1]

    if force_start is not None:
        start = max(0, min(int(force_start), max(0, total - 1)))
        target_bottom = prefix[start] + viewport + buf
        end = start
        while end < total and prefix[end] < target_bottom:
            end += 1
        end = max(end, min(total, start + 1))
    else:
        target_top = max(0.0, scroll - buf)
        target_bottom = scroll + viewport + buf
        start = 0
        while start < total and prefix[start + 1] <= target_top:
            start += 1
        end = start
        while end < total and prefix[end] < target_bottom:
            end += 1
        end = max(end, min(total, start + 1))

    if pin_index is not None and 0 <= int(pin_index) < total:
        pin = int(pin_index)
        if pin < start or pin >= end:
            # Center a viewport-sized window around the pin.
            approx_rows = max(1, int((viewport + 2 * buf) / max(1.0, DEFAULT_ASSISTANT_ROW_HEIGHT)))
            start = max(0, pin - approx_rows // 3)
            end = min(total, start + approx_rows)
            if end <= start:
                end = min(total, start + 1)
            # Grow until viewport+buffer covered.
            while end < total and (prefix[end] - prefix[start]) < (viewport + buf):
                end += 1
            while start > 0 and (prefix[end] - prefix[start]) < (viewport + buf):
                start -= 1

    top_pad = prefix[start]
    bottom_pad = max(0.0, total_h - prefix[end])
    return {
        "virtualized": True,
        "start": start,
        "end": end,
        "top_pad": top_pad,
        "bottom_pad": bottom_pad,
        "total": total,
        "total_height": total_h,
    }


def expand_to_turn_boundaries(
    start: int,
    end: int,
    *,
    roles: Sequence[str],
) -> tuple[int, int]:
    """Expand [start, end) so we never split a consecutive assistant run mid-turn.

    ``roles`` is parallel to vis indices (``user`` / ``assistant`` / other).
    """
    total = len(roles)
    if total == 0:
        return 0, 0
    s = max(0, min(int(start), total))
    e = max(s, min(int(end), total))
    # If start lands on assistant that continues a previous assistant, back up.
    while s > 0 and roles[s] == "assistant" and roles[s - 1] == "assistant":
        s -= 1
    # If end lands mid assistant run, advance to end of run.
    while e < total and e > 0 and roles[e - 1] == "assistant" and roles[e] == "assistant":
        e += 1
    return s, e
