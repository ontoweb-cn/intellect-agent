"""Async Redis event bus (P4)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

from agent.events.bus import EventBus
from agent.events.redis_url import resolve_events_redis_url


class RedisEventBus(EventBus):
    """Pub/sub via redis.asyncio; one client per bus instance."""

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self._handlers: dict[str, list[Callable[[dict], Awaitable[None]]]] = {}
        self._tasks: list[asyncio.Task] = []
        self._client = None
        self._pubsub = None
        self._listener_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            import redis.asyncio as aioredis
        except ImportError as exc:
            raise ImportError(
                "Redis event bus requires redis>=5. "
                "Install: pip install 'intellect-agent[high-performance]'"
            ) from exc
        url = resolve_events_redis_url(self._config)
        self._client = aioredis.Redis.from_url(url, decode_responses=True)
        return self._client

    async def publish(self, channel: str, message: dict) -> None:
        client = self._ensure_client()
        await client.publish(channel, json.dumps(message, separators=(",", ":")))

    async def subscribe(self, channel: str, handler: Callable[[dict], Awaitable[None]]) -> None:
        async with self._lock:
            self._handlers.setdefault(channel, []).append(handler)
            if self._listener_task is None:
                self._listener_task = asyncio.create_task(self._listen_loop())

    async def unsubscribe(self, channel: str) -> None:
        async with self._lock:
            self._handlers.pop(channel, None)

    async def _listen_loop(self) -> None:
        client = self._ensure_client()
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        self._pubsub = pubsub
        subscribed: set[str] = set()
        try:
            while True:
                async with self._lock:
                    wanted = set(self._handlers.keys())
                for ch in wanted - subscribed:
                    await pubsub.subscribe(ch)
                    subscribed.add(ch)
                for ch in list(subscribed - wanted):
                    await pubsub.unsubscribe(ch)
                    subscribed.discard(ch)
                if not subscribed:
                    await asyncio.sleep(0.2)
                    continue
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message or message.get("type") != "message":
                    continue
                channel = message.get("channel")
                raw = message.get("data")
                if not channel or not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                async with self._lock:
                    handlers = list(self._handlers.get(channel, ()))
                for handler in handlers:
                    asyncio.create_task(handler(dict(payload)))
        except asyncio.CancelledError:
            raise
        finally:
            try:
                await pubsub.close()
            except Exception:
                pass

    def close(self) -> None:
        if self._listener_task is not None:
            self._listener_task.cancel()
            self._listener_task = None
        if self._client is not None:
            try:
                asyncio.get_event_loop().run_until_complete(self._client.close())
            except Exception:
                pass
            self._client = None
