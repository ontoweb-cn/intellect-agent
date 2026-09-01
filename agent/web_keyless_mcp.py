"""Shared self-contained MCP JSON-RPC mini-client for keyless web vendors
(G-15 / A3-3 rollout). Used by the parallel (stateless) and exa (session)
keyless providers — the Hermes ``keyless_mcp.py`` "self-contained" pattern.

Two call shapes:

- :func:`mcp_call` — STATELESS: one POST ``tools/call`` per invocation,
  no handshake (Parallel's ``search.parallel.ai/mcp`` behaves this way —
  live-probed 2026-09-02).
- :func:`mcp_session_call` — SESSION: initialize → capture
  ``Mcp-Session-Id`` response header → ``notifications/initialized`` →
  ``tools/call`` (Exa's ``mcp.exa.ai/mcp`` requires this; live-probed).

Streamable-HTTP responses may be plain JSON or SSE-framed
(``event: message\\ndata: {json}``); both are unwrapped here.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "intellect-keyless", "version": "0.1"}


def _headers(session_id: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def _unwrap_sse(text: str) -> Optional[Dict[str, Any]]:
    """Extract the last JSON-RPC result from an SSE-framed body (or parse
    a plain JSON body directly)."""
    text = text.strip()
    if not text:
        return None
    if not text.startswith("event:") and not text.startswith("data:"):
        try:
            return json.loads(text)
        except ValueError:
            return None
    result: Optional[Dict[str, Any]] = None
    for line in text.splitlines():
        if line.startswith("data:"):
            raw = line[len("data:"):].strip()
            try:
                result = json.loads(raw)
            except ValueError:
                continue
    return result


def _post_json(
    endpoint: str, payload: Dict[str, Any], timeout: float,
    session_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """POST one JSON-RPC message; return (session_id, unwrapped result).

    ``session_id`` attaches the Mcp-Session-Id header for calls that belong
    to an established session (initialize captures it; subsequent calls
    must carry it or the server treats them as a new session).
    """
    response = httpx.post(
        endpoint,
        json=payload,
        headers=_headers(session_id),
        timeout=timeout,
    )
    response.raise_for_status()
    session_id = response.headers.get("mcp-session-id") or session_id
    return session_id, _unwrap_sse(response.text)


def _extract_text(result: Dict[str, Any]) -> str:
    """Flatten an MCP tools/call result into one text blob."""
    content = (result or {}).get("content") or []
    parts: List[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("text"):
            parts.append(str(block["text"]))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


def mcp_call(
    endpoint: str,
    tool: str,
    arguments: Dict[str, Any],
    timeout: float = 60.0,
) -> str:
    """Stateless MCP ``tools/call`` — one POST, no handshake.

    Returns the flattened tool output text. Raises on transport errors or
    a JSON-RPC error payload.
    """
    _session, result = _post_json(endpoint, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }, timeout)
    if result is None:
        raise RuntimeError(f"MCP call to {tool} returned no payload")
    if "error" in result:
        raise RuntimeError(f"MCP error: {result['error']}")
    return _extract_text(result.get("result") or {})


def mcp_session_call(
    endpoint: str,
    tool: str,
    arguments: Dict[str, Any],
    timeout: float = 60.0,
) -> str:
    """Session-mode MCP ``tools/call`` (initialize → session header →
    initialized → tools/call). Returns the flattened tool output text.
    """
    session_id, init = _post_json(endpoint, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": _CLIENT_INFO,
        },
    }, timeout)
    if init is None:
        raise RuntimeError(f"MCP initialize failed at {endpoint}")
    if not session_id:
        # Some servers return the session id inside the initialize result.
        session_id = ((init.get("result") or {}).get("sessionId")) or None

    # Initialized notification (no id — must not produce a response).
    httpx.post(endpoint, json={
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }, headers=_headers(session_id), timeout=timeout)

    _s, result = _post_json(endpoint, {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }, timeout, session_id=session_id)
    if result is None:
        raise RuntimeError(f"MCP call to {tool} returned no payload")
    if "error" in result:
        raise RuntimeError(f"MCP error: {result['error']}")
    return _extract_text(result.get("result") or {})
