"""Anthropic prompt caching strategy.

Single layout: ``system_and_3``. 4 cache_control breakpoints — system
prompt + last 3 non-system messages, all at the same TTL (5m or 1h).
Reduces input token costs by ~75% on multi-turn conversations within a
single session.

Since v0.6.2 delegates to the Rust extension.
"""

from __future__ import annotations

from typing import Any, Dict, List

from intellect_rust import rust_apply_cache_control


def apply_anthropic_cache_control(
    api_messages: List[Dict[str, Any]],
    cache_ttl: str = "5m",
    native_anthropic: bool = False,
) -> List[Dict[str, Any]]:
    """Apply system_and_3 caching strategy to messages for Anthropic models.

    Delegates to the Rust extension (mandatory since v0.6.2).
    """
    return list(rust_apply_cache_control(api_messages, cache_ttl, native_anthropic))
