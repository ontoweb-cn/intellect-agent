"""Built-in SearXNG web search (P3-4).

Provides a lightweight, self-hosted web search capability via the
SearXNG meta-search engine.  No API key required — users run their
own SearXNG instance or point at a shared one.

Configuration lives in config.yaml:
    features:
      searxng_enabled: true
      searxng_url: "https://searxng.example.com"
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_SEARXNG_TIMEOUT = 10.0
_MAX_RESULTS = 10


async def search_searxng(query: str, base_url: str) -> dict[str, Any]:
    """Query a SearXNG instance and return structured results.

    Returns ``{"results": [...], "error": None}`` on success, or
    ``{"results": [], "error": "message"}`` on failure.
    """
    url = base_url.rstrip("/") + "/search"
    try:
        async with httpx.AsyncClient(timeout=_SEARXNG_TIMEOUT) as client:
            resp = await client.get(
                url,
                params={"q": query, "format": "json", "categories": "general"},
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])[:_MAX_RESULTS]
            return {
                "results": [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "content": r.get("content", ""),
                        "engine": r.get("engine", ""),
                    }
                    for r in results
                ],
                "error": None,
            }
    except httpx.TimeoutException:
        return {"results": [], "error": "SearXNG request timed out"}
    except httpx.HTTPStatusError as exc:
        return {"results": [], "error": f"SearXNG returned HTTP {exc.response.status_code}"}
    except ValueError:
        # Non-JSON response (proxy landing page, maintenance page, etc.) (F8)
        return {"results": [], "error": "SearXNG returned an unexpected response format"}
    except Exception as exc:
        logger.debug("SearXNG search failed: %s", exc)
        # F6: don't leak internal IPs/hostnames from httpx exception messages
        return {"results": [], "error": "SearXNG request failed — check the server URL and connectivity"}


def get_searxng_config(config: dict | None = None) -> dict[str, Any]:
    """Read SearXNG settings from config.yaml."""
    cfg = config or {}
    features = cfg.get("features") if isinstance(cfg.get("features"), dict) else {}
    return {
        "enabled": bool(features.get("searxng_enabled", False)),
        "url": str(features.get("searxng_url", "")).strip().rstrip("/"),
        "timeout": int(features.get("searxng_timeout_seconds", 10)),
    }


async def probe_searxng(base_url: str) -> dict[str, Any]:
    """Test connectivity to a SearXNG instance. Returns status dict."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                base_url.rstrip("/") + "/search",
                params={"q": "test", "format": "json"},
            )
            resp.raise_for_status()
            data = resp.json()
            return {"ok": True, "version": data.get("version", "unknown")}
    except ValueError:
        return {"ok": False, "error": "Unexpected response from SearXNG instance"}
    except Exception:
        # F6: don't leak internal network details in error messages
        return {"ok": False, "error": "Could not connect to the SearXNG instance — verify the URL and network access"}
