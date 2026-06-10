"""Lightweight invalidation events for session sidebar state.

Single-process mode uses in-memory queues. Multi-worker (W4b) also publishes to
Redis channel ``webui.sessions`` so any worker can invalidate all SSE clients.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

logger = logging.getLogger(__name__)

_SESSION_EVENTS_LOCK = threading.Lock()
_SESSION_EVENTS_SUBSCRIBERS: set[queue.Queue] = set()
_SESSION_EVENTS_VERSION = 0

_REDIS_BRIDGE_LOCK = threading.Lock()
_REDIS_SUBSCRIBER = None
_REDIS_CONFIG: dict | None = None


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


def _fanout_local(payload: dict[str, Any]) -> None:
    with _SESSION_EVENTS_LOCK:
        subscribers = list(_SESSION_EVENTS_SUBSCRIBERS)
    for q in subscribers:
        try:
            q.put_nowait(payload)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass


def _on_redis_message(message: dict[str, Any]) -> None:
    """Apply remote invalidation from another WebUI worker."""
    global _SESSION_EVENTS_VERSION
    if message.get("action") != "sessions_changed":
        return
    with _SESSION_EVENTS_LOCK:
        remote_version = message.get("version")
        if isinstance(remote_version, int) and remote_version > _SESSION_EVENTS_VERSION:
            _SESSION_EVENTS_VERSION = remote_version
        else:
            _SESSION_EVENTS_VERSION += 1
        payload = {
            "type": "sessions_changed",
            "version": _SESSION_EVENTS_VERSION,
            "reason": message.get("reason") or "redis",
            "origin": "redis",
        }
    _fanout_local(payload)


def start_session_events_bridge() -> None:
    """Subscribe to Redis when ``events.backend=redis`` (idempotent)."""
    global _REDIS_SUBSCRIBER, _REDIS_CONFIG
    if not _events_backend_is_redis():
        return
    with _REDIS_BRIDGE_LOCK:
        if _REDIS_SUBSCRIBER is not None:
            return
        try:
            from agent.events.channels import WEBUI_SESSIONS
            from agent.events.redis_sync import RedisSubscriberThread

            _REDIS_CONFIG = _load_config()
            _REDIS_SUBSCRIBER = RedisSubscriberThread(
                WEBUI_SESSIONS,
                _on_redis_message,
                config=_REDIS_CONFIG,
                name="webui-session-events",
            )
            _REDIS_SUBSCRIBER.start()
            logger.info("Session events Redis bridge started on %s", WEBUI_SESSIONS)
        except Exception:
            logger.warning("Session events Redis bridge failed to start", exc_info=True)


def stop_session_events_bridge() -> None:
    """Stop Redis subscriber thread on shutdown."""
    global _REDIS_SUBSCRIBER
    with _REDIS_BRIDGE_LOCK:
        sub = _REDIS_SUBSCRIBER
        _REDIS_SUBSCRIBER = None
    if sub is not None:
        try:
            sub.stop()
        except Exception:
            logger.debug("Failed to stop session events Redis bridge", exc_info=True)


def publish_session_list_changed(reason: str = "session_changed") -> None:
    """Notify connected browsers that the session sidebar may be stale."""
    global _SESSION_EVENTS_VERSION
    with _SESSION_EVENTS_LOCK:
        _SESSION_EVENTS_VERSION += 1
        payload = {
            "type": "sessions_changed",
            "version": _SESSION_EVENTS_VERSION,
            "reason": reason,
        }
    _fanout_local(payload)

    if _events_backend_is_redis():
        try:
            from agent.events.channels import WEBUI_SESSIONS
            from agent.events.redis_sync import publish_sync

            publish_sync(
                WEBUI_SESSIONS,
                {
                    "action": "sessions_changed",
                    "version": payload["version"],
                    "reason": reason,
                },
                config=_REDIS_CONFIG or _load_config(),
            )
        except Exception:
            logger.debug("Redis session invalidation publish failed", exc_info=True)


def subscribe_session_events() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=1)
    with _SESSION_EVENTS_LOCK:
        _SESSION_EVENTS_SUBSCRIBERS.add(q)
    return q


def unsubscribe_session_events(q: queue.Queue) -> None:
    with _SESSION_EVENTS_LOCK:
        _SESSION_EVENTS_SUBSCRIBERS.discard(q)
