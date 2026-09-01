"""Tests for the keyless web-search pool (G-15 / A3-3).

Tier tri-state routing, keyless walk strictly last, rescue marked and
non-sticky, and the gate-5 default-off regression."""

import pytest
from agent.web_search_provider import WebSearchProvider
from agent.web_search_registry import (
    KeylessProviderView,
    _reset_for_tests,
)


class _Provider(WebSearchProvider):
    def __init__(self, name, *, keyless=True, search=True):
        self._name = name
        self._keyless = keyless
        self._search = search
        self.keyless_calls = 0
        self.search_calls = 0

    @property
    def name(self):
        return self._name

    @property
    def display_name(self):
        return self._name.title()

    def is_available(self):
        return False  # NO credentials — keyed mode unavailable

    def is_keyless_available(self):
        return self._keyless

    def supports_search(self):
        return self._search

    def search(self, query, limit=5):
        self.search_calls += 1
        return {"success": True, "data": {"web": []}, "mode": "keyed"}

    def search_keyless(self, query, limit=5):
        self.keyless_calls += 1
        return {"success": True, "data": {"web": []}, "mode": "keyless"}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    import agent.web_search_registry as wsr

    providers = {
        "tavily": _Provider("tavily", keyless=True),
        "exa": _Provider("exa", keyless=True),
    }
    for p in providers.values():
        wsr.register_provider(p)
    _reset_for_tests()  # isolate the global registry for this test
    for p in providers.values():
        wsr.register_provider(p)
    monkeypatch.setattr(wsr, "_keyless_fallback_enabled", lambda: True)
    # isolate config tier lookups (no user config interference)
    monkeypatch.setattr(wsr, "_tier_for", lambda name: "auto")
    yield wsr, providers
    _reset_for_tests()


def _register_into(wsr, provider):
    wsr.register_provider(provider)


# ── resolution ──────────────────────────────────────────────────────────

def test_keyless_walk_disabled_by_default(registry, monkeypatch):
    """门-5 regression: keyless_fallback defaults OFF — with no keyed
    credentials, resolution returns None instead of an anonymous vendor."""
    wsr, _ = registry
    monkeypatch.setattr(wsr, "_keyless_fallback_enabled", lambda: False)
    assert wsr._resolve(None, capability="search") is None


def test_keyless_walk_enabled_resolves_anonymous_view(registry):
    wsr, _ = registry
    resolved = wsr._resolve(None, capability="search")
    assert isinstance(resolved, KeylessProviderView)
    assert resolved.name.endswith("-keyless")


def test_paid_tier_blocks_keyless_walk(registry, monkeypatch):
    wsr, providers = registry
    monkeypatch.setattr(wsr, "_tier_for", lambda name: "paid")
    assert wsr._resolve(None, capability="search") is None


def test_free_tier_forces_keyless_on_keyed_provider(registry, monkeypatch):
    """tier=free forces the provider's KEYLESS mode even when keyed
    credentials are available (tri-state semantics)."""
    wsr, providers = registry
    monkeypatch.setattr(wsr, "_tier_for",
                        lambda name: "free" if name == "tavily" else "auto")
    keyed = _Provider("tavily", keyless=True)
    monkeypatch.setattr(keyed, "is_available", lambda: True)  # keyed creds OK
    _register_into(wsr, keyed)
    resolved = wsr._resolve(None, capability="search")
    assert isinstance(resolved, KeylessProviderView)
    assert resolved.name == "tavily-keyless"


def test_keyed_available_provider_wins_before_keyless_walk(registry, monkeypatch):
    """Legacy preference walk (keyed) beats the keyless walk — keyless is
    strictly LAST in resolution."""
    wsr, providers = registry
    keyed = _Provider("firecrawl", keyless=False)
    monkeypatch.setattr(keyed, "is_available", lambda: True)
    _register_into(wsr, keyed)
    resolved = wsr._resolve(None, capability="search")
    assert resolved.name == "firecrawl"  # keyed legacy preference, not keyless


# ── rescue ──────────────────────────────────────────────────────────────

def test_rescue_provider_skips_excluded(registry):
    wsr, _ = registry
    rescue = wsr.get_keyless_provider("search", exclude="tavily")
    assert isinstance(resolved := rescue, KeylessProviderView)
    assert resolved.name != "tavily-keyless"


def test_rescue_disabled_without_keyless_flag(registry, monkeypatch):
    wsr, _ = registry
    monkeypatch.setattr(wsr, "_keyless_fallback_enabled", lambda: False)
    assert wsr.get_keyless_provider("search") is None
