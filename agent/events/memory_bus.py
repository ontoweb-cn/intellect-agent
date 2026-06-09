"""In-process asyncio event bus (default)."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from agent.events.bus import EventBus


class MemoryEventBus(EventBus):
    """Channel subscribers run via asyncio tasks on publish."""

    def __init__(self, config: dict | None = None) -> None:
        self._handlers: dict[str, list[Callable[[dict], Awaitable[None]]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def publish(self, channel: str, message: dict) -> None:
        async with self._lock:
            handlers = list(self._handlers.get(channel, ()))
        for handler in handlers:
            asyncio.create_task(handler(dict(message)))

    async def subscribe(self, channel: str, handler: Callable[[dict], Awaitable[None]]) -> None:
        async with self._lock:
            self._handlers[channel].append(handler)

    async def unsubscribe(self, channel: str) -> None:
        async with self._lock:
            self._handlers.pop(channel, None)
