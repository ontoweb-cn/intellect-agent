"""Cache backend abstract base class."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any


class CacheBackend(ABC):
    @abstractmethod
    async def get(self, key: str) -> Any | None: ...

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int | None = None) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def exists(self, key: str) -> bool: ...

    @abstractmethod
    async def clear(self) -> None: ...

    def get_sync(self, key: str) -> Any | None:
        return asyncio.run(self.get(key))

    def set_sync(self, key: str, value: Any, ttl: int | None = None) -> None:
        asyncio.run(self.set(key, value, ttl=ttl))

    def close(self) -> None:
        """Optional cleanup hook."""
