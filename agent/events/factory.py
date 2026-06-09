"""Event bus factory."""

from __future__ import annotations

import os

from agent.events.memory_bus import MemoryEventBus


def get_events_backend_name(config: dict | None = None) -> str:
    cfg = config or {}
    events = cfg.get("events") if isinstance(cfg.get("events"), dict) else {}
    env = (os.environ.get("INTELLECT_EVENTS_BACKEND") or "").strip().lower()
    if env in ("memory", "redis"):
        return env
    name = str(events.get("backend") or "memory").strip().lower()
    return name if name in ("memory", "redis") else "memory"


def create_event_bus(config: dict | None = None):
    name = get_events_backend_name(config)
    if name == "memory":
        return MemoryEventBus(config)
    if name == "redis":
        from agent.events.redis_bus import RedisEventBus

        return RedisEventBus(config)
    raise ValueError(f"Unknown events backend: {name!r}")
