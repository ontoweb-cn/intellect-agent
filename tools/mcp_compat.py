"""Compatibility layer for the MCP Python SDK v1 (1.x) and v2 (2.x).

The SDK made several breaking changes in v2 that this integration has to
straddle while the pin moves forward:

* ``mcp.server.fastmcp.FastMCP`` was renamed to
  ``mcp.server.mcpserver.MCPServer``.
* Every model field moved from camelCase to snake_case. The models still
  accept camelCase as *constructor* kwargs, but attribute access must use
  snake_case -- ``Tool(..., inputSchema=...)`` works, ``tool.inputSchema``
  does not.
* The client switched from ``httpx`` to ``httpx2`` (an API-compatible fork),
  so the transport must build an ``httpx2.AsyncClient``.
* ``mcp.client.streamable_http.streamablehttp_client`` was removed in favour
  of ``streamable_http_client``, whose context manager now yields a two-tuple
  (read/write streams) instead of three (the session-id callback is gone).

Importing the SDK is optional for this project, so nothing here imports the
SDK at module import time -- every accessor resolves lazily and degrades to
the v1 shape when the v2 modules are absent.
"""

from __future__ import annotations

import importlib.metadata
from typing import Any

__all__ = [
    "sdk_major_version",
    "is_v2",
    "server_class",
    "http_lib",
    "mcp_field",
    "supported_kwargs",
    "authorization_code_result",
    "extract_tool_result_text",
]

_MISSING = object()


def sdk_major_version() -> int:
    """Return the installed MCP SDK major version, or 0 when not installed."""
    try:
        raw = importlib.metadata.version("mcp")
    except Exception:
        return 0
    try:
        return int(raw.split(".", 1)[0])
    except (ValueError, IndexError):
        return 0


def is_v2() -> bool:
    """True when the installed MCP SDK is v2 or newer."""
    return sdk_major_version() >= 2


def server_class():
    """Return the SDK's high-level server class (MCPServer on v2, FastMCP on v1)."""
    try:
        from mcp.server.mcpserver import MCPServer

        return MCPServer
    except ImportError:
        from mcp.server.fastmcp import FastMCP

        return FastMCP


def http_lib():
    """Return the HTTP client library the installed SDK builds transports with.

    v2 uses ``httpx2``; v1 uses ``httpx``. ``httpx2`` is a fork with the same
    public surface (``AsyncClient``/``Timeout``/``URL``/``HTTPStatusError``),
    so call sites can treat the return value uniformly.
    """
    if is_v2():
        import httpx2

        return httpx2

    import httpx

    return httpx


def _camelize(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def mcp_field(obj: Any, name: str, default: Any = None) -> Any:
    """Read an MCP model field by its v2 (snake_case) ``name``.

    Falls back to the v1 camelCase spelling so the caller works against
    either SDK major version, and returns ``default`` when neither is
    present (e.g. a test double that omits the field entirely).
    """
    value = getattr(obj, name, _MISSING)
    if value is not _MISSING:
        return value
    camel = _camelize(name)
    if camel != name:
        value = getattr(obj, camel, _MISSING)
        if value is not _MISSING:
            return value
    return default


def supported_kwargs(target: Any, kwargs: dict) -> dict:
    """Drop keyword arguments ``target`` does not accept.

    The SDK has removed constructor parameters across majors (for example
    ``OAuthClientProvider(timeout=...)`` disappeared in v2). Passing them
    positionally is not an option for keyword-only params, so callers build
    the full kwargs and let this trim them to the installed version.

    For classes, the accepted names are collected across the MRO so a
    subclass that forwards ``**kwargs`` to its base still keeps the
    parameters its base supports (and loses those the base dropped).
    Anything not introspectable is passed through unchanged.
    """
    import inspect

    def _explicit_names(callable_obj: Any) -> tuple[set[str], bool]:
        """Return (explicit kwarg names, accepts_arbitrary_kwargs)."""
        try:
            params = inspect.signature(callable_obj).parameters
        except (TypeError, ValueError):
            return set(), True
        wildcard = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        names = {
            name
            for name, p in params.items()
            if p.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
            and name != "self"
        }
        return names, wildcard

    if inspect.isclass(target):
        # A subclass that forwards **kwargs to its base still only accepts
        # what some class in the chain names explicitly (the base rejects the
        # rest), so union the explicit params across the MRO.
        accepted: set[str] = set()
        for klass in target.__mro__:
            if klass is object:
                break
            init = klass.__dict__.get("__init__")
            if init is None:
                continue
            names, wildcard = _explicit_names(init)
            accepted |= names
            if wildcard and not accepted:
                return dict(kwargs)  # nothing introspectable; pass through
        return {k: v for k, v in kwargs.items() if k in accepted}

    names, wildcard = _explicit_names(target)
    if wildcard:
        return dict(kwargs)
    return {k: v for k, v in kwargs.items() if k in names}


def authorization_code_result(code: str, state: Any = None, iss: Any = None) -> Any:
    """Build the value an SDK OAuth ``callback_handler`` must return.

    mcp 1.x unpacks a ``(code, state)`` two-tuple; 2.x expects an
    ``AuthorizationCodeResult`` object exposing ``.code``/``.state``/``.iss``.
    Returns whichever shape the installed SDK consumes.
    """
    if is_v2():
        try:
            from mcp.shared.auth import AuthorizationCodeResult

            return AuthorizationCodeResult(code=code, state=state, iss=iss)
        except ImportError:  # pragma: no cover - defensive
            pass
    return code, state



def extract_tool_result_text(result: Any) -> str:
    """Extract the first text payload from an SDK server ``call_tool`` result.

    mcp 1.x's ``FastMCP.call_tool`` returns ``(content_blocks, meta)``; 2.x's
    ``MCPServer.call_tool`` returns a ``CallToolResult`` whose ``.content``
    holds the blocks. Handles both, plus a bare content list.
    """
    if isinstance(result, tuple):
        content = result[0]
    else:
        content = mcp_field(result, "content", result)
    if isinstance(content, str):
        return content
    for block in content or []:
        text = getattr(block, "text", None)
        if text is not None:
            return text
    return ""
