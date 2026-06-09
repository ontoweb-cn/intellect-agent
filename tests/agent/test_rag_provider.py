"""Tests for RAGProvider ABC and RAGManager."""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from agent.rag_manager import RAGManager, build_rag_context_block
from agent.rag_provider import RAGProvider


def test_rag_provider_abc_exists():
    assert hasattr(RAGProvider, "name")
    assert hasattr(RAGProvider, "prefetch")
    assert hasattr(RAGProvider, "get_tool_schemas")


class _StubRAG(RAGProvider):
    @property
    def name(self) -> str:
        return "stub"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        pass

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return "doc hit"

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [{"name": "stub_search", "description": "x", "parameters": {}}]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        return json.dumps({"success": True, "tool": tool_name})


def test_build_rag_context_block_wraps_content():
    out = build_rag_context_block("chunk one")
    assert out.startswith("<rag-context>")
    assert out.rstrip().endswith("</rag-context>")
    assert "chunk one" in out


def test_rag_manager_prefetch_and_tools():
    mgr = RAGManager()
    mgr.add_provider(_StubRAG())
    assert mgr.prefetch_all("query about docs") == "doc hit"
    assert mgr.has_tool("stub_search")
    result = json.loads(mgr.handle_tool_call("stub_search", {"q": "x"}))
    assert result["success"] is True


def test_rag_manager_rejects_second_provider():
    mgr = RAGManager()

    class _Other(RAGProvider):
        @property
        def name(self) -> str:
            return "other"

        def is_available(self) -> bool:
            return True

        def initialize(self, session_id: str, **kwargs) -> None:
            pass

        def get_tool_schemas(self) -> List[Dict[str, Any]]:
            return []

    mgr.add_provider(_StubRAG())
    mgr.add_provider(_Other())
    assert len(mgr.providers) == 1
