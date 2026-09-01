"""Profile-based routing for the gateway with hierarchical matching (G-12/B1-1).

Allows a single gateway instance to route specific platform scopes (guild /
channel / thread) to different profiles — each with its own model, tools,
memory, and persona. This module is PURE: parse + match only, no I/O and no
gateway imports, so it is unit-testable in isolation and safe to embed in
any consumer (supervisor front end, in-process multiplexer, CLI doctor).

Matching priority (most specific first):
  1. platform + chat_id + thread_id (exact thread)   — specificity 14
  2. platform + guild_id + chat_id (channel route)   — specificity 6
  3. platform + guild_id (guild/server route)        — specificity 2
  4. no match                                        → default profile

Conjunctive semantics: every discriminator a route DECLARES must hold.
``chat_id`` additionally matches ``parent_chat_id`` so a channel route
covers its Discord threads/forum posts (hierarchical parent-chain).

Fail-closed (GW-302 lineage): a route that resolves to a profile this
gateway does not serve raises :class:`ProfileRouteRejected` at the consumer
— the routing layer only reports the match.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import logging

logger = logging.getLogger(__name__)


class ProfileRouteRejected(RuntimeError):
    """An explicit route matched a profile this gateway does not serve."""


@dataclass(frozen=True)
class ProfileRoute:
    """A single routing rule mapping a platform scope to a profile."""

    name: str
    platform: str
    profile: str
    guild_id: Optional[str] = None
    chat_id: Optional[str] = None
    thread_id: Optional[str] = None
    enabled: bool = True

    @property
    def specificity(self) -> int:
        """Higher = more specific. thread(8) > chat(4) > guild(2)."""
        s = 0
        if self.guild_id:
            s += 2
        if self.chat_id:
            s += 4
        if self.thread_id:
            s += 8
        return s

    def matches(
        self,
        platform: str,
        guild_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        parent_chat_id: Optional[str] = None,
    ) -> bool:
        """True when every discriminator this route declares holds.

        Conjunction (AND): a route declaring guild_id AND chat_id requires
        both. ``chat_id`` also matches ``parent_chat_id`` so channel routes
        cover threads/posts parented to that channel.
        """
        if not self.enabled:
            return False
        if self.platform != platform:
            return False
        if self.thread_id and self.thread_id != thread_id:
            return False
        if self.chat_id and self.chat_id != chat_id and self.chat_id != parent_chat_id:
            return False
        if self.guild_id and self.guild_id != guild_id:
            return False
        return True


def parse_profile_routes(raw: Optional[List[Dict[str, Any]]]) -> List[ProfileRoute]:
    """Parse ``gateway.profile_routes`` config entries, most-specific first.

    Entries missing ``platform`` or ``profile`` are skipped with a warning;
    profile names are validated (path-traversal guard) before acceptance.
    """
    if not raw:
        return []
    routes: List[ProfileRoute] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", ""))
        platform = str(entry.get("platform", ""))
        profile = str(entry.get("profile", ""))
        if not platform or not profile:
            logger.warning(
                "Skipping profile route %s: missing platform or profile", name
            )
            continue
        # Validate the profile name lazily to avoid a config import cycle.
        try:
            from intellect_cli.profiles import (
                normalize_profile_name,
                validate_profile_name,
            )

            profile = normalize_profile_name(profile)
            validate_profile_name(profile)
        except (ValueError, ImportError):
            logger.warning(
                "Skipping profile route %s: invalid profile name %r", name, profile
            )
            continue
        routes.append(
            ProfileRoute(
                name=name,
                platform=platform,
                profile=profile,
                guild_id=entry.get("guild_id") or None,
                chat_id=entry.get("chat_id") or None,
                thread_id=entry.get("thread_id") or None,
                enabled=bool(entry.get("enabled", True)),
            )
        )
    routes.sort(key=lambda r: r.specificity, reverse=True)
    logger.debug("Loaded %d profile routes (most-specific-first)", len(routes))
    return routes


def match_profile_route(
    routes: List[ProfileRoute],
    platform: str,
    guild_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    parent_chat_id: Optional[str] = None,
) -> Optional[ProfileRoute]:
    """Best-matching enabled route for this source, or None (default profile)."""
    for route in routes:  # pre-sorted most-specific-first
        if route.matches(
            platform,
            guild_id=guild_id,
            chat_id=chat_id,
            thread_id=thread_id,
            parent_chat_id=parent_chat_id,
        ):
            return route
    return None
