"""Event bus abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any


class EventBus(ABC):
    @abstractmethod
    async def publish(self, channel: str, message: dict) -> None: ...

    @abstractmethod
    async def subscribe(self, channel: str, handler: Callable[[dict], Awaitable[None]]) -> None: ...

    @abstractmethod
    async def unsubscribe(self, channel: str) -> None: ...

    def close(self) -> None:
        """Optional cleanup hook."""
