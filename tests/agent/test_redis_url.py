"""Redis URL resolution for cache/events sections."""

from __future__ import annotations

from agent.events.redis_url import resolve_events_redis_url, resolve_redis_url


def test_resolve_redis_url_from_components():
    url = resolve_redis_url(
        {"host": "redis.internal", "port": 6380, "db": 1, "password": "secret"},
        default_db=0,
    )
    assert url == "redis://:secret@redis.internal:6380/1"


def test_resolve_events_redis_url_defaults_db_one():
    url = resolve_events_redis_url({"events": {"redis": {"host": "localhost"}}})
    assert url.endswith("/1")
