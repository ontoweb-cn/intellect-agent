"""Phase 3 tests for the graphiti MCP bridge.

Covers:
  - MCP server creation (5 tools, names, descriptions)
  - MCP client config generation (Claude Desktop / Cursor / VS Code)
  - CLI registration (mcp start / mcp config with --scope)
  - Tool delegation (each MCP tool calls the correct manager method)
  - Error handling (manager unavailable → RuntimeError with install hint)

Does NOT require graphiti-core or FalkorDB — the manager is mocked
throughout.
"""

from __future__ import annotations

import json
import sys
import time
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — reset the module-level manager cache between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_mcp_manager_state():
    """Each test starts with a fresh manager cache so init ordering
    doesn't leak between tests."""
    from plugins.memory.graphiti import mcp_server

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
    """Return a MagicMock that looks like GraphitiClientManager."""
    mgr = MagicMock()
    mgr.add_episode.return_value = {"episode_id": "ep-1", "graph": "global"}
    mgr.search_facts.return_value = [
        {"fact": "Alice likes tea", "valid_at": "2024-01-01T00:00:00+00:00"}
    ]
    mgr.search_nodes.return_value = [
        {"node_id": "n1", "name": "Alice", "summary": "A person"}
    ]
    mgr.get_node_timeline.return_value = [
        {"fact": "Alice worked at Acme", "valid_at": "2024-03-01T00:00:00+00:00"}
    ]
    mgr.delete_episode.return_value = {"deleted": True, "episode_id": "ep-1"}
    return mgr


def _install_mock_mgr():
    """Replace _get_manager so it returns a mock."""
    from plugins.memory.graphiti import mcp_server

    mgr = _mock_mgr()
    mcp_server._manager = mgr
    mcp_server._manager_ok = True
    mcp_server._manager_message = ""
    mcp_server._init_attempted = True
    return mgr


# ---------------------------------------------------------------------------
# 1. Server creation — 5 tools registered
# ---------------------------------------------------------------------------

def test_create_mcp_registers_five_tools():
    """create_graphiti_mcp() must register exactly 5 tools with correct names."""
    from plugins.memory.graphiti.mcp_server import create_graphiti_mcp

    mcp = create_graphiti_mcp(scope="auto")
    tool_names = sorted(t.name for t in mcp._tool_manager.list_tools())

    assert tool_names == sorted([
        "graphiti_add_episode",
        "graphiti_search_facts",
        "graphiti_search_nodes",
        "graphiti_get_node_timeline",
        "graphiti_delete_episode",
    ])


def test_create_mcp_tools_have_descriptions():
    """Every MCP tool must include a non-empty description."""
    from plugins.memory.graphiti.mcp_server import create_graphiti_mcp

    mcp = create_graphiti_mcp(scope="auto")
    for t in mcp._tool_manager.list_tools():
        assert t.description, f"Tool {t.name} missing description"


def test_mcp_server_name_is_graphiti():
    from plugins.memory.graphiti.mcp_server import create_graphiti_mcp

    mcp = create_graphiti_mcp(scope="team")
    assert mcp.name == "graphiti"


# ---------------------------------------------------------------------------
# 2. Client config generation
# ---------------------------------------------------------------------------

def test_render_mcp_config_contains_claude_desktop():
    from plugins.memory.graphiti.mcp_server import render_mcp_config

    output = render_mcp_config(scope="auto")
    assert "Claude Desktop" in output
    assert "mcpServers" in output
    assert "graphiti-auto" in output
    assert "command" in output


def test_render_mcp_config_contains_cursor():
    from plugins.memory.graphiti.mcp_server import render_mcp_config

    output = render_mcp_config(scope="member")
    assert "Cursor" in output
    assert "mcpServers" in output
    assert "graphiti-member" in output


def test_render_mcp_config_contains_vscode():
    from plugins.memory.graphiti.mcp_server import render_mcp_config

    output = render_mcp_config(scope="all")
    assert "VS Code" in output
    assert "type" in output
    assert "stdio" in output
    assert "graphiti-all" in output


def test_render_mcp_config_command_is_intellect():
    from plugins.memory.graphiti.mcp_server import render_mcp_config, _intellect_path

    output = render_mcp_config(scope="auto")
    path = _intellect_path()
    assert path in output


def test_render_mcp_config_scopes_correctly():
    """Different scopes produce different server names."""
    from plugins.memory.graphiti.mcp_server import render_mcp_config

    auto_out = render_mcp_config(scope="auto")
    team_out = render_mcp_config(scope="team")

    assert "graphiti-auto" in auto_out
    assert "graphiti-team" in team_out
    assert "graphiti-team" not in auto_out


# ---------------------------------------------------------------------------
# 3. CLI registration
# ---------------------------------------------------------------------------

def test_cli_registers_mcp_subcommand_with_start_and_config():
    import argparse
    from plugins.memory.graphiti.cli import register_cli

    parser = argparse.ArgumentParser()
    register_cli(parser)

    args = parser.parse_args(["mcp", "start", "--scope", "team"])
    assert args.graphiti_command == "mcp"
    assert args.graphiti_mcp_command == "start"
    assert args.scope == "team"

    args = parser.parse_args(["mcp", "config", "--scope", "member"])
    assert args.graphiti_command == "mcp"
    assert args.graphiti_mcp_command == "config"
    assert args.scope == "member"


def test_cli_mcp_start_default_scope_is_auto():
    import argparse
    from plugins.memory.graphiti.cli import register_cli

    parser = argparse.ArgumentParser()
    register_cli(parser)

    args = parser.parse_args(["mcp", "start"])
    assert args.scope == "auto"


def test_cli_mcp_config_default_scope_is_auto():
    import argparse
    from plugins.memory.graphiti.cli import register_cli

    parser = argparse.ArgumentParser()
    register_cli(parser)

    args = parser.parse_args(["mcp", "config"])
    assert args.scope == "auto"


def test_cli_mcp_start_rejects_invalid_scope(capsys):
    """_cmd_mcp_start must sys.exit(1) on invalid scope before
    trying to import the MCP stack."""
    import argparse
    from plugins.memory.graphiti.cli import _cmd_mcp_start

    args = argparse.Namespace(scope="bogus")

    with pytest.raises(SystemExit) as exc:
        _cmd_mcp_start(args)
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# 4. Tool delegation — each MCP tool calls correct manager method
# ---------------------------------------------------------------------------

async def _call_mcp_tool(mcp, name: str, **kwargs) -> str:
    """Await a tool on the MCP server and extract the text result."""
    content, _meta = await mcp.call_tool(name, kwargs)
    return content[0].text


@pytest.mark.asyncio
async def test_add_episode_delegates_to_manager():
    mgr = _install_mock_mgr()
    from plugins.memory.graphiti.mcp_server import create_graphiti_mcp

    mcp = create_graphiti_mcp(scope="auto")
    result_str = await _call_mcp_tool(mcp, "graphiti_add_episode", content="Test episode")
    result = json.loads(result_str)
    assert result["episode_id"] == "ep-1"
    mgr.add_episode.assert_called_once_with(
        content="Test episode",
        source_description="mcp",
        reference_time=None,
    )


@pytest.mark.asyncio
async def test_search_facts_delegates_to_manager():
    mgr = _install_mock_mgr()
    from plugins.memory.graphiti.mcp_server import create_graphiti_mcp

    mcp = create_graphiti_mcp(scope="auto")
    result_str = await _call_mcp_tool(
        mcp, "graphiti_search_facts", query="Alice", max_results=5
    )
    result = json.loads(result_str)
    assert result[0]["fact"] == "Alice likes tea"
    mgr.search_facts.assert_called_once_with(
        query="Alice",
        max_results=5,
        scope="auto",
    )


@pytest.mark.asyncio
async def test_search_nodes_delegates_to_manager():
    mgr = _install_mock_mgr()
    from plugins.memory.graphiti.mcp_server import create_graphiti_mcp

    mcp = create_graphiti_mcp(scope="auto")
    result_str = await _call_mcp_tool(mcp, "graphiti_search_nodes", query="Alice")
    result = json.loads(result_str)
    assert result[0]["name"] == "Alice"
    mgr.search_nodes.assert_called_once()


@pytest.mark.asyncio
async def test_get_node_timeline_delegates_to_manager():
    mgr = _install_mock_mgr()
    from plugins.memory.graphiti.mcp_server import create_graphiti_mcp

    mcp = create_graphiti_mcp(scope="auto")
    result_str = await _call_mcp_tool(
        mcp, "graphiti_get_node_timeline", node_id="n1", since="2024-01-01"
    )
    result = json.loads(result_str)
    assert result[0]["fact"] == "Alice worked at Acme"
    mgr.get_node_timeline.assert_called_once_with(
        node_id="n1",
        since="2024-01-01",
        until=None,
    )


@pytest.mark.asyncio
async def test_delete_episode_delegates_to_manager():
    mgr = _install_mock_mgr()
    from plugins.memory.graphiti.mcp_server import create_graphiti_mcp

    mcp = create_graphiti_mcp(scope="auto")
    result_str = await _call_mcp_tool(
        mcp, "graphiti_delete_episode", episode_id="ep-1", reason="PII leak"
    )
    result = json.loads(result_str)
    assert result["deleted"] is True
    assert result["audit_reason"] == "PII leak"   # audit trail preserved
    mgr.delete_episode.assert_called_once_with(episode_id="ep-1")


@pytest.mark.asyncio
async def test_delete_episode_logs_audit_reason():
    """The delete tool must include the audit reason in the result."""
    mgr = _install_mock_mgr()
    from plugins.memory.graphiti.mcp_server import create_graphiti_mcp

    mcp = create_graphiti_mcp(scope="auto")
    result_str = await _call_mcp_tool(
        mcp, "graphiti_delete_episode", episode_id="ep-2", reason="user request"
    )
    result = json.loads(result_str)
    assert result["audit_reason"] == "user request"


@pytest.mark.asyncio
async def test_tools_return_json_strings():
    """Every MCP tool must return a JSON string (not a dict)."""
    mgr = _install_mock_mgr()
    from plugins.memory.graphiti.mcp_server import create_graphiti_mcp

    mcp = create_graphiti_mcp(scope="auto")
    result_str = await _call_mcp_tool(mcp, "graphiti_search_facts", query="x")
    assert isinstance(result_str, str)
    assert json.loads(result_str)  # valid JSON


# ---------------------------------------------------------------------------
# 5. Error handling
# ---------------------------------------------------------------------------

def test_get_manager_raises_when_init_failed():
    from plugins.memory.graphiti import mcp_server

    # Simulate a failed init — set _init_attempted so the retry
    # cooldown kicks in and _get_manager raises instead of retrying.
    mcp_server._manager = None
    mcp_server._manager_ok = False
    mcp_server._init_attempted = True
    mcp_server._last_init_attempt = time.monotonic()  # retry cooldown not yet elapsed
    mcp_server._manager_message = "graphiti-core not installed — run `uv pip install 'intellect-agent[graphiti]'`"

    with pytest.raises(RuntimeError, match="graphiti-core not installed"):
        mcp_server._get_manager()


def test_mcp_tool_raises_when_manager_unavailable():
    """When the manager is unavailable, calling a tool should raise
    RuntimeError (which the MCP SDK translates to an error response)."""
    from plugins.memory.graphiti import mcp_server

    # Simulate failed init
    mcp_server._manager = None
    mcp_server._manager_ok = False
    mcp_server._init_attempted = True
    mcp_server._last_init_attempt = time.monotonic()  # retry cooldown not yet elapsed
    mcp_server._manager_message = "FalkorDB unreachable"

    with pytest.raises(RuntimeError, match="FalkorDB unreachable"):
        mcp_server._get_manager()


# ---------------------------------------------------------------------------
# 6. _init_manager is idempotent
# ---------------------------------------------------------------------------

def test_init_manager_idempotent():
    """Calling _init_manager twice must not rebuild the manager."""
    from plugins.memory.graphiti import mcp_server

    # First call: manager not set, proceeds to build
    # Since graphiti-core is available, this should succeed
    mcp_server._init_manager(scope="auto")
    assert mcp_server._manager_ok is True
    first = mcp_server._manager

    # Second call: manager already set, returns immediately
    mcp_server._init_manager(scope="team")
    assert mcp_server._manager is first  # same object, not rebuilt


# ---------------------------------------------------------------------------
# 7. _intellect_path resolution
# ---------------------------------------------------------------------------

def test_intellect_path_returns_string():
    from plugins.memory.graphiti.mcp_server import _intellect_path

    path = _intellect_path()
    assert isinstance(path, str)
    assert len(path) > 0
    assert "intellect" in path


def test_intellect_path_respects_env_var(monkeypatch):
    from plugins.memory.graphiti.mcp_server import _intellect_path

    monkeypatch.setenv("INTELLECT_BIN", "/custom/path/intellect")
    assert _intellect_path() == "/custom/path/intellect"
