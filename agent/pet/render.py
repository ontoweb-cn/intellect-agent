"""Unicode half-block renderer (V1) — deterministic procedural pet.

truecolor 半块网格渲染 with an independent clarity floor (per deep-dive
unicode fallback). webp sprite decoding and the kitty/iTerm2 graphics
protocols are deferred to the follow-up batch, so V1 renders a
DETERMINISTIC procedural pet derived from (slug, state, frame) — the
same inputs always produce the same pixels.
"""

from __future__ import annotations

import hashlib
from typing import List

from agent.pet.constants import FRAMES_PER_STATE, STATES

_TOP = "▀"
_BOTTOM = "▄"
_FULL = "█"
_GRID_W = 8   # half-block columns
_GRID_H = 8   # full rows → 16 half-block pixel rows


def _seeded_grid(slug: str, state: str, frame: int) -> List[List[int]]:
    """8×8 grid of hue indices, deterministic per (slug, state, frame)."""
    seed_material = f"{slug}:{state}:{frame % FRAMES_PER_STATE}".encode("utf-8")
    grid: List[List[int]] = []
    for row in range(_GRID_H):
        row_vals = []
        for col in range(_GRID_W):
            digest = hashlib.sha256(seed_material + bytes([row, col])).digest()
            row_vals.append(digest[0] % 4)  # 0=empty 1=body 2=shade 3=accent
        grid.append(row_vals)
    return grid


def _color_for(value: int, slug: str) -> str:
    base = int(hashlib.sha256(slug.encode("utf-8")).hexdigest()[:6], 16) % 360
    palette = {1: base, 2: (base + 40) % 360, 3: (base + 200) % 360}
    return f"38;2;{palette.get(value, base)&255};{(palette.get(value, base)//2)%256};{(palette.get(value, base)*2)%256+40}"


def render_unicode(slug: str, state: str = "idle", frame: int = 0) -> List[str]:
    """Render the pet as truecolor half-block lines (rows of ▀/▄ pairs)."""
    if state not in STATES:
        state = "idle"
    grid = _seeded_grid(slug, state, frame)
    lines: List[str] = []
    for row in range(0, _GRID_H, 2):
        top_row, bottom_row = grid[row], grid[row + 1]
        line_parts: List[str] = []
        for col in range(_GRID_W):
            top, bottom = top_row[col], bottom_row[col]
            if top == 0 and bottom == 0:
                line_parts.append(" ")
            elif bottom == 0:
                line_parts.append(f"\x1b[{_color_for(top, slug)}m{_TOP}\x1b[0m")
            elif top == 0:
                line_parts.append(f"\x1b[{_color_for(bottom, slug)}m{_BOTTOM}\x1b[0m")
            else:
                line_parts.append(
                    f"\x1b[{_color_for(top, slug)}m;48;{_color_for(bottom, slug)}m{_FULL}\x1b[0m"
                )
        lines.append("".join(line_parts))
    return lines


def render_png_placeholder() -> bytes:
    """V1 stub for sprite-PNG rendering (webp decode deferred)."""
    raise NotImplementedError(
        "sprite rendering (webp) is deferred to the graphics-protocols batch"
    )
