"""LightRAG HTTP client tests (mocked)."""

from __future__ import annotations

import json

import httpx
import pytest

from plugins.rag.lightrag.client import (
    LightRAGClient,
    LightRAGClientManager,
    merge_query_results,
)


def test_merge_query_results_dedupes():
    a = {"response": "ctx", "references": [{"file_path": "a.md", "reference_id": "1", "content": ["one"]}]}
    b = {"references": [{"file_path": "a.md", "reference_id": "1", "content": ["one"]}]}
    merged = merge_query_results([a, b])
    assert "ctx" in merged
    assert merged.count("one") == 1


def test_client_health_and_query(monkeypatch):
    cfg = {
        "server": {"base_url": "http://test", "timeout_seconds": 5},
        "circuit_breaker": {"threshold": 3, "cooldown_seconds": 30},
        "workspace": {"default": "global"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/query":
            body = json.loads(request.content)
            assert body.get("workspace") == "global"
            assert body.get("only_need_context") is True
            return httpx.Response(200, json={"response": "found chunk"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = LightRAGClient(cfg)
    client._client = httpx.Client(transport=transport)
    assert client.health()["status"] == "ok"
    data = client.query("q", workspace="global")
    assert data["response"] == "found chunk"
    client.close()


def test_parallel_workspace_queries():
    cfg = {
        "server": {"base_url": "http://test", "timeout_seconds": 5},
        "circuit_breaker": {"threshold": 3, "cooldown_seconds": 30},
        "workspace": {
            "default": "global",
            "member_prefix": "member_",
            "team_prefix": "team_",
        },
        "query": {"default_mode": "mix", "enable_rerank": False},
    }
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query":
            call_count["n"] += 1
            body = json.loads(request.content)
            ws = body.get("workspace", "")
            return httpx.Response(200, json={"response": f"hit-{ws}"})
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(handler)
    mgr = LightRAGClientManager(cfg)
    mgr._client._client = httpx.Client(transport=transport)
    mgr.bind_scope(member_id="alice", team_id="eng")
    text = mgr.search("policy?", scope="auto")
    assert call_count["n"] == 2
    assert "member_alice" in text
    assert "team_eng" in text
    mgr.shutdown()


def test_manager_search_multi_workspace():
    cfg = {
        "server": {"base_url": "http://test", "timeout_seconds": 5},
        "circuit_breaker": {"threshold": 3, "cooldown_seconds": 30},
        "workspace": {
            "default": "global",
            "member_prefix": "member_",
            "team_prefix": "team_",
        },
        "query": {"default_mode": "mix", "enable_rerank": False},
    }
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query":
            body = json.loads(request.content)
            calls.append(body.get("workspace"))
            return httpx.Response(200, json={"response": f"hit-{body.get('workspace')}"})
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(handler)
    mgr = LightRAGClientManager(cfg)
    mgr._client._client = httpx.Client(transport=transport)
    mgr.bind_scope(member_id="alice", team_id="eng")
    text = mgr.search("policy?", scope="auto")
    assert "member_alice" in text or "team_eng" in text
    assert len(calls) >= 1
    mgr.shutdown()
