"""Clear ImportError helpers for optional HTTP client dependencies in plugins."""

from __future__ import annotations

from typing import Any


def require_requests(requests_mod: Any, *, plugin: str) -> Any:
    if requests_mod is None:
        raise ImportError(
            f"The {plugin} plugin requires the 'requests' package. "
            "Install it with: pip install requests"
        )
    return requests_mod


def require_httpx(httpx_mod: Any, *, plugin: str) -> Any:
    if httpx_mod is None:
        raise ImportError(
            f"The {plugin} plugin requires the 'httpx' package. "
            "Install it with: pip install httpx"
        )
    return httpx_mod
