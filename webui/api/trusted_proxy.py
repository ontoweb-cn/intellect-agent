"""Trusted-proxy-first helpers for X-Forwarded-* / X-Real-* (W7 S5–S7).

- Unset env → trust loopback peers only (127.0.0.0/8, ::1).
- Explicit ``none`` or empty string → trust nothing.
"""

from __future__ import annotations

import ipaddress
import os
from functools import lru_cache
from typing import Optional

_ENV_KEY = "INTELLECT_WEBUI_TRUSTED_PROXIES"
_LOOPBACK_DEFAULT = ("127.0.0.0/8", "::1")
_SENTINEL_DEFAULT = "__loopback_default__"
_SENTINEL_NONE = "__none__"


@lru_cache(maxsize=8)
def _trusted_networks(raw: str) -> tuple:
    if raw == _SENTINEL_NONE:
        return tuple()
    if raw == _SENTINEL_DEFAULT:
        parts = _LOOPBACK_DEFAULT
    else:
        parts = tuple(p.strip() for p in raw.split(",") if p.strip())
        if not parts or all(p.lower() in ("none", "-") for p in parts):
            return tuple()
    out = []
    for item in parts:
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return tuple(out)


def clear_trusted_proxy_cache() -> None:
    _trusted_networks.cache_clear()


def trusted_proxy_cidrs() -> tuple:
    """Return configured trusted networks."""
    raw = os.getenv(_ENV_KEY)
    if raw is None:
        return _trusted_networks(_SENTINEL_DEFAULT)
    text = raw.strip()
    if not text or text.lower() in ("none", "-"):
        return _trusted_networks(_SENTINEL_NONE)
    return _trusted_networks(text)


def peer_ip(handler) -> Optional[ipaddress._BaseAddress]:
    try:
        raw = (handler.client_address or ("", 0))[0]
        return ipaddress.ip_address(raw)
    except Exception:
        return None


def is_trusted_proxy(handler) -> bool:
    peer = peer_ip(handler)
    if peer is None:
        return False
    for net in trusted_proxy_cidrs():
        try:
            if peer in net:
                return True
        except Exception:
            continue
    return False


def _first_header(handler, *names: str) -> str:
    headers = getattr(handler, "headers", None)
    if not headers:
        return ""
    for name in names:
        val = headers.get(name, "")
        if val and str(val).strip():
            return str(val).strip()
    return ""


def client_ip(handler) -> str:
    """Client IP: honor XFF / X-Real-IP only when peer is a trusted proxy."""
    peer = peer_ip(handler)
    peer_s = str(peer) if peer is not None else ""
    if is_trusted_proxy(handler):
        xff = _first_header(handler, "X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip() or peer_s
        xri = _first_header(handler, "X-Real-IP")
        if xri:
            return xri.split(",")[0].strip() or peer_s
    return peer_s


def request_scheme(handler) -> str:
    """http/https: honor X-Forwarded-Proto only when peer is trusted."""
    if is_trusted_proxy(handler):
        proto = _first_header(handler, "X-Forwarded-Proto").split(",", 1)[0].strip().lower()
        if proto in ("http", "https"):
            return proto
    try:
        if getattr(handler.request, "getpeercert", None) is not None:
            return "https"
    except Exception:
        pass
    return "http"


def request_host(handler) -> str:
    """Host for CSRF / absolute URLs: honor forwarded host only when trusted."""
    if is_trusted_proxy(handler):
        fwd = _first_header(handler, "X-Forwarded-Host", "X-Real-Host")
        if fwd:
            return fwd.split(",")[0].strip()
    return _first_header(handler, "Host") or "localhost"
