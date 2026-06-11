"""httpx clients with connect-time SSRF peer validation.

Complements ``tools.url_safety.is_safe_url`` (pre-flight DNS) by checking the
actual TCP peer address after the socket is connected, closing the DNS-rebinding
TOCTOU gap.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class SSRFConnectBlocked(OSError):
    """Raised when the remote peer IP fails connect-time SSRF validation."""


def _validate_stream_peer(stream: Any) -> None:
    from tools.url_safety import is_safe_peer_ip

    addr = stream.get_extra_info("server_addr")
    if not addr:
        return
    ip_str = str(addr[0])
    if not is_safe_peer_ip(ip_str):
        try:
            stream.close()
        except Exception:
            pass  # intentionally silent — cleanup/teardown path
        logger.warning("Blocked HTTP connection to unsafe peer address: %s", ip_str)
        raise SSRFConnectBlocked(
            f"Blocked connection to private or internal network address: {ip_str}"
        )


def _inject_safe_sync_backend(transport: httpx.HTTPTransport) -> httpx.HTTPTransport:
    from httpcore._backends.sync import SyncBackend

    class SafeSyncBackend(SyncBackend):
        def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            stream = super().connect_tcp(
                host, port, timeout=timeout, local_address=local_address, socket_options=socket_options
            )
            _validate_stream_peer(stream)
            return stream

    transport._pool._network_backend = SafeSyncBackend()
    return transport


def _inject_safe_async_backend(transport: httpx.AsyncHTTPTransport) -> httpx.AsyncHTTPTransport:
    from httpcore._backends.anyio import AnyIOBackend

    class SafeAsyncBackend(AnyIOBackend):
        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            stream = await super().connect_tcp(
                host, port, timeout=timeout, local_address=local_address, socket_options=socket_options
            )
            _validate_stream_peer(stream)
            return stream

    transport._pool._network_backend = SafeAsyncBackend()
    return transport


def safe_http_transport(**kwargs: Any) -> httpx.HTTPTransport:
    """Return an ``httpx.HTTPTransport`` that validates peer IPs on connect."""
    return _inject_safe_sync_backend(httpx.HTTPTransport(**kwargs))


def safe_async_http_transport(**kwargs: Any) -> httpx.AsyncHTTPTransport:
    """Return an ``httpx.AsyncHTTPTransport`` that validates peer IPs on connect."""
    return _inject_safe_async_backend(httpx.AsyncHTTPTransport(**kwargs))


def safe_httpx_client(**kwargs: Any) -> httpx.Client:
    """Construct a sync ``httpx.Client`` with connect-time SSRF validation."""
    if "transport" not in kwargs:
        kwargs["transport"] = safe_http_transport()
    return httpx.Client(**kwargs)


def safe_httpx_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """Construct an async ``httpx.AsyncClient`` with connect-time SSRF validation."""
    if "transport" not in kwargs:
        kwargs["transport"] = safe_async_http_transport()
    return httpx.AsyncClient(**kwargs)
