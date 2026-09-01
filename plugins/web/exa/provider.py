"""Exa web search + content extraction — plugin form.

Subclasses :class:`agent.web_search_provider.WebSearchProvider`. Uses the
official Exa SDK (``exa-py``) which is lazy-loaded via
:func:`tools.lazy_deps.ensure` so that cold-start CLI users don't pay the
SDK import cost when Exa isn't configured.

Config keys this provider responds to::

    web:
      search_backend: "exa"      # explicit per-capability
      extract_backend: "exa"     # explicit per-capability
      backend: "exa"             # shared fallback for both

Env var::

    EXA_API_KEY=...    # https://exa.ai (paid tier; free trial available)

The previous in-tree implementation lived at
``tools.web_tools._exa_search`` / ``_exa_extract``; this file is the
canonical replacement. Behavior is bit-for-bit identical aside from the
ABC method-name change.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

# Module-level note: the canonical ``_exa_client`` cache slot lives on
# :mod:`tools.web_tools` so tests that do ``tools.web_tools._exa_client =
# None`` between cases see fresh state. The plugin reads/writes through
# that public module (see :func:`_get_exa_client`).


def _get_exa_client() -> Any:
    """Lazy-import and cache an Exa SDK client.

    Cache lives on :mod:`tools.web_tools` (as ``_exa_client``) so unit
    tests that reset that name between cases keep working. Raises
    ``ValueError`` when ``EXA_API_KEY`` is unset.
    """
    import tools.web_tools as _wt

    cached = getattr(_wt, "_exa_client", None)
    if cached is not None:
        return cached

    api_key = os.getenv("EXA_API_KEY")
    if not api_key:
        raise ValueError(
            "EXA_API_KEY environment variable not set. "
            "Get your API key at https://exa.ai"
        )

    try:
        from tools.lazy_deps import ensure as _lazy_ensure

        _lazy_ensure("search.exa", prompt=False)
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 — lazy_deps surfaces install hints
        raise ImportError(str(exc))

    from exa_py import Exa  # noqa: WPS433 — deliberately lazy

    client = Exa(api_key=api_key)
    client.headers["x-exa-integration"] = "intellect-agent"
    _wt._exa_client = client
    return client


def _reset_client_for_tests() -> None:
    """Drop the cached Exa client so tests can re-instantiate cleanly."""
    import tools.web_tools as _wt

    _wt._exa_client = None


class ExaWebSearchProvider(WebSearchProvider):
    """Exa search + extract provider.

    Both methods are sync — Exa's SDK is sync-only. The web_extract_tool
    dispatcher wraps sync extracts via ``asyncio.to_thread`` when it
    needs to keep the event loop responsive.
    """

    @property
    def name(self) -> str:
        return "exa"

    @property
    def display_name(self) -> str:
        return "Exa"

    def is_keyless_available(self) -> bool:
        """G-15: Exa's hosted MCP server (mcp.exa.ai/mcp) serves keyless
        search/fetch over session-based streamable HTTP (live-probed
        2026-09-02 — initialize handshake HTTP 200, no credentials)."""
        return True

    def search_keyless(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Anonymous search via the session MCP client (``web_search_exa``).

        The tool returns a structured TEXT blob ("Title:/URL:/…Highlights:"
        per result) — parsed back into the standard data.web shape. If the
        blob shape ever changes, falls through to a single text-only entry
        so the caller still gets the content.
        """
        import os as _os

        from agent.web_keyless_mcp import mcp_session_call

        endpoint = _os.getenv("EXA_KEYLESS_MCP_URL", "https://mcp.exa.ai/mcp")
        text = mcp_session_call(
            endpoint, "web_search_exa",
            {"query": query, "numResults": min(max(limit, 1), 20)},
        )
        results: List[Dict[str, Any]] = []
        current: Dict[str, Any] = {}
        for line in text.splitlines():
            if line.startswith("Title: "):
                if current.get("url"):
                    results.append(dict(current))
                current = {"url": "", "title": line[len("Title: "):],
                           "description": ""}
                continue
            if not current:
                # text before any Title block — preamble, not a result
                continue
            if line.startswith("URL: "):
                current["url"] = line[len("URL: "):]
            elif line.strip():
                current["description"] = (
                    current["description"] + " " + line).strip()
        if current.get("url"):
            results.append(dict(current))
        if not results:
            results = [{"url": "", "title": "", "description": text,
                        "note": "text-only result"}]
        for i, r in enumerate(results[:limit]):
            r["position"] = i + 1
        return {"success": True, "data": {"web": results}}

    async def extract_keyless(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Anonymous page fetch via the session MCP client (``web_fetch_exa``)."""
        import asyncio

        from agent.web_keyless_mcp import mcp_session_call

        endpoint = os.getenv("EXA_KEYLESS_MCP_URL", "https://mcp.exa.ai/mcp")

        async def _fetch_one(url: str) -> Dict[str, Any]:
            try:
                text = await asyncio.to_thread(
                    mcp_session_call, endpoint, "web_fetch_exa", {"url": url},
                )
                return {"url": url, "title": "", "content": text,
                        "raw_content": text, "metadata": {"sourceURL": url}}
            except Exception as exc:
                return {"url": url, "error": str(exc)}

        return list(await asyncio.gather(*(_fetch_one(u) for u in urls)))

    def is_available(self) -> bool:
        """Return True when ``EXA_API_KEY`` is set to a non-empty value."""
        return bool(os.getenv("EXA_API_KEY", "").strip())

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Execute an Exa search.

        Returns ``{"success": True, "data": {"web": [{...}, ...]}}`` on
        success, ``{"success": False, "error": str}`` on failure (incl.
        missing API key and SDK install errors).
        """
        try:
            from tools.interrupt import is_interrupted

            if is_interrupted():
                return {"success": False, "error": "Interrupted"}

            logger.info("Exa search: '%s' (limit=%d)", query, limit)
            response = _get_exa_client().search(
                query,
                num_results=limit,
                contents={"highlights": True},
            )

            web_results = []
            for i, result in enumerate(response.results or []):
                highlights = result.highlights or []
                web_results.append(
                    {
                        "url": result.url or "",
                        "title": result.title or "",
                        "description": " ".join(highlights) if highlights else "",
                        "position": i + 1,
                    }
                )

            return {"success": True, "data": {"web": web_results}}
        except ValueError as exc:
            # Raised by _get_exa_client when EXA_API_KEY missing
            return {"success": False, "error": str(exc)}
        except ImportError as exc:
            return {"success": False, "error": f"Exa SDK not installed: {exc}"}
        except Exception as exc:  # noqa: BLE001 — surface as failure
            logger.warning("Exa search error: %s", exc)
            return {"success": False, "error": f"Exa search failed: {exc}"}

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Extract content from one or more URLs via Exa.

        Returns a list of result dicts shaped for the legacy LLM
        post-processing pipeline. On per-URL or whole-batch failure,
        results carry an ``error`` field rather than raising.
        """
        try:
            from tools.interrupt import is_interrupted

            if is_interrupted():
                return [
                    {"url": u, "error": "Interrupted", "title": ""} for u in urls
                ]

            logger.info("Exa extract: %d URL(s)", len(urls))
            response = _get_exa_client().get_contents(urls, text=True)

            results: List[Dict[str, Any]] = []
            for result in response.results or []:
                content = result.text or ""
                url = result.url or ""
                title = result.title or ""
                results.append(
                    {
                        "url": url,
                        "title": title,
                        "content": content,
                        "raw_content": content,
                        "metadata": {"sourceURL": url, "title": title},
                    }
                )
            return results
        except ValueError as exc:
            return [{"url": u, "title": "", "content": "", "error": str(exc)} for u in urls]
        except ImportError as exc:
            return [
                {"url": u, "title": "", "content": "", "error": f"Exa SDK not installed: {exc}"}
                for u in urls
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Exa extract error: %s", exc)
            return [
                {"url": u, "title": "", "content": "", "error": f"Exa extract failed: {exc}"}
                for u in urls
            ]

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Exa",
            "badge": "paid",
            "tag": "Semantic + neural web search with content extraction.",
            "env_vars": [
                {
                    "key": "EXA_API_KEY",
                    "prompt": "Exa API key",
                    "url": "https://exa.ai",
                },
            ],
        }
