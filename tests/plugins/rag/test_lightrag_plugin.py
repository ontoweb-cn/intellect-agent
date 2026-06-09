"""LightRAG RAGProvider integration tests."""

from __future__ import annotations

import json
import httpx

from plugins.rag.lightrag import LightRAGRAGProvider


def _mock_transport():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/query":
            return httpx.Response(200, json={"response": "spec section 3"})
        if request.url.path == "/documents/text":
            captured["body"] = json.loads(request.content.decode())
            return httpx.Response(200, json={"track_id": "t1"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    transport.captured = captured  # type: ignore[attr-defined]
    return transport


def test_provider_prefetch_hybrid(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / ".intellect"))
    home = tmp_path / ".intellect"
    home.mkdir()
    (home / "lightrag").mkdir()
    (home / "lightrag" / "config.json").write_text(
        json.dumps({"server": {"base_url": "http://test"}}),
        encoding="utf-8",
    )

    provider = LightRAGRAGProvider()
    assert provider.is_available()
    provider.initialize(
        "sess1",
        intellect_home=str(home),
        config={
            "rag": {
                "prefetch_policy": "hybrid",
                "prefetch_min_chars": 40,
                "prefetch_keywords": ["spec"],
            }
        },
    )
    provider._mgr._client._client = httpx.Client(transport=_mock_transport())
    ctx = provider.prefetch("What does the spec say about auth?")
    assert "spec section" in ctx


def test_provider_tools_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / ".intellect"))
    home = tmp_path / ".intellect"
    home.mkdir()
    (home / "lightrag").mkdir()
    (home / "lightrag" / "config.json").write_text(
        json.dumps({"server": {"base_url": "http://test"}}),
        encoding="utf-8",
    )

    provider = LightRAGRAGProvider()
    provider.initialize("sess1", intellect_home=str(home), config={})
    transport = _mock_transport()
    provider._mgr._client._client = httpx.Client(transport=transport)

    ins = json.loads(
        provider.handle_tool_call(
            "lightrag_insert_text",
            {"text": "note body", "file_path": "note.md"},
        )
    )
    assert ins["success"] is True
    assert transport.captured["body"]["file_source"] == "note.md"

    sr = json.loads(
        provider.handle_tool_call("lightrag_search", {"query": "note"})
    )
    assert sr["success"] is True
    assert "spec section" in sr["context"]
