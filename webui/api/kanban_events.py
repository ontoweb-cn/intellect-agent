"""Cross-worker Kanban SSE invalidation via Redis (P4b / W4b)."""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_BRIDGE_LOCK = threading.Lock()
_SUBSCRIBERS: dict[str, Any] = {}
_WAKE_EVENTS: dict[str, threading.Event] = {}
_CONFIG: dict | None = None


def _events_backend_is_redis() -> bool:
    try:
        from agent.events.factory import get_events_backend_name
        from intellect_cli.config import load_config

        return get_events_backend_name(load_config()) == "redis"
    except Exception:
        return False


def _load_config() -> dict:
    try:
        from intellect_cli.config import load_config

        return load_config()
    except Exception:
        return {}


def wake_event(board_id: str) -> threading.Event:
    """Return a per-board wake event used to interrupt SSE poll loops."""
    key = str(board_id or "default")
    with _BRIDGE_LOCK:
        ev = _WAKE_EVENTS.get(key)
        if ev is None:
            ev = threading.Event()
            _WAKE_EVENTS[key] = ev
        return ev


def publish_kanban_changed(board_id: str, *, revision: int | None = None) -> None:
    """Notify all workers that Kanban board *board_id* may have new events."""
    key = str(board_id or "default")
    wake_event(key).set()
    if not _events_backend_is_redis():
        return
    try:
        from agent.events.channels import webui_kanban_channel
        from agent.events.redis_sync import publish_sync

        publish_sync(
            webui_kanban_channel(key),
            {"action": "kanban_changed", "board": key, "revision": revision},
            config=_CONFIG or _load_config(),
        )
    except Exception:
        logger.debug("Kanban Redis publish failed for %s", key, exc_info=True)


def _on_redis_message(message: dict[str, Any]) -> None:
    if message.get("action") != "kanban_changed":
        return
    board = str(message.get("board") or "default")
    wake_event(board).set()


def start_kanban_events_bridge() -> None:
    """Subscribe to per-board Kanban invalidation when ``events.backend=redis``."""
    global _CONFIG
    if not _events_backend_is_redis():
        return
    with _BRIDGE_LOCK:
        if _SUBSCRIBERS.get("__ready__"):
            return
        try:
            from agent.events.channels import webui_kanban_channel
            from agent.events.redis_sync import RedisSubscriberThread

            _CONFIG = _load_config()
            # Default board channel — additional boards subscribe lazily on publish.
            default_channel = webui_kanban_channel("default")
            sub = RedisSubscriberThread(
                default_channel,
                _on_redis_message,
                config=_CONFIG,
                name="webui-kanban-events",
            )
            _SUBSCRIBERS[default_channel] = sub
            sub.start()
            _SUBSCRIBERS["__ready__"] = True
            logger.info("Kanban events Redis bridge started on %s", default_channel)
        except Exception:
            logger.warning("Kanban events Redis bridge failed to start", exc_info=True)


def subscribe_kanban_board(board_id: str) -> None:
    """Ensure a Redis subscriber exists for *board_id* (idempotent)."""
    if not _events_backend_is_redis():
        return
    global _CONFIG
    key = str(board_id or "default")
    try:
        from agent.events.channels import webui_kanban_channel
        from agent.events.redis_sync import RedisSubscriberThread

        channel = webui_kanban_channel(key)
        with _BRIDGE_LOCK:
            if channel in _SUBSCRIBERS:
                return
            _CONFIG = _CONFIG or _load_config()
            sub = RedisSubscriberThread(
                channel,
                _on_redis_message,
                config=_CONFIG,
                name=f"webui-kanban-{key[:12]}",
            )
            _SUBSCRIBERS[channel] = sub
            sub.start()
    except Exception:
        logger.debug("Kanban Redis subscribe failed for %s", key, exc_info=True)
