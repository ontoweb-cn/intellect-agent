"""Cross-worker approval/clarify SSE fan-out via Redis (W4b / P4b)."""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_BRIDGE_LOCK = threading.Lock()
_SUBSCRIBERS: dict[str, Any] = {}
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


def _publish(channel: str, payload: dict[str, Any]) -> None:
    if not _events_backend_is_redis():
        return
    try:
        from agent.events.redis_sync import publish_sync

        publish_sync(channel, payload, config=_CONFIG or _load_config())
    except Exception:
        logger.debug("Redis prompt publish failed on %s", channel, exc_info=True)


def publish_approval_changed(session_id: str, head: dict | None, total: int) -> None:
    from agent.events.channels import webui_approval_channel

    _publish(
        webui_approval_channel(session_id),
        {"session_id": session_id, "head": head, "total": total},
    )


def publish_clarify_changed(session_id: str, head: dict | None, total: int) -> None:
    from agent.events.channels import webui_clarify_channel

    _publish(
        webui_clarify_channel(session_id),
        {"session_id": session_id, "head": head, "total": total},
    )


def subscribe_approval_channel(session_id: str, on_message) -> None:
    """Start a background subscriber for one approval channel (idempotent)."""
    if not _events_backend_is_redis():
        return
    global _CONFIG
    try:
        from agent.events.channels import webui_approval_channel
        from agent.events.redis_sync import RedisSubscriberThread

        channel = webui_approval_channel(session_id)
        with _BRIDGE_LOCK:
            if channel in _SUBSCRIBERS:
                return
            _CONFIG = _CONFIG or _load_config()
            sub = RedisSubscriberThread(
                channel,
                on_message,
                config=_CONFIG,
                name=f"webui-approval-{session_id[:8]}",
            )
            _SUBSCRIBERS[channel] = sub
            sub.start()
    except Exception:
        logger.debug("Approval Redis subscribe failed for %s", session_id, exc_info=True)


def subscribe_clarify_channel(session_id: str, on_message) -> None:
    if not _events_backend_is_redis():
        return
    global _CONFIG
    try:
        from agent.events.channels import webui_clarify_channel
        from agent.events.redis_sync import RedisSubscriberThread

        channel = webui_clarify_channel(session_id)
        with _BRIDGE_LOCK:
            if channel in _SUBSCRIBERS:
                return
            _CONFIG = _CONFIG or _load_config()
            sub = RedisSubscriberThread(
                channel,
                on_message,
                config=_CONFIG,
                name=f"webui-clarify-{session_id[:8]}",
            )
            _SUBSCRIBERS[channel] = sub
            sub.start()
    except Exception:
        logger.debug("Clarify Redis subscribe failed for %s", session_id, exc_info=True)
