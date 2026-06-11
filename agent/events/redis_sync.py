"""Synchronous Redis pub/sub for WebUI workers (threading HTTP server)."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from agent.events.redis_url import resolve_events_redis_url

logger = logging.getLogger(__name__)


def _redis_client(url: str):
    try:
        import redis
    except ImportError as exc:
        raise ImportError(
            "Redis event bus requires the redis package. "
            "Install with: pip install 'intellect-agent[high-performance]' "
            "or pip install 'redis>=5,<6'"
        ) from exc
    return redis.Redis.from_url(url, decode_responses=True)


def publish_sync(
    channel: str,
    message: dict[str, Any],
    *,
    config: dict | None = None,
) -> None:
    """Publish a JSON message; no-op when Redis is unreachable (logged)."""
    try:
        url = resolve_events_redis_url(config)
        client = _redis_client(url)
        try:
            client.publish(channel, json.dumps(message, separators=(",", ":")))
        finally:
            try:
                client.close()
            except Exception:
                pass  # intentionally silent — cleanup/teardown path
    except Exception:
        logger.warning("Redis publish failed on channel %s", channel, exc_info=True)


class RedisSubscriberThread(threading.Thread):
    """Background thread: subscribe to *channel* and invoke *on_message* per payload."""

    def __init__(
        self,
        channel: str,
        on_message: Callable[[dict[str, Any]], None],
        *,
        config: dict | None = None,
        name: str = "redis-event-subscriber",
    ) -> None:
        super().__init__(daemon=True, name=name)
        self._channel = channel
        self._on_message = on_message
        self._config = config
        self._stop = threading.Event()
        self._pubsub = None

    def stop(self) -> None:
        self._stop.set()
        pubsub = self._pubsub
        if pubsub is not None:
            try:
                pubsub.close()
            except Exception:
                pass  # intentionally silent — cleanup/teardown path

    def run(self) -> None:
        try:
            url = resolve_events_redis_url(self._config)
            client = _redis_client(url)
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            self._pubsub = pubsub
            pubsub.subscribe(self._channel)
            while not self._stop.is_set():
                item = pubsub.get_message(timeout=1.0)
                if not item or item.get("type") != "message":
                    continue
                raw = item.get("data")
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict):
                    try:
                        self._on_message(payload)
                    except Exception:
                        logger.debug("Redis subscriber handler failed", exc_info=True)
        except Exception:
            if not self._stop.is_set():
                logger.warning(
                    "Redis subscriber stopped on channel %s",
                    self._channel,
                    exc_info=True,
                )
        finally:
            pubsub = self._pubsub
            if pubsub is not None:
                try:
                    pubsub.close()
                except Exception:
                    pass  # intentionally silent — cleanup/teardown path
