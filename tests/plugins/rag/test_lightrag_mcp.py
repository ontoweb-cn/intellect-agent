"""LightRAG MCP bridge tests (mocked manager)."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_mcp_manager_state():
    from plugins.rag.lightrag import mcp_server

    mcp_server._manager = None
    mcp_server._manager_ok = False
    mcp_server._manager_message = ""
    mcp_server._init_attempted = False
    mcp_server._last_init_attempt = 0.0
    yield
    mcp_server._manager = None
    mcp_server._manager_ok = False
    mcp_server._manager_message = ""
    mcp_server._init_attempted = False
    mcp_server._last_init_attempt = 0.0


def _mock_mgr():
    mgr = MagicMock()
    mgr.search.return_value = "chunk one"
    mgr.query_answer.return_value = {"response": "answer"}
    mgr.list_documents.return_value = {"documents": [{"id": "d1"}]}
    mgr.insert_text.return_value = {"track_id": "t1"}
    mgr.upload_document.return_value = {"track_id": "t2"}
    return mgr


def _install_mock_mgr():
    from plugins.rag.lightrag import mcp_server

    mgr = _mock_mgr()
    mcp_server._manager = mgr
    mcp_server._manager_ok = True
    mcp_server._init_attempted = True
    return mgr


def test_create_mcp_registers_five_tools():
    from plugins.rag.lightrag.mcp_server import create_lightrag_mcp

    mcp = create_lightrag_mcp(scope="auto")
    tool_names = sorted(t.name for t in mcp._tool_manager.list_tools())
    assert tool_names == sorted([
        "lightrag_search",
        "lightrag_query",
        "lightrag_list_documents",
        "lightrag_insert_text",
        "lightrag_upload_document",
    ])


def test_render_mcp_config_contains_lightrag_scope():
    from plugins.rag.lightrag.mcp_server import render_mcp_config

    output = render_mcp_config(scope="member")
    assert "lightrag-member" in output
    assert "mcpServers" in output


def test_cli_registers_mcp_subcommand():
    import argparse
    from plugins.rag.lightrag.cli import register_cli

    parser = argparse.ArgumentParser()
    register_cli(parser)
    args = parser.parse_args(["mcp", "start", "--scope", "team"])
    assert args.lightrag_command == "mcp"
    assert args.lightrag_mcp_command == "start"
    assert args.scope == "team"


@pytest.mark.asyncio
async def test_search_delegates_to_manager():
    mgr = _install_mock_mgr()
    from plugins.rag.lightrag.mcp_server import create_lightrag_mcp

    mcp = create_lightrag_mcp(scope="auto")
    content, _meta = await mcp.call_tool("lightrag_search", {"query": "docs"})
    result = json.loads(content[0].text)
    assert result["success"] is True
    assert result["context"] == "chunk one"
    mgr.search.assert_called_once()


@pytest.mark.asyncio
async def test_upload_passes_multimodal_kwargs():
    mgr = _install_mock_mgr()
    from plugins.rag.lightrag.mcp_server import create_lightrag_mcp

    mcp = create_lightrag_mcp(scope="auto")
    await mcp.call_tool(
        "lightrag_upload_document",
        {
            "file_path": "/tmp/a.pdf",
            "parse_engine": "mineru",
            "analyze_images": True,
        },
    )
    mgr.upload_document.assert_called_once()
    kwargs = mgr.upload_document.call_args.kwargs
    assert kwargs["parse_engine"] == "mineru"
    assert kwargs["analyze_images"] is True


def test_mcp_start_rejects_invalid_scope():
    import argparse
    from plugins.rag.lightrag.cli import _cmd_mcp_start

    args = argparse.Namespace(scope="bogus")
    with pytest.raises(SystemExit) as exc:
        _cmd_mcp_start(args)
    assert exc.value.code == 1


def test_manager_unavailable_raises():
    import time
    from plugins.rag.lightrag import mcp_server

    mcp_server._manager = None
    mcp_server._manager_ok = False
    mcp_server._init_attempted = True
    mcp_server._last_init_attempt = time.monotonic()
    mcp_server._manager_message = "server unreachable"

    with pytest.raises(RuntimeError, match="server unreachable"):
        mcp_server._get_manager()
