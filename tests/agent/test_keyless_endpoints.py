"""Keyless endpoint tests for the web-search vendors (G-15 / A3-3 rollout).

All transport is mocked — zero real network. Live endpoint verification is
done separately via the curl archive in
docs/plans/2026-09-02-a3-3-keyless-endpoints-analysis.md."""

import pytest


# ── firecrawl (raw REST, no key) ────────────────────────────────────────

@pytest.fixture
def firecrawl_provider():
    from plugins.web.firecrawl.provider import FirecrawlWebSearchProvider

    return FirecrawlWebSearchProvider()


def test_firecrawl_is_keyless_available(firecrawl_provider):
    assert firecrawl_provider.is_keyless_available() is True


def test_firecrawl_search_keyless_no_auth_header(firecrawl_provider, tmp_path, monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"success": True, "data": [
                {"url": "https://x", "title": "X",
                 "description": "desc", "markdown": "# X"},
            ]}

    def _fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setenv("FIRECRAWL_KEYLESS_API_URL", "https://fake.fc")
    import httpx

    monkeypatch.setattr(httpx, "post", _fake_post)

    out = firecrawl_provider.search_keyless("test query", limit=3)

    assert captured["url"] == "https://fake.fc/v1/search"
    assert captured["body"]["query"] == "test query"
    assert captured["body"]["limit"] == 3
    assert "Authorization" not in (captured["headers"] or {})
    assert "api_key" not in (captured["body"] or {})
    assert out["success"] is True
    assert out["data"]["web"][0]["url"] == "https://x"
    assert out["data"]["web"][0]["position"] == 1


@pytest.mark.asyncio
async def test_firecrawl_extract_keyless_legacy_shape(firecrawl_provider, tmp_path, monkeypatch):
    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"success": True, "data": {
                "markdown": "# Page",
                "metadata": {"title": "Page"},
            }}

    monkeypatch.setenv("FIRECRAWL_KEYLESS_API_URL", "https://fake.fc")
    import httpx

    monkeypatch.setattr(httpx, "post",
                        lambda url, json=None, headers=None, timeout=None: _Resp())

    out = await firecrawl_provider.extract_keyless(["https://example.com"])
    assert out[0]["url"] == "https://example.com"
    assert out[0]["content"] == "# Page"
    assert out[0]["metadata"]["title"] == "Page"


@pytest.mark.asyncio
async def test_firecrawl_extract_keyless_per_url_error(firecrawl_provider, tmp_path, monkeypatch):
    def _boom(url, json=None, headers=None, timeout=None):
        raise RuntimeError("refused")

    monkeypatch.setenv("FIRECRAWL_KEYLESS_API_URL", "https://fake.fc")
    import httpx

    monkeypatch.setattr(httpx, "post", _boom)

    out = await firecrawl_provider.extract_keyless(["https://bad.example"])
    assert "error" in out[0]
