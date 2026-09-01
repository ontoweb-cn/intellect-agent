"""Pet state machine: activity signal → state with priority (deep-dive:
state 活动信号 → 状态优先级)."""

from __future__ import annotations

import time
from typing import Optional

from agent.pet.constants import STATES

# Activity → state priority: recent activity means walk/alert, silence
# means idle, long silence means sleep.
_SLEEP_AFTER_S = 3600.0
_WALK_WINDOW_S = 20.0

_state: str = "idle"
_last_activity: Optional[float] = None


def mark_activity() -> None:
    """Record a user/agent activity signal (message sent, command run)."""
    global _last_activity, _state
    _last_activity = time.monotonic()
    _state = "alert"


def current_state(now: Optional[float] = None) -> str:
    """Derive the current pet state from the activity signal."""
    global _state
    if now is None:
        now = time.monotonic()
    if _last_activity is None:
        return "idle"
    since = now - _last_activity
    if since < _WALK_WINDOW_S:
        return "walk"
    if since >= _SLEEP_AFTER_S:
        return "sleep"
    return "idle"


def reset() -> None:
    global _last_activity, _state
    _last_activity = None
    _state = "idle"


def valid_states() -> tuple:
    return STATES
