"""URL safety checks — blocks requests to private/internal network addresses.

Prevents SSRF (Server-Side Request Forgery) where a malicious prompt or
skill could trick the agent into fetching internal resources like cloud
metadata endpoints (169.254.169.254), localhost services, or private
network hosts.

The check can be globally disabled via ``security.allow_private_urls: true``
in config.yaml for environments where DNS resolves external domains to
private/benchmark-range IPs (OpenWrt routers, corporate proxies, VPNs
that use 198.18.0.0/15 or 100.64.0.0/10).  Even when disabled, cloud
metadata hostnames (metadata.google.internal, 169.254.169.254) are
**always** blocked — those are never legitimate agent targets.

Connect-time validation (DNS rebinding / TOCTOU):
  Use ``tools.safe_http.safe_httpx_async_client`` / ``safe_httpx_client`` for
  agent-controlled HTTP fetches.  These validate the actual TCP peer IP after
  connect via ``is_safe_peer_ip()``, closing the gap where pre-flight DNS
  returns a public address but the live connection lands on a private IP.

Pre-flight-only limitations:
  - Redirect-based bypass is mitigated by httpx event hooks that re-validate
    each redirect target in vision_tools, gateway platform adapters, and
    media cache helpers. Web tools use third-party SDKs (Firecrawl/Tavily)
    where redirect handling is on their servers.
"""

import ipaddress
import logging
import os
import socket
from urllib.parse import urlparse

from utils import is_truthy_value

logger = logging.getLogger(__name__)

from intellect_rust import rust_is_ip_blocked as _rust_is_ip_blocked

# Hostnames that should always be blocked regardless of IP resolution
# or any config toggle.  These are cloud metadata endpoints that an
# attacker could use to steal instance credentials.
_BLOCKED_HOSTNAMES = frozenset({
    "metadata.google.internal",
    "metadata.goog",
})

# IPs and networks that should always be blocked regardless of the
# allow_private_urls toggle.  These are cloud metadata / credential
# endpoints — the #1 SSRF target — and the link-local range where
# they all live.
#
# IPv4-mapped IPv6 variants are included because DNS resolvers may
# return ``::ffff:x.x.x.x`` for IPv4-only hosts, and Python's
# ipaddress module treats these as distinct from the plain IPv4
# address (they won't match ``ip in frozenset`` or ``ip in network``).
_ALWAYS_BLOCKED_IPS = frozenset({
    ipaddress.ip_address("169.254.169.254"),  # AWS/GCP/Azure/DO/Oracle metadata
    ipaddress.ip_address("169.254.170.2"),     # AWS ECS task metadata (task IAM creds)
    ipaddress.ip_address("169.254.169.253"),   # Azure IMDS wire server
    ipaddress.ip_address("fd00:ec2::254"),     # AWS metadata (IPv6)
    ipaddress.ip_address("100.100.100.200"),   # Alibaba Cloud metadata
    # IPv4-mapped IPv6 variants — same endpoints reachable via ::ffff:x.x.x.x
    ipaddress.ip_address("::ffff:169.254.169.254"),
    ipaddress.ip_address("::ffff:169.254.170.2"),
    ipaddress.ip_address("::ffff:169.254.169.253"),
    ipaddress.ip_address("::ffff:100.100.100.200"),
})
_ALWAYS_BLOCKED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),    # Entire link-local range (no legit agent target)
    ipaddress.ip_network("::ffff:169.254.0.0/112"), # IPv4-mapped link-local range
)

# Exact HTTPS hostnames allowed to resolve to private/benchmark-space IPs.
# This is intentionally narrow: QQ media downloads can legitimately resolve
# to 198.18.0.0/15 behind local proxy/benchmark infrastructure.
_TRUSTED_PRIVATE_IP_HOSTS = frozenset({
    "multimedia.nt.qq.com.cn",
})

# 100.64.0.0/10 (CGNAT / Shared Address Space, RFC 6598) is NOT covered by
# ipaddress.is_private — it returns False for both is_private and is_global.
# Must be blocked explicitly. Used by carrier-grade NAT, Tailscale/WireGuard
# VPNs, and some cloud internal networks.
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# ---------------------------------------------------------------------------
# Global toggle: allow private/internal IP resolution
# ---------------------------------------------------------------------------
# Cached after first read so we don't hit the filesystem on every URL check.
_allow_private_resolved = False
_cached_allow_private: bool = False


def _global_allow_private_urls() -> bool:
    """Return True when the user has opted out of private-IP blocking.

    Checks (in priority order):
    1. ``intellect_ALLOW_PRIVATE_URLS`` env var  (``true``/``1``/``yes``)
    2. ``security.allow_private_urls`` in config.yaml
    3. ``browser.allow_private_urls`` in config.yaml  (legacy / backward compat)

    Result is cached for the process lifetime.
    """
    global _allow_private_resolved, _cached_allow_private
    if _allow_private_resolved:
        return _cached_allow_private

    _allow_private_resolved = True
    _cached_allow_private = False  # safe default

    # 1. Env var override (highest priority)
    env_val = os.getenv("intellect_ALLOW_PRIVATE_URLS", "").strip().lower()
    if env_val in {"true", "1", "yes"}:
        _cached_allow_private = True
        return _cached_allow_private
    if env_val in {"false", "0", "no"}:
        # Explicit false — don't fall through to config
        return _cached_allow_private

    # 2. Config file
    try:
        from intellect_cli.config import read_raw_config
        cfg = read_raw_config()
        # security.allow_private_urls (preferred)
        sec = cfg.get("security", {})
        if isinstance(sec, dict) and is_truthy_value(
            sec.get("allow_private_urls"), default=False
        ):
            _cached_allow_private = True
            return _cached_allow_private
        # browser.allow_private_urls (legacy fallback)
        browser = cfg.get("browser", {})
        if isinstance(browser, dict) and is_truthy_value(
            browser.get("allow_private_urls"), default=False
        ):
            _cached_allow_private = True
            return _cached_allow_private
    except Exception:
        # Config unavailable (e.g. tests, early import) — keep default
        pass

    return _cached_allow_private


def _reset_allow_private_cache() -> None:
    """Reset the cached toggle — only for tests."""
    global _allow_private_resolved, _cached_allow_private
    _allow_private_resolved = False
    _cached_allow_private = False


def is_safe_peer_ip(ip_str: str) -> bool:
    """Return True if a connected socket's peer IP is allowed for outbound fetch.

    Used at TCP connect time (see ``tools.safe_http``) to close the DNS-rebinding
    TOCTOU gap left by pre-flight ``is_safe_url``.  Cloud metadata addresses are
    always blocked.  Private ranges follow ``security.allow_private_urls``.
    """
    if not ip_str:
        return False
    # Strip IPv6 zone id (e.g. fe80::1%en0)
    normalized = ip_str.split("%", 1)[0].strip()
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return False

    if ip in _ALWAYS_BLOCKED_IPS or any(ip in net for net in _ALWAYS_BLOCKED_NETWORKS):
        return False

    if _global_allow_private_urls():
        return True

    return not _is_blocked_ip(ip)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if the IP should be blocked for SSRF protection."""
    return _rust_is_ip_blocked(str(ip)) is not None


def is_always_blocked_url(url: str) -> bool:
    """Return True when the URL targets an always-blocked endpoint.

    This is the security floor — cloud metadata IPs / hostnames
    (169.254.169.254, metadata.google.internal, ECS task metadata, etc.)
    that have no legitimate agent use regardless of backend, routing, or
    the ``allow_private_urls`` toggle.  Used by callers that bypass the
    full ``is_safe_url`` check for their own reasons (e.g. hybrid cloud
    browser routing to a local Chromium sidecar for private URLs) and
    still need to enforce the non-negotiable floor before letting the
    request proceed.

    Returns True (= blocked) on:
      - Hostnames in ``_BLOCKED_HOSTNAMES``
      - IPs / networks in ``_ALWAYS_BLOCKED_IPS`` / ``_ALWAYS_BLOCKED_NETWORKS``
      - URLs whose hostname resolves to any of the above

    Returns False (= not in the always-blocked floor) on:
      - Benign public / private / loopback URLs (whether or not they'd
        be blocked by the ordinary SSRF check)
      - DNS-resolution failures for non-sentinel hostnames (these are
        someone else's problem — the caller's ordinary fail-closed path
        will catch them if applicable)
      - Parse errors (caller decides fail-open vs fail-closed)

    Intentionally narrower than ``is_safe_url``: only blocks the sentinel
    set, not ordinary private addresses.  Callers that want the full
    SSRF check should still use ``is_safe_url``.
    """
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        if not hostname:
            return False

        # Blocked-hostname check fires regardless of DNS resolution
        if hostname in _BLOCKED_HOSTNAMES:
            logger.warning(
                "Blocked request to internal hostname (always-blocked floor): %s",
                hostname,
            )
            return True

        # Literal IP → check directly against the always-blocked set
        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            ip = None

        if ip is not None:
            if ip in _ALWAYS_BLOCKED_IPS or any(
                ip in net for net in _ALWAYS_BLOCKED_NETWORKS
            ):
                logger.warning(
                    "Blocked request to cloud metadata address "
                    "(always-blocked floor): %s",
                    hostname,
                )
                return True
            return False

        # Hostname → resolve and check every answer.  DNS failure is NOT
        # always-blocked (caller's ordinary path handles that).
        try:
            addr_info = socket.getaddrinfo(
                hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
        except socket.gaierror:
            return False

        for _family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                resolved = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if resolved in _ALWAYS_BLOCKED_IPS or any(
                resolved in net for net in _ALWAYS_BLOCKED_NETWORKS
            ):
                logger.warning(
                    "Blocked request to cloud metadata address "
                    "(always-blocked floor): %s -> %s",
                    hostname,
                    ip_str,
                )
                return True

        return False

    except Exception as exc:
        # Parse failures or unexpected errors — don't claim the URL is
        # always-blocked.  Caller decides what to do with a malformed URL.
        logger.debug("is_always_blocked_url error for %s: %s", url, exc)
        return False


def _allows_private_ip_resolution(hostname: str, scheme: str) -> bool:
    """Return True when a trusted HTTPS hostname may bypass IP-class blocking."""
    return scheme == "https" and hostname in _TRUSTED_PRIVATE_IP_HOSTS


def is_safe_url(url: str) -> bool:
    """Return True if the URL target is not a private/internal address.

    Resolves the hostname to an IP and checks against private ranges.
    Fails closed: DNS errors and unexpected exceptions block the request.

    When ``security.allow_private_urls`` is enabled (or the env var
    ``intellect_ALLOW_PRIVATE_URLS=true``), private-IP blocking is skipped.
    Cloud metadata endpoints (169.254.169.254, metadata.google.internal)
    remain blocked regardless — they are never legitimate agent targets.

    SECURITY NOTE: DNS resolution here is a pre-flight check only.
    There is an inherent TOCTOU window between this check and the actual
    TCP connect. The definitive guard is in ``tools.safe_http``, which
    validates the actual peer address at connect time via
    ``safe_async_http_transport``. All HTTP fetches MUST use
    ``safe_httpx_client`` or ``safe_async_http_transport`` — never a
    bare ``httpx.Client`` / ``httpx.AsyncClient``.
    """
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        scheme = (parsed.scheme or "").strip().lower()
        if scheme not in {"http", "https"}:
            logger.warning("Blocked request — unsupported URL scheme: %s", scheme or "<empty>")
            return False
        if not hostname:
            return False

        # Block known internal hostnames — ALWAYS, even with toggle on
        if hostname in _BLOCKED_HOSTNAMES:
            logger.warning("Blocked request to internal hostname: %s", hostname)
            return False

        # Check the global toggle AFTER blocking metadata hostnames
        allow_all_private = _global_allow_private_urls()

        allow_private_ip = _allows_private_ip_resolution(hostname, scheme)

        # Try to resolve and check IP
        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            # DNS resolution failed — fail closed. If DNS can't resolve it,
            # the HTTP client will also fail, so blocking loses nothing.
            logger.warning("Blocked request — DNS resolution failed for: %s", hostname)
            return False

        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue

            # Always block cloud metadata IPs and link-local, even with toggle on
            if ip in _ALWAYS_BLOCKED_IPS or any(ip in net for net in _ALWAYS_BLOCKED_NETWORKS):
                logger.warning(
                    "Blocked request to cloud metadata address: %s -> %s",
                    hostname, ip_str,
                )
                return False

            if not allow_all_private and not allow_private_ip and _is_blocked_ip(ip):
                logger.warning(
                    "Blocked request to private/internal address: %s -> %s",
                    hostname, ip_str,
                )
                return False

        if allow_all_private:
            logger.debug(
                "Allowing private/internal resolution (security.allow_private_urls=true): %s",
                hostname,
            )
        elif allow_private_ip:
            logger.debug(
                "Allowing trusted hostname despite private/internal resolution: %s",
                hostname,
            )

        return True

    except Exception as exc:
        # Fail closed on unexpected errors — don't let parsing edge cases
        # become SSRF bypass vectors
        logger.warning("Blocked request — URL safety check error for %s: %s", url, exc)
        return False
