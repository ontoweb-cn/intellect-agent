"""Keenable keyless web search + fetch (G-15 fifth vendor).

Keenable (keenable.ai) is an independent web-search API whose /public
endpoints are KEYLESS BY DESIGN — the only required header is
``X-Keenable-Title`` (an app identifier; the per-IP anonymous pool is
capped at ~1000 requests/hour, per their llms.txt). Live-probed
2026-09-02:

- ``POST /v1/search/public`` {"query"} → results [{title, url, snippet}]
- ``GET  /v1/fetch/public?url=…``     → {url, title, content, description}

There is NO keyed mode — ``is_available()`` is therefore always False
and ``is_keyless_available()`` always True (the only keyless-only
provider; every other vendor keeps both modes).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.keenable.ai"
DEFAULT_APP_TITLE = "intellect-agent"


def _api_base() -> str:
    return os.getenv(
        "KEENABLE_KEYLESS_API_URL", DEFAULT_API_BASE
    ).rstrip("/")


def _app_title() -> str:
    return os.getenv("KEENABLE_APP_TITLE", DEFAULT_APP_TITLE)


def _get(endpoint: str, params: Optional[Dict[str, str]] = None,
         timeout: float = 30.0) -> Dict[str, Any]:
    import httpx

    response = httpx.get(
        f"{_api_base()}/{endpoint.lstrip('/')}",
        params=params,
        headers={"X-Keenable-Title": _app_title(),
                 "Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _post(endpoint: str, payload: Dict[str, Any],
          timeout: float = 30.0) -> Dict[str, Any]:
    import httpx

    response = httpx.post(
        f"{_api_base()}/{endpoint.lstrip('/')}",
        json=payload,
        headers={"X-Keenable-Title": _app_title(),
                 "Content-Type": "application/json",
                 "Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


class KeenableWebSearchProvider(WebSearchProvider):
    """Keenable — keyless-only web search + page fetch.

    The one provider where ``is_available()`` is False and
    ``is_keyless_available()`` is True by design: keenable has no keyed
    mode; its /public endpoints ARE the product.
    """

    @property
    def name(self) -> str:
        return "keenable"

    @property
    def display_name(self) -> str:
        return "Keenable (keyless)"

    def is_available(self) -> bool:
        # No keyed mode exists — keyless availability is the real signal.
        return False

    def is_keyless_available(self) -> bool:
        return True

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        try:
            payload = _post("v1/search/public",
                            {"query": query, "limit": min(max(limit, 1), 20)})
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        web = []
        for i, r in enumerate(payload.get("results") or []):
            web.append({
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "description": r.get("snippet") or r.get("description") or "",
                "position": i + 1,
            })
        return {"success": True, "data": {"web": web}}

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Page fetch via GET /v1/fetch/public?url=… (one URL per call)."""
        results: List[Dict[str, Any]] = []
        for url in urls:
            try:
                payload = _get("v1/fetch/public", params={"url": url})
                content = payload.get("content") or payload.get("markdown") or ""
                results.append({
                    "url": payload.get("url") or url,
                    "title": payload.get("title", ""),
                    "content": content,
                    "raw_content": content,
                    "metadata": {"sourceURL": payload.get("url") or url,
                                 "title": payload.get("title", "")},
                })
            except Exception as exc:
                results.append({"url": url, "error": str(exc)})
        return results
