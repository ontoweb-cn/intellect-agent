"""Pet package constants (deep-dive §12 values)."""

from __future__ import annotations

# Sprite sheet geometry (Hermes petdex taxonomy).
FRAME_W = 192
FRAME_H = 208
FRAMES_PER_STATE = 6
LOOP_MS = 1100

# Manifest source — host-pinned against SSRF (deep-dive: 仅允许
# petdex.dev/*.petdex.dev). Any asset URL outside these hosts is rejected.
MANIFEST_URL = "https://petdex.dev/manifest.json"
MANIFEST_TTL_S = 300
ALLOWED_MANIFEST_HOSTS = ("petdex.dev",)
ALLOWED_MANIFEST_HOST_SUFFIX = ".petdex.dev"

STATES = ("idle", "walk", "sleep", "alert")
