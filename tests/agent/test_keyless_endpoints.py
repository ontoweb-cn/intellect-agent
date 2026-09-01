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


# ── parallel (stateless MCP JSON-RPC) ───────────────────────────────────

@pytest.fixture
def parallel_provider():
    from plugins.web.parallel.provider import ParallelWebSearchProvider

    return ParallelWebSearchProvider()


def test_parallel_is_keyless_available(parallel_provider):
    assert parallel_provider.is_keyless_available() is True


def test_parallel_search_keyless_stateless_call(parallel_provider, monkeypatch):
    import agent.web_keyless_mcp as wk

    captured = {}

    def _fake_call(endpoint, tool, arguments, timeout=60.0):
        captured["endpoint"] = endpoint
        captured["tool"] = tool
        captured["arguments"] = arguments
        return "first line\nsecond line"

    monkeypatch.setattr(wk, "mcp_call", _fake_call)
    monkeypatch.setenv("PARALLEL_KEYLESS_MCP_URL", "https://fake.parallel/mcp")

    out = parallel_provider.search_keyless("test query", limit=5)
    assert out["success"] is True
    assert captured["endpoint"] == "https://fake.parallel/mcp"
    assert captured["tool"] == "web_search"
    assert captured["arguments"]["query"] == "test query"
    assert out["data"]["web"][0]["position"] == 1
    assert len(out["data"]["web"]) == 2


@pytest.mark.asyncio
async def test_parallel_extract_keyless(parallel_provider, monkeypatch):
    import agent.web_keyless_mcp as wk

    captured = {}

    def _fake_call(endpoint, tool, arguments, timeout=60.0):
        captured["tool"] = tool
        captured["arguments"] = arguments
        return "page content"

    monkeypatch.setattr(wk, "mcp_call", _fake_call)

    out = await parallel_provider.extract_keyless(["https://example.com"])
    assert out[0]["content"] == "page content"
    assert captured["tool"] == "web_fetch"
    assert captured["arguments"] == {"url": "https://example.com"}


def test_mcp_session_call_handshake_sequence(monkeypatch):
    """Exa (session MCP): initialize → session header → initialized →
    tools/call, SSE-framed responses unwrapped."""
    import agent.web_keyless_mcp as wk

    calls = []

    class _Resp:
        status_code = 200
        headers = {"mcp-session-id": "sess-123"}
        text = (
            'event: message\n'
            'data: {"jsonrpc":"2.0","id":9,"result":{"content":'
            '[{"type":"text","text":"found it"}]}}\n'
        )

        def raise_for_status(self):
            return None

    def _fake_post(endpoint, json=None, headers=None, timeout=None):
        calls.append((json.get("method"), headers.get("Mcp-Session-Id")))
        return _Resp()

    monkeypatch.setattr(wk.httpx, "post", _fake_post)

    text = wk.mcp_session_call("https://fake.exa/mcp", "web_search_exa",
                               {"query": "q", "numResults": 3})
    assert text == "found it"
    # sequence: initialize (no session) → initialized (session) → tools/call
    assert calls[0][0] == "initialize" and calls[0][1] is None
    assert calls[1][0] == "notifications/initialized" and calls[1][1] == "sess-123"
    assert calls[2][0] == "tools/call" and calls[2][1] == "sess-123"
    assert calls[2][1] is not None or calls[1][1] == "sess-123"


# ── exa (session MCP, keyless) ──────────────────────────────────────────

@pytest.fixture
def exa_provider():
    from plugins.web.exa.provider import ExaWebSearchProvider

    return ExaWebSearchProvider()


def test_exa_is_keyless_available(exa_provider):
    assert exa_provider.is_keyless_available() is True


def test_exa_search_keyless_parses_text_blob(exa_provider, monkeypatch):
    import agent.web_keyless_mcp as wk

    blob = (
        "Title: First Result\n"
        "URL: https://first.example\n"
        "Published: 2026-09-01\n"
        "Author: N/A\n"
        "Highlights:\n"
        "some highlights here\n"
        "Title: Second Result\n"
        "URL: https://second.example\n"
        "Highlights:\n"
        "other\n"
    )
    captured = {}

    def _fake_call(endpoint, tool, arguments, timeout=60.0):
        captured["endpoint"] = endpoint
        captured["tool"] = tool
        captured["arguments"] = arguments
        return blob

    monkeypatch.setattr(wk, "mcp_session_call", _fake_call)

    out = exa_provider.search_keyless("intellect agent", limit=5)
    assert out["success"] is True
    web = out["data"]["web"]
    assert [w["url"] for w in web] == [
        "https://first.example", "https://second.example"]
    assert web[0]["position"] == 1
    assert "some highlights here" in web[0]["description"]
    assert captured["tool"] == "web_search_exa"
    assert captured["arguments"]["numResults"] == 5


def test_exa_search_keyless_unparseable_blob_falls_back(exa_provider, monkeypatch):
    import agent.web_keyless_mcp as wk

    monkeypatch.setattr(
        wk, "mcp_session_call",
        lambda *a, **kw: "completely unstructured response body")

    out = exa_provider.search_keyless("q")
    assert out["success"] is True
    web = out["data"]["web"]
    assert len(web) == 1
    assert web[0]["note"] == "text-only result"
    assert "unstructured" in web[0]["description"]


@pytest.mark.asyncio
async def test_exa_extract_keyless(exa_provider, monkeypatch):
    from plugins.web.exa import provider as exa_prov

    captured = {}

    def _fake_call(endpoint, tool, arguments, timeout=60.0):
        captured["tool"] = tool
        captured["arguments"] = arguments
        return "page body"

    monkeypatch.setattr(wk := __import__(
        "agent.web_keyless_mcp", fromlist=["mcp_session_call"]),
        "mcp_session_call", _fake_call)
    _ = wk

    out = await exa_prov.ExaWebSearchProvider.extract_keyless(
        exa_provider, ["https://example.com"])
    assert out[0]["content"] == "page body"
    assert captured["tool"] == "web_fetch_exa"
