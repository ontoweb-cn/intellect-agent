"""LightRAG MCP server — expose read/write RAG tools over stdio MCP.

Usage::

    intellect lightrag mcp start --scope auto

Dependency note: imports ``mcp`` — load only via ``intellect lightrag mcp start``.
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

_manager: Any = None
_manager_scope: str = "auto"
_manager_ok: bool = False
_manager_message: str = ""
_init_attempted: bool = False
_last_init_attempt: float = 0.0
_RETRY_COOLDOWN_SEC: float = 30.0


def _intellect_home() -> str:
    return os.environ.get("INTELLECT_HOME", str(Path.home() / ".intellect"))


def _init_manager(scope: str = "auto") -> None:
    global _manager, _manager_scope, _manager_ok, _manager_message
    global _init_attempted, _last_init_attempt

    if _manager_ok:
        return

    _manager_scope = scope

    if _init_attempted and not _manager_ok:
        elapsed = time.monotonic() - _last_init_attempt
        if elapsed < _RETRY_COOLDOWN_SEC:
            return
        logger.info(
            "lightrag mcp: retrying manager init (%.0fs since last attempt)",
            elapsed,
        )

    _init_attempted = True
    _last_init_attempt = time.monotonic()

    try:
        from .config import load_config
        from .client import LightRAGClientManager

        cfg = load_config(_intellect_home())
        base = (cfg.get("server") or {}).get("base_url", "")
        if not base or not str(base).strip():
            raise ValueError("server.base_url not configured")

        mgr = LightRAGClientManager(cfg)
        member_id = ""
        try:
            from intellect_cli.member_session import current_member_id
            member_id = current_member_id() or ""
        except Exception:
            pass
        mgr.bind_scope(member_id=member_id or None, team_id=None, project_id=None)
    except ImportError as exc:
        _manager = None
        _manager_ok = False
        _manager_message = f"lightrag client unavailable: {exc}"
        logger.error("lightrag mcp: %s", _manager_message)
        return
    except Exception as exc:
        _manager = None
        _manager_ok = False
        _manager_message = f"manager init failed: {exc}"
        logger.error("lightrag mcp: %s", _manager_message)
        return

    _manager = mgr
    _manager_ok = True
    _manager_message = ""
    logger.info("lightrag mcp: manager ready (scope=%s)", scope)


def _get_manager() -> Any:
    if not _manager_ok:
        _init_manager(_manager_scope)
    if not _manager_ok:
        raise RuntimeError(_manager_message or "lightrag manager unavailable")
    return _manager


async def _call_sync(func, *args, **kwargs) -> Any:
    return await asyncio.to_thread(func, *args, **kwargs)


from .tools import (
    INSERT_TEXT_SCHEMA,
    LIST_SCHEMA,
    QUERY_SCHEMA,
    SEARCH_SCHEMA,
    UPLOAD_SCHEMA,
)

_SEARCH_DESC = SEARCH_SCHEMA["description"]
_QUERY_DESC = QUERY_SCHEMA["description"]
_INSERT_DESC = INSERT_TEXT_SCHEMA["description"]
_UPLOAD_DESC = UPLOAD_SCHEMA["description"]
_LIST_DESC = LIST_SCHEMA["description"]


def create_lightrag_mcp(scope: str = "auto") -> Any:
    """Build a FastMCP server with LightRAG document RAG tools."""
    from mcp.server.fastmcp import FastMCP  # type: ignore

    _init_manager(scope)

    mcp = FastMCP(
        name="lightrag",
        instructions=(
            "LightRAG — graph-enhanced document RAG for intellect agent. "
            "Use lightrag_search for context chunks, lightrag_query for "
            "answers with references, lightrag_upload_document for files "
            "(optional multimodal parser hints), and lightrag_insert_text "
            "for short text fragments."
        ),
    )

    @mcp.tool(name="lightrag_search", description=_SEARCH_DESC)
    async def search(
        query: str,
        mode: str | None = None,
        scope: str = "auto",
    ) -> str:
        mgr = _get_manager()
        context = await _call_sync(
            mgr.search,
            query,
            scope=scope,
            mode=mode,
        )
        return json.dumps({"success": True, "context": context})

    @mcp.tool(name="lightrag_query", description=_QUERY_DESC)
    async def query(
        query: str,
        mode: str | None = None,
        scope: str = "auto",
        enable_rerank: bool | None = None,
    ) -> str:
        mgr = _get_manager()
        result = await _call_sync(
            mgr.query_answer,
            query,
            scope=scope,
            mode=mode,
            enable_rerank=enable_rerank,
        )
        return json.dumps({"success": True, "result": result})

    @mcp.tool(name="lightrag_list_documents", description=_LIST_DESC)
    async def list_documents(scope: str = "auto") -> str:
        mgr = _get_manager()
        result = await _call_sync(mgr.list_documents, scope=scope)
        return json.dumps({"success": True, "result": result})

    @mcp.tool(name="lightrag_insert_text", description=_INSERT_DESC)
    async def insert_text(
        text: str,
        file_path: str = "",
        scope: str = "auto",
    ) -> str:
        mgr = _get_manager()
        result = await _call_sync(
            mgr.insert_text,
            text,
            scope=scope,
            file_path=file_path,
        )
        return json.dumps({"success": True, "result": result})

    @mcp.tool(name="lightrag_upload_document", description=_UPLOAD_DESC)
    async def upload_document(
        file_path: str,
        scope: str = "auto",
        parse_engine: str | None = None,
        process_options: str | None = None,
        analyze_images: bool | None = None,
        analyze_tables: bool | None = None,
        analyze_equations: bool | None = None,
        chunking: str | None = None,
    ) -> str:
        mgr = _get_manager()
        result = await _call_sync(
            mgr.upload_document,
            file_path,
            scope=scope,
            parse_engine=parse_engine,
            process_options=process_options,
            analyze_images=analyze_images,
            analyze_tables=analyze_tables,
            analyze_equations=analyze_equations,
            chunking=chunking,
        )
        return json.dumps({"success": True, "result": result})

    return mcp


async def start_mcp_server(scope: str = "auto") -> None:
    mcp = create_lightrag_mcp(scope=scope)
    await mcp.run_stdio_async()


def _intellect_path() -> str:
    explicit = os.environ.get("INTELLECT_BIN")
    if explicit:
        return explicit
    import shutil
    return shutil.which("intellect") or "intellect"


def _mcp_client_entry(scope: str = "auto") -> Dict[str, Any]:
    return {
        "command": _intellect_path(),
        "args": ["lightrag", "mcp", "start", "--scope", scope],
    }


def render_mcp_config(scope: str = "auto") -> str:
    entry = _mcp_client_entry(scope)
    bin_path = _intellect_path()
    home = _intellect_home()

    lines: list[str] = []
    lines.append("  LightRAG MCP Client Configuration")
    lines.append("")
    lines.append(f"  intellect binary : {bin_path}")
    lines.append(f"  intellect home   : {home}")
    lines.append(f"  scope            : {scope}")
    lines.append("")

    lines.append("  ── Claude Desktop ──")
    lines.append("  (macOS: ~/Library/Application Support/Claude/claude_desktop_config.json)")
    lines.append("")
    claude = {"mcpServers": {f"lightrag-{scope}": entry}}
    for line in json.dumps(claude, indent=4).splitlines():
        lines.append(f"    {line}")
    lines.append("")

    lines.append("  ── Cursor ──")
    lines.append("  (~/.cursor/mcp.json)")
    lines.append("")
    cursor = {"mcpServers": {f"lightrag-{scope}": entry}}
    for line in json.dumps(cursor, indent=4).splitlines():
        lines.append(f"    {line}")
    lines.append("")

    lines.append("  ── VS Code (GitHub Copilot) ──")
    lines.append("  (.vscode/mcp.json in workspace root)")
    lines.append("")
    vscode = {
        "servers": {
            f"lightrag-{scope}": {
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
