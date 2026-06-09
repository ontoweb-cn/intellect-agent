"""Graphiti MCP server — expose the 5 graphiti tools as an MCP stdio server.

Phase 3 (Option A) — plugin-internal MCP bridge.  When an MCP client
(e.g. Claude Desktop, Cursor, VS Code) starts this server, it talks the
Model Context Protocol over stdin/stdout and can read/write the user's
knowledge graph through the standard ``graphiti_*`` tools.

Usage via CLI::

    intellect graphiti mcp start --scope auto

The server:

1. Reads connection config from ``$INTELLECT_HOME/graphiti/config.json``
2. Loads ontology from ``$INTELLECT_HOME/graphiti/ontology.yaml`` (when present)
3. Creates a single ``GraphitiClientManager`` bound to the requested scope
4. Registers the 5 graphiti tools as MCP tools
5. Runs until the client disconnects (or the process receives EOF/SIGTERM)

Dependency note: this module imports graphiti-core; load it only via
``intellect graphiti mcp start``.  Discovery code paths (``discover_memory_providers``
etc.) never touch this file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Manager factory (one per server process)
# ---------------------------------------------------------------------------

_manager: Any = None                  # GraphitiClientManager or None
_manager_scope: str = "auto"
_manager_ok: bool = False             # True once successfully built
_manager_message: str = ""            # Error message from failed init
_init_attempted: bool = False         # True once we've tried at least once
_last_init_attempt: float = 0.0       # monotonic timestamp of last attempt
_RETRY_COOLDOWN_SEC: float = 30.0     # wait before retrying after a failure


def _intellect_home() -> str:
    """Return the intellect home directory (shared with ontology loading)."""
    return os.environ.get(
        "INTELLECT_HOME", str(Path.home() / ".intellect")
    )


def _init_manager(scope: str = "auto") -> None:
    """Build the per-process ``GraphitiClientManager`` (idempotent, retry-safe).

    Called once at MCP server startup so tools don't pay the init cost
    on every call.  Allows one retry after a cooldown when the first
    attempt failed (transient FalkorDB outage, network blip, etc.).
    """
    global _manager, _manager_scope, _manager_ok, _manager_message
    global _init_attempted, _last_init_attempt

    # Already running — nothing to do.
    if _manager_ok:
        return

    _manager_scope = scope

    # If we've failed before, enforce a cooldown before retrying so we
    # don't hammer a recovering database.
    if _init_attempted and not _manager_ok:
        elapsed = time.monotonic() - _last_init_attempt
        if elapsed < _RETRY_COOLDOWN_SEC:
            return
        logger.info(
            "graphiti mcp: retrying manager init (%.0fs since last attempt)",
            elapsed,
        )

    _init_attempted = True
    _last_init_attempt = time.monotonic()

    try:
        from .config import load_config

        cfg = load_config()
    except Exception as exc:
        _manager = None
        _manager_ok = False
        _manager_message = f"config load failed: {exc}"
        logger.error("graphiti mcp: %s", _manager_message)
        return

    try:
        from .client import GraphitiClientManager
        from .ontology import load_ontology

        # Phase 5.1: pick up custom entity/edge types from ontology.yaml.
        # Same logic as the agent path in __init__.py:initialize().
        ontology = load_ontology(_intellect_home())
        ontology_kwargs = ontology.as_add_episode_kwargs()
        if not ontology.is_empty():
            logger.info(
                "graphiti mcp: loaded ontology from %s (%d entities, %d edges, %d edge-map entries)",
                ontology.source,
                len(ontology.entities),
                len(ontology.edges),
                len(ontology.edge_type_map),
            )

        mgr = GraphitiClientManager(cfg, ontology_kwargs=ontology_kwargs)
        # MCP servers are scoped locally — member context comes from the
        # CLI profile or defaults to global.
        member_id: str = ""
        try:
            from intellect_cli.member_session import current_member_id
            member_id = current_member_id() or ""
        except Exception:
            pass
        mgr.bind_scope(member_id=member_id or None, team_id=None, project_id=None)
    except ImportError as exc:
        _manager = None
        _manager_ok = False
        _manager_message = (
            f"graphiti-core not installed — "
            f"run `uv pip install 'intellect-agent[graphiti]'`: {exc}"
        )
        logger.error("graphiti mcp: %s", _manager_message)
        return
    except Exception as exc:
        _manager = None
        _manager_ok = False
        _manager_message = f"manager init failed: {exc}"
        logger.error("graphiti mcp: %s", _manager_message)
        return

    _manager = mgr
    _manager_ok = True
    _manager_message = ""
    logger.info("graphiti mcp: manager ready (scope=%s)", scope)


def _get_manager() -> Any:
    """Return the cached manager, or raise ``RuntimeError`` with a
    human-readable message when deps/config are missing.

    Retries init on transient failures (respects cooldown)."""
    if not _manager_ok:
        _init_manager(_manager_scope)
    if not _manager_ok:
        raise RuntimeError(_manager_message or "graphiti manager unavailable")
    return _manager


async def _call_sync(func, *args, **kwargs) -> Any:
    """Run a blocking GraphitiClientManager method in a thread pool.

    The manager's sync methods block on ``_LOOP.submit(coro)``, which
    waits for the background asyncio loop.  Running those in the current
    async context would block the MCP event loop, so we punt them to a
    thread.
    """
    return await asyncio.to_thread(func, *args, **kwargs)


# ---------------------------------------------------------------------------
# FastMCP server factory
# ---------------------------------------------------------------------------

# Tool descriptions sourced from tools.py so they stay in sync with the
# agent-facing schemas automatically.

from .tools import (
    ADD_EPISODE_SCHEMA,
    SEARCH_FACTS_SCHEMA,
    SEARCH_NODES_SCHEMA,
    GET_NODE_TIMELINE_SCHEMA,
    DELETE_EPISODE_SCHEMA,
)

_ADD_EPISODE_DESC = ADD_EPISODE_SCHEMA["description"]
_SEARCH_FACTS_DESC = SEARCH_FACTS_SCHEMA["description"]
_SEARCH_NODES_DESC = SEARCH_NODES_SCHEMA["description"]
_TIMELINE_DESC = GET_NODE_TIMELINE_SCHEMA["description"]
_DELETE_DESC = DELETE_EPISODE_SCHEMA["description"]


def create_graphiti_mcp(scope: str = "auto") -> Any:
    """Build a ``FastMCP`` server with the 5 graphiti tools registered.

    ``scope`` controls which graphs are read by search/timeline tools:
    ``auto`` (member + team), ``member``, ``team``, ``project``, or ``all``.
    Writes always go to the member graph (or global).

    Returns a ``FastMCP`` instance.  Call ``mcp.run_stdio_async()`` to
    start serving on stdin/stdout.
    """
    from mcp.server.fastmcp import FastMCP  # type: ignore

    _init_manager(scope)

    mcp = FastMCP(
        name="graphiti",
        instructions=(
            "Graphiti — bi-temporal knowledge graph for intellect agent.  "
            "Each episode records facts about entities (people, projects, "
            "concepts) with validity windows (when-said / when-valid).  "
            "Use add_episode to persist new knowledge; search_facts / "
            "search_nodes to recall; get_node_timeline to see change over "
            "time; delete_episode to remove stale data (with audit reason)."
        ),
    )

    # -- graphiti_add_episode -------------------------------------------------

    @mcp.tool(name="graphiti_add_episode", description=_ADD_EPISODE_DESC)
    async def add_episode(
        content: str,
        source_description: str = "mcp",
        reference_time: str | None = None,
    ) -> str:
        mgr = _get_manager()
        result = await _call_sync(
            mgr.add_episode,
            content=content,
            source_description=source_description,
            reference_time=reference_time,
        )
        return json.dumps(result)

    # -- graphiti_search_facts -------------------------------------------------

    @mcp.tool(name="graphiti_search_facts", description=_SEARCH_FACTS_DESC)
    async def search_facts(
        query: str,
        max_results: int = 10,
        scope: str = "auto",
    ) -> str:
        mgr = _get_manager()
        facts = await _call_sync(
            mgr.search_facts,
            query=query,
            max_results=max_results,
            scope=scope,
        )
        return json.dumps(facts)

    # -- graphiti_search_nodes -------------------------------------------------

    @mcp.tool(name="graphiti_search_nodes", description=_SEARCH_NODES_DESC)
    async def search_nodes(
        query: str,
        max_results: int = 10,
        scope: str = "auto",
    ) -> str:
        mgr = _get_manager()
        nodes = await _call_sync(
            mgr.search_nodes,
            query=query,
            max_results=max_results,
            scope=scope,
        )
        return json.dumps(nodes)

    # -- graphiti_get_node_timeline ---------------------------------------------

    @mcp.tool(name="graphiti_get_node_timeline", description=_TIMELINE_DESC)
    async def get_node_timeline(
        node_id: str,
        since: str | None = None,
        until: str | None = None,
    ) -> str:
        mgr = _get_manager()
        records = await _call_sync(
            mgr.get_node_timeline,
            node_id=node_id,
            since=since,
            until=until,
        )
        return json.dumps(records)

    # -- graphiti_delete_episode -----------------------------------------------

    @mcp.tool(name="graphiti_delete_episode", description=_DELETE_DESC)
    async def delete_episode(episode_id: str, reason: str) -> str:
        mgr = _get_manager()
        # reason is required for the audit trail — matches the agent tool
        # contract.  The manager doesn't store reason itself, so we
        # include it in the returned result for observability.
        logger.info(
            "graphiti mcp: delete episode %s reason=%s", episode_id, reason
        )
        result = await _call_sync(
            mgr.delete_episode, episode_id=episode_id
        )
        result["audit_reason"] = reason
        return json.dumps(result)

    return mcp


async def start_mcp_server(scope: str = "auto") -> None:
    """Build and run the MCP server on stdin/stdout.

    Blocks until the client disconnects.
    """
    mcp = create_graphiti_mcp(scope=scope)
    await mcp.run_stdio_async()


# ---------------------------------------------------------------------------
# MCP client config generators (``intellect graphiti mcp config``)
# ---------------------------------------------------------------------------

def _intellect_path() -> str:
    """Return the path to the ``intellect`` CLI binary.

    Prefer ``INTELLECT_BIN`` if set, otherwise search PATH.
    """
    explicit = os.environ.get("INTELLECT_BIN")
    if explicit:
        return explicit
    import shutil
    found = shutil.which("intellect")
    return found or "intellect"


def _mcp_client_entry(scope: str = "auto") -> Dict[str, Any]:
    """Return the MCP client config entry for this server."""
    return {
        "command": _intellect_path(),
        "args": ["graphiti", "mcp", "start", "--scope", scope],
    }


def render_mcp_config(scope: str = "auto") -> str:
    """Render MCP client config JSON for all supported editors.

    Returns a multi-section string with copy-paste-ready JSON snippets
    for Claude Desktop, Cursor, and VS Code.
    """
    entry = _mcp_client_entry(scope)
    bin_path = _intellect_path()
    home = _intellect_home()

    lines: list[str] = []
    lines.append("  Graphiti MCP Client Configuration")
    lines.append("")
    lines.append(f"  intellect binary : {bin_path}")
    lines.append(f"  intellect home   : {home}")
    lines.append(f"  scope            : {scope}")
    lines.append("")

    # -- Claude Desktop -------------------------------------------------------
    lines.append("  ── Claude Desktop ──")
    lines.append("  (macOS: ~/Library/Application Support/Claude/claude_desktop_config.json)")
    lines.append("  (Windows: %APPDATA%\\Claude\\claude_desktop_config.json)")
    lines.append("")
    claude = {
        "mcpServers": {
            f"graphiti-{scope}": entry,
        },
    }
    for line in json.dumps(claude, indent=4).splitlines():
        lines.append(f"    {line}")
    lines.append("")

    # -- Cursor ---------------------------------------------------------------
    lines.append("  ── Cursor ──")
    lines.append("  (~/.cursor/mcp.json)")  # simplified — Cursor uses .cursor/mcp.json
    lines.append("")
    cursor = {
        "mcpServers": {
            f"graphiti-{scope}": entry,
        },
    }
    for line in json.dumps(cursor, indent=4).splitlines():
        lines.append(f"    {line}")
    lines.append("")

    # -- VS Code / Copilot ----------------------------------------------------
    lines.append("  ── VS Code (GitHub Copilot) ──")
    lines.append("  (.vscode/mcp.json in workspace root)")
    lines.append("")
    vscode = {
        "servers": {
            f"graphiti-{scope}": {
                "type": "stdio",
                "command": entry["command"],
                "args": entry["args"],
            },
        },
    }
    for line in json.dumps(vscode, indent=4).splitlines():
        lines.append(f"    {line}")
    lines.append("")

    return "\n".join(lines)
