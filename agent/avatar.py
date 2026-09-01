"""Deterministic blob avatars (BT-04 / B2-3).

Pure-arithmetic, zero-network, zero-dependency: a name hashes (FNV-1a 32)
into a seed for a xorshift32 PRNG, and the stream picks stable face
parameters — the same name renders the same blob forever, on every machine,
in every process (the whole point of "deterministic blob avatar" per
Hermes blobatar, minus the npm dependency).

Renders as SVG (embedding/UI) or ANSI-256 (terminal roster)."""

from __future__ import annotations

from typing import Any, Dict, List

_FNV_OFFSET = 0x811C9DC5
_FNV_PRIME = 0x01000193
_MASK32 = 0xFFFFFFFF

_EYES = ("•‿•", "◕‿◕", "•︿•", "◔_◔", "¬‿¬", "◉▾◉")
_MOUTHS = ("‿", "︿", "o", "◎", "_")

# ANSI-256 hue buckets (evenly spaced, perceptually distinct enough for a
# roster list; pure table lookup — no color math needed).
_HUE_TO_256 = (196, 201, 135, 46, 220, 209, 203, 171, 99, 39)


def fnv1a32(data: bytes) -> int:
    """32-bit FNV-1a (standard test vectors: b"" → 0x811c9dc5)."""
    h = _FNV_OFFSET
    for byte in data:
        h ^= byte
        h = (h * _FNV_PRIME) & _MASK32
    return h


def _xorshift32(state: int):
    """Yield an unbounded stream of 32-bit xorshift values (deterministic)."""
    x = state & _MASK32
    while True:
        x ^= (x << 13) & _MASK32
        x ^= x >> 17
        x ^= (x << 5) & _MASK32
        yield x


def avatar_params(name: str) -> Dict[str, Any]:
    """Stable face parameters for one bot name."""
    seed = fnv1a32((name or "bot").strip().lower().encode("utf-8"))
    rng = _xorshift32(seed)
    hue = next(rng) % 360
    hue2 = (hue + 120 + next(rng) % 120) % 360
    return {
        "seed": seed,
        "hue": hue,
        "hue2": hue2,
        "eyes": _EYES[next(rng) % len(_EYES)],
        "mouth": _MOUTHS[next(rng) % len(_MOUTHS)],
        "sym": next(rng) % 2,
    }


def render_svg(name: str, size: int = 64) -> str:
    """A tiny deterministic blob SVG (for web/embedding surfaces)."""
    p = avatar_params(name)
    body = f"hsl({p['hue']}, 70%, 60%)"
    shade = f"hsl({p['hue2']}, 55%, 45%)"
    w = size // 6
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" '
        f'height="{size}" viewBox="0 0 64 64">'
        f'<circle cx="32" cy="32" r="30" fill="{body}"/>'
        f'<ellipse cx="32" cy="40" rx="{w}" ry="{w // 2}" fill="{shade}" opacity="0.6"/>'
        f'<circle cx="22" cy="26" r="5" fill="#222"/>'
        f'<circle cx="42" cy="26" r="5" fill="#222"/>'
        f'<path d="M 24 44 Q 32 {44 + (6 if p["sym"] else 4)} 40 44" '
        f'stroke="#222" stroke-width="3" fill="none" stroke-linecap="round"/>'
        f"</svg>"
    )


def render_ansi(name: str, *, color: bool = True) -> str:
    """Colored terminal face for roster listings (degrades without color)."""
    p = avatar_params(name)
    color_code = _HUE_TO_256[p["hue"] % len(_HUE_TO_256)]
    face = f"({p['eyes']}{p['mouth']}{p['eyes'][0]})"
    if not color:
        return f"{face} {name}"
    return f"\x1b[38;5;{color_code}m{face}\x1b[0m {name}"


def distinct_count(names: List[str]) -> int:
    """How many of the given names render distinct SVGs (test helper)."""
    return len({render_svg(n) for n in names})
