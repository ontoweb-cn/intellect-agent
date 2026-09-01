"""Pets (主题 J / PT V1) — profile-scoped desktop-pet package.

V1 scope (recorded ruling): store + manifest (petdex.dev, host-pinned)
+ state machine + unicode half-block renderer + pets CLI + doctor.
kitty/iTerm2 graphics protocols and the ui-tui petSprite.tsx component
are deferred to a follow-up batch.
"""

from agent.pet.constants import (
    ALLOWED_MANIFEST_HOSTS,
    FRAME_H,
    FRAME_W,
    FRAMES_PER_STATE,
    LOOP_MS,
    MANIFEST_TTL_S,
    MANIFEST_URL,
)

__all__ = [
    "ALLOWED_MANIFEST_HOSTS",
    "FRAME_H",
    "FRAME_W",
    "FRAMES_PER_STATE",
    "LOOP_MS",
    "MANIFEST_TTL_S",
    "MANIFEST_URL",
]
