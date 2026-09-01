"""
Web Search Provider Registry
============================

Central map of registered web providers. Populated by plugins at import-time
via :meth:`PluginContext.register_web_search_provider`; consumed by the
``web_search`` and ``web_extract`` tool wrappers in :mod:`tools.web_tools` to
dispatch each call to the active backend.

Active selection
----------------
The active provider is chosen by configuration with this precedence:

1. ``web.search_backend`` / ``web.extract_backend``
   (per-capability override).
2. ``web.backend`` (shared fallback).
3. If exactly one capability-eligible provider is registered AND available,
   use it.
4. Legacy preference order — ``firecrawl`` → ``parallel`` → ``tavily`` →
   ``exa`` → ``searxng`` → ``brave-free`` → ``ddgs`` — filtered by
   availability. Matches the historic ``tools.web_tools._get_backend()``
   candidate order so installs that never set a config key keep landing
   on the same provider they did before the plugin migration.
5. Otherwise ``None`` — the tool surfaces a helpful error pointing at
   ``intellect tools``.

The capability filter (``supports_search`` / ``supports_extract``) is
applied at every step so a search-only provider (``brave-free``)
configured as ``web.extract_backend`` correctly falls through to an
extract-capable backend.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional, Any

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)


_providers: Dict[str, WebSearchProvider] = {}
_lock = threading.Lock()


def register_provider(provider: WebSearchProvider) -> None:
    """Register a web search/extract provider.

    Re-registration (same ``name``) overwrites the previous entry and logs
    a debug message — makes hot-reload scenarios (tests, dev loops) behave
    predictably.
    """
    if not isinstance(provider, WebSearchProvider):
        raise TypeError(
            f"register_provider() expects a WebSearchProvider instance, "
            f"got {type(provider).__name__}"
        )
    name = provider.name
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Web provider .name must be a non-empty string")
    with _lock:
        existing = _providers.get(name)
        _providers[name] = provider
    if existing is not None:
        logger.debug(
            "Web provider '%s' re-registered (was %r)",
            name, type(existing).__name__,
        )
    else:
        logger.debug(
            "Registered web provider '%s' (%s)",
            name, type(provider).__name__,
        )


def list_providers() -> List[WebSearchProvider]:
    """Return all registered providers, sorted by name."""
    with _lock:
        items = list(_providers.values())
    return sorted(items, key=lambda p: p.name)


def get_provider(name: str) -> Optional[WebSearchProvider]:
    """Return the provider registered under *name*, or None."""
    if not isinstance(name, str):
        return None
    with _lock:
        return _providers.get(name.strip())


# ---------------------------------------------------------------------------
# Active-provider resolution
# ---------------------------------------------------------------------------


def _read_config_key(*path: str) -> Optional[str]:
    """Resolve a dotted config key from ``config.yaml``. Returns None on miss."""
    try:
        from intellect_cli.config import load_config

        cfg = load_config()
        cur = cfg
        for segment in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(segment)
        if isinstance(cur, str) and cur.strip():
            return cur.strip()
    except Exception as exc:
        logger.debug("Could not read config %s: %s", ".".join(path), exc)
    return None


# Legacy preference order — preserves behaviour for users who set no
# ``web.backend`` / ``web.<capability>_backend`` config key at all. Matches
# the historic candidate order in :func:`tools.web_tools._get_backend`
# (paid providers first so existing paid setups don't get downgraded to
# a free tier on upgrade). Filtered by ``is_available()`` at walk time so
# we don't surface a provider the user has no credentials for.
_LEGACY_PREFERENCE = (
    "firecrawl",
    "parallel",
    "tavily",
    "exa",
    "searxng",
    "brave-free",
    "ddgs",
)


def _keyless_fallback_enabled() -> bool:
    """``web.keyless_fallback`` — default FALSE (recorded ruling: privacy
    stance stricter than Hermes, whose default is True). Anonymous vendor
    endpoints are only walked when explicitly enabled."""
    try:
        from intellect_cli.config import load_config

        cfg = load_config() or {}
        return bool((cfg.get("web") or {}).get("keyless_fallback", False))
    except Exception:
        return False


def _tier_for(name: str) -> str:
    """``web.provider_tier.<name>`` — free | paid | auto (default auto)."""
    try:
        from intellect_cli.config import load_config

        cfg = load_config() or {}
        tiers = (cfg.get("web") or {}).get("provider_tier") or {}
        if isinstance(tiers, dict):
            return str(tiers.get(name, "auto")).lower()
    except Exception:
        pass
    return "auto"


class KeylessProviderView(WebSearchProvider):
    """Dispatcher-facing view of a keyless-capable provider: resolves like
    any provider but its ``is_available()`` mirrors the vendor's anonymous
    availability, and search/extract hit the vendor's keyless mode."""

    def __init__(self, inner: WebSearchProvider) -> None:
        self._inner = inner

    @property
    def name(self) -> str:
        return f"{self._inner.name}-keyless"

    @property
    def display_name(self) -> str:
        return f"{self._inner.display_name} (keyless)"

    def is_available(self) -> bool:
        return self._inner.is_keyless_available()

    def is_keyless_available(self) -> bool:
        return self._inner.is_keyless_available()

    def supports_search(self) -> bool:
        return self._inner.supports_search()

    def supports_extract(self) -> bool:
        return self._inner.supports_extract()

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        return self._inner.search_keyless(query, limit=limit)

    def extract(self, urls: List[str], **kwargs: Any) -> Any:
        return self._inner.extract_keyless(urls, **kwargs)


_KEYLESS_ROUND_ROBIN: tuple = (
    "tavily", "exa", "parallel", "firecrawl", "keenable",
)
# Per-process random seeding + per-request advance → fleet-wide even spread.
import random as _random

_rr_cursor = _random.randrange(1 << 30)
_rr_cursor_rescue = _random.randrange(1 << 30)


def _resolve(configured: Optional[str], *, capability: str) -> Optional[WebSearchProvider]:
    """Resolve the active provider for a capability ("search" | "extract").

    Resolution rules (in order):

    1. **Explicit config wins, ignoring availability.** If
       ``web.{capability}_backend`` or ``web.backend`` names a registered
       provider that supports *capability*, return it even if its
       :meth:`is_available` returns False — the dispatcher will surface a
       precise "X_API_KEY is not set" error to the user instead of silently
       routing somewhere else. Matches legacy
       :func:`tools.web_tools._get_backend` behavior for configured names.

    2. **Single-provider shortcut.** When only one registered provider
       supports *capability* AND ``is_available()`` reports True, return it.

    3. **Legacy preference walk, filtered by availability.** Walk the
       :data:`_LEGACY_PREFERENCE` order (firecrawl → parallel → tavily →
       exa → searxng → brave-free → ddgs) looking for a provider whose
       ``supports_<capability>()`` is True AND whose ``is_available()`` is
       True. Matches the historic ``tools.web_tools._get_backend()``
       candidate order so users with credentials but no explicit config
       key keep landing on the same provider as pre-migration. This is
       the path that fires when no config key is set — pick the
       highest-priority backend the user actually has credentials for.

    Returns None when no provider is configured AND no available provider
    matches the legacy preference; the dispatcher then returns a "set up a
    provider" error to the user.
    """
    with _lock:
        snapshot = dict(_providers)

    def _capable(p: WebSearchProvider) -> bool:
        if capability == "search":
            return bool(p.supports_search())
        if capability == "extract":
            return bool(p.supports_extract())
        return False

    def _is_available_safe(p: WebSearchProvider) -> bool:
        """Wrap ``is_available()`` so a buggy provider doesn't kill resolution."""
        try:
            return bool(p.is_available())
        except Exception as exc:  # noqa: BLE001
            logger.debug("provider %s.is_available() raised %s", p.name, exc)
            return False

    # 1. Explicit config wins — return regardless of is_available() so the
    #    user gets a precise downstream error message rather than a silent
    #    backend switch. Matches _get_backend() in web_tools.py.
    if configured:
        provider = snapshot.get(configured)
        if provider is not None and _capable(provider):
            return provider
        if provider is None:
            logger.debug(
                "web backend '%s' configured but not registered; falling back",
                configured,
            )
        else:
            logger.debug(
                "web backend '%s' configured but does not support '%s'; falling back",
                configured, capability,
            )

    # 2. + 3. Fallback path — filter by availability so we don't surface
    #    a provider the user has no credentials for. Without this filter,
    #    a registered-but-unconfigured provider could end up "active" on
    #    a fresh install with no API keys at all.
    eligible = [
        p for p in snapshot.values()
        if _capable(p) and _is_available_safe(p)
    ]
    if len(eligible) == 1:
        provider = eligible[0]
        # G-15 tier "free" forces keyless even on a single keyed provider.
        if _tier_for(provider.name) == "free":
            try:
                if provider.is_keyless_available():
                    return KeylessProviderView(provider)
            except Exception as exc:
                logger.debug("keyless view unavailable for %s: %s",
                             provider.name, exc)
        return provider

    for legacy in _LEGACY_PREFERENCE:
        provider = snapshot.get(legacy)
        if (
            provider is not None
            and _capable(provider)
            and _is_available_safe(provider)
        ):
            # tier "free" forces this provider's keyless mode (G-15 tri-state)
            if _tier_for(legacy) == "free":
                try:
                    if provider.is_keyless_available():
                        return KeylessProviderView(provider)
                except Exception:
                    pass
            return provider

    # 4. Keyless walk — strictly LAST (G-15): anonymous vendor endpoints,
    # round-robin (per-process random seed + per-request advance), only when
    # explicitly enabled and the tier is not "paid".
    if _keyless_fallback_enabled():
        order = list(_KEYLESS_ROUND_ROBIN)
        global _rr_cursor
        _rr_cursor = (_rr_cursor + 1) % max(len(order), 1)
        ordered = order[_rr_cursor:] + order[:_rr_cursor]
        for legacy in ordered:
            if _tier_for(legacy) == "paid":
                continue
            provider = snapshot.get(legacy)
            if provider is None or not _capable(provider):
                continue
            try:
                if not provider.is_keyless_available():
                    continue
            except Exception as exc:
                logger.debug(
                    "provider %s.is_keyless_available() raised %s", legacy, exc
                )
                continue
            return KeylessProviderView(provider)

    return None


def get_keyless_provider(
    capability: str, exclude: Optional[str] = None
) -> Optional[WebSearchProvider]:
    """Round-robin keyless provider for one-shot rescue (G-15).

    ``exclude`` names a provider whose keyless view must not be returned
    (the failing primary). None when keyless is disabled/unavailable.
    """
    if not _keyless_fallback_enabled():
        return None
    with _lock:
        snapshot = dict(_providers)
    order = list(_KEYLESS_ROUND_ROBIN)
    global _rr_cursor_rescue
    _rr_cursor_rescue = (_rr_cursor_rescue + 1) % max(len(order), 1)
    ordered = order[_rr_cursor_rescue:] + order[:_rr_cursor_rescue]
    for legacy in ordered:
        if legacy == exclude:
            continue
        provider = snapshot.get(legacy)
        if provider is None:
            continue
        try:
            if not provider.is_keyless_available():
                continue
        except Exception:
            continue
        if capability == "search" and provider.supports_search():
            return KeylessProviderView(provider)
        if capability == "extract" and provider.supports_extract():
            return KeylessProviderView(provider)
    return None


def get_active_search_provider() -> Optional[WebSearchProvider]:
    """Resolve the currently-active web search provider.

    Reads ``web.search_backend`` (preferred) or ``web.backend`` (shared
    fallback) from config.yaml; falls back per the module docstring.
    """
    explicit = _read_config_key("web", "search_backend") or _read_config_key("web", "backend")
    return _resolve(explicit, capability="search")


def get_active_extract_provider() -> Optional[WebSearchProvider]:
    """Resolve the currently-active web extract provider.

    Reads ``web.extract_backend`` (preferred) or ``web.backend`` (shared
    fallback) from config.yaml; falls back per the module docstring.
    """
    explicit = _read_config_key("web", "extract_backend") or _read_config_key("web", "backend")
    return _resolve(explicit, capability="extract")


def _reset_for_tests() -> None:
    """Clear the registry. **Test-only.**"""
    with _lock:
        _providers.clear()
