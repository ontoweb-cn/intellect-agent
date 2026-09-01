"""Multiplex front end (B1-4/B1-5) — the supervisor's only listener.

Per the multiplex ADR option (a), the supervisor front end owns the ONLY
external listener. Children spawned with ``INTELLECT_MULTIPLEX_CHILD=1``
rebind their listener platforms (api_server / webhook) to internal loopback
ephemeral ports and report the resolved port via their control socket
``status``; the supervisor polls those into ``ProfileChild.listeners``.

Routing (per MP-04/MP-05):

- no prefix (``/v1/...``)          → the DEFAULT profile's child
- ``/p/<name>/...``                → child ``<name>``, prefix stripped
- invalid name / unknown profile   → 404 with a structured JSON error
- known profile, no bound port yet → 503 with a structured JSON error
- WebSocket upgrades               → routed the same way; routing failures
  reach the client as WS close 4404 (fail-closed, GW-302 lineage)

The front end is a secret-free byte pump: it holds no credentials, and each
child keeps enforcing its own API key / webhook secrets / WS token from its
own INTELLECT_HOME. Two sites are hosted when the corresponding platform is
enabled anywhere in the serve set — the api site (default profile's
configured api_server host/port) and the webhook site (so external webhook
providers keep pointing at one port while ``/p/<name>/webhooks/<route>``
fans out per profile).

aiohttp is imported lazily by the supervisor glue; if unavailable, the
supervisor degrades to the pre-B1-4 shape and this module never loads.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from aiohttp import ClientSession, ClientTimeout, web

from gateway.supervisor import (
    child_platforms,
    is_loopback_host,
    listener_binding,
)

logger = logging.getLogger("gateway.multiplex_front")

# Mirror the platform adapters' contractual defaults (plugins/platforms/
# api_server: 127.0.0.1:8642, plugins/platforms/webhook: 0.0.0.0:8644).
# Used only when the default profile enables a platform without pinning a
# binding of its own.
_SITE_DEFAULTS: Dict[str, Tuple[str, int]] = {
    "api": ("127.0.0.1", 8642),
    "webhook": ("0.0.0.0", 8644),
}
_PLATFORM_KINDS: Dict[str, str] = {
    "api_server": "api",
    "webhook": "webhook",
}

# hop-by-hop headers (RFC 7230 §6.1) must never be forwarded, plus Host —
# the aiohttp client derives it from the upstream URL.
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "trailers", "transfer-encoding", "upgrade",
    "host",
})

ERROR_UNREADY = "profile_unready"


# WS close codes that must never be re-sent on the wire: reserved or
# "received-only" diagnostics (1005/1006 are what a stack REPORTS, not what
# a peer SENDS). Anything else outside 1000-1011/3000-4999 is not sendable.
_WS_UNSENDABLE_CLOSE_CODES = frozenset({1004, 1005, 1006, 1015})


def _forwardable_close_code(code: Any) -> Optional[int]:
    """Normalize a received WS close code to one that is legal to send."""
    if code is None:
        return None
    try:
        code = int(code)
    except (TypeError, ValueError):
        return None
    if (
        code < 1000
        or code in _WS_UNSENDABLE_CLOSE_CODES
        or 1012 <= code <= 2999
    ):
        return None
    return code


def _error_response(
    status_code: int, code: str, message: str, profile: Optional[str] = None
) -> web.Response:
    """Structured, visible routing-rejection error (MP-03 acceptance)."""
    err: Dict[str, Any] = {"code": code, "message": message}
    if profile is not None:
        err["profile"] = profile
    return web.json_response({"error": err}, status=status_code)


def _split_profile_prefix(path: str) -> Tuple[Optional[str], str]:
    """Three-state ``/p/<profile>/`` parse (Hermes MP-04 semantics).

    Returns ``(None, path)`` for an unprefixed path, or ``(name, rest)`` —
    ``rest`` always starts with ``/``. Raises ``ValueError`` on a malformed
    profile segment (empty, or failing ``validate_profile_name``).
    """
    parts = path.split("/", 3)
    # "/p/<name>/..." → ['', 'p', name, 'rest/of/path']
    if len(parts) < 3 or parts[1] != "p":
        return (None, path)
    name = parts[2]
    if not name:
        raise ValueError("empty profile prefix")
    # The prefix may address the profile root itself: "/p/<name>" or
    # "/p/<name>/". maxsplit=3 keeps the remainder intact.
    rest = "/" + parts[3] if len(parts) > 3 and parts[3] else "/"
    try:
        from intellect_cli.profiles import (
            normalize_profile_name,
            validate_profile_name,
        )

        name = normalize_profile_name(name)
        validate_profile_name(name)
    except (ValueError, ImportError) as exc:
        raise ValueError(f"invalid profile prefix {name!r}") from exc
    return (name, rest)


class MultiplexFront:
    """HTTP + WebSocket front end owned by the supervisor (B1-4/B1-5)."""

    def __init__(
        self,
        supervisor: Any,
        sites: Optional[Sequence[Tuple[str, str, int]]] = None,
    ) -> None:
        self._sup = supervisor
        self._sites_override = list(sites) if sites is not None else None
        self._session: Optional[ClientSession] = None
        self._runners: List[web.AppRunner] = []
        self._stop_event: Any = None  # asyncio.Event, bound in run()
        self._loop: Any = None
        # Plain flag so a stop request that arrives BEFORE run() binds the
        # event/loop is never lost (request_stop must be safe to call from
        # a signal handler at any moment).
        self._stop_requested = False
        # kind -> (host, port) actually bound (port resolved when 0).
        self.bound: Dict[str, Tuple[str, int]] = {}

    # ── lifecycle ──────────────────────────────────────────────────────

    def request_stop(self) -> None:
        """Thread-safe stop (signal handler safe): just flips the flag."""
        self._stop_requested = True
        loop = self._loop
        event = self._stop_event
        if loop is not None and event is not None:
            try:
                loop.call_soon_threadsafe(event.set)
                return
            except RuntimeError:
                pass
        if event is not None:
            event.set()

    async def run(self) -> int:
        """Serve until :meth:`request_stop`. Returns a process exit code."""
        import asyncio

        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._session = ClientSession(
            # total=None: proxied model streams are legitimately long-lived.
            # sock_read bounds a silent upstream — frees the connection if a
            # child wedges (900s: generous for long first-token latencies).
            timeout=ClientTimeout(
                total=None, connect=10.0, sock_connect=10.0, sock_read=900.0
            )
        )
        exit_code = 0
        try:
            if self._stop_requested:
                # SIGTERM landed before the front end ever served — unwind
                # immediately instead of waiting on an already-set flag.
                return 0
            sites = self._sites_override or self._plan_sites()
            if not sites:
                logger.info(
                    "Multiplex front end: no listener platforms enabled in "
                    "the serve set — supervising children without a front end"
                )
            bind_failed = False
            for kind, host, port in sites:
                try:
                    await self._start_site(kind, host, port)
                except OSError as exc:
                    logger.error(
                        "Multiplex front end cannot bind %s site on %s:%s "
                        "(%s) — free the port or fix platforms config",
                        kind, host, port, exc,
                    )
                    bind_failed = True
            if bind_failed:
                # Fail fast (review P4): a half-served multiplex (e.g. webhook
                # callbacks unroutable) is worse than a clean refusal. The
                # supervisor's finally stops all children.
                logger.error(
                    "Multiplex front end startup incomplete — shutting down"
                )
                return 1
            for kind, (host, port) in sorted(self.bound.items()):
                logger.info(
                    "Multiplex front end %s site on %s:%s "
                    "(unprefixed → default profile, /p/<name>/ → profile)",
                    kind, host, port,
                )
            await self._stop_event.wait()
        finally:
            await self._stop_sites()
            if self._session is not None:
                await self._session.close()
                self._session = None
        return exit_code

    # ── site planning ──────────────────────────────────────────────────

    def _plan_sites(self) -> List[Tuple[str, str, int]]:
        """Sites to host: listener platforms enabled anywhere in the serve set.

        The default profile's pinned binding wins when it enables the
        platform; otherwise the platform's contractual default is used so
        secondaries are still reachable under their prefixes.
        """
        default_child = self._sup.children.get("default")
        default_home = default_child.home if default_child is not None else None
        default_platforms = (
            child_platforms(default_home) if default_home is not None else set()
        )
        sites: List[Tuple[str, str, int]] = []
        for platform, kind in _PLATFORM_KINDS.items():
            wanted = any(
                platform in child_platforms(child.home)
                for child in self._sup.children.values()
            )
            if not wanted:
                continue
            if platform in default_platforms and default_home is not None:
                host, port = listener_binding(default_home, platform)
            else:
                host, port = None, None
                if default_home is not None:
                    logger.info(
                        "Default profile does not enable %s — its unprefixed "
                        "%s routes will answer 503; secondaries remain "
                        "reachable under /p/<name>/",
                        platform, kind,
                    )
            fallback_host, fallback_port = _SITE_DEFAULTS[kind]
            sites.append((kind, host or fallback_host, port or fallback_port))
        return sites

    async def _start_site(self, kind: str, host: str, port: int) -> None:
        runner = web.AppRunner(self._make_app(kind, host))
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        bound_port = port
        if port == 0:
            try:
                addrs = runner.addresses
                if callable(addrs):
                    addrs = addrs()
                for addr in addrs or []:
                    if isinstance(addr, (tuple, list)) and len(addr) >= 2:
                        bound_port = int(addr[1])
                        break
            except Exception:
                pass
        self._runners.append(runner)
        self.bound[kind] = (host, bound_port)

    async def _stop_sites(self) -> None:
        for runner in self._runners:
            try:
                await runner.cleanup()
            except Exception:
                pass
        self._runners.clear()
        self.bound.clear()

    # ── app / handlers ─────────────────────────────────────────────────

    def _make_app(self, kind: str, host: str) -> web.Application:
        # Generous body cap: the child adapters enforce their own limits;
        # the front end only relays streams.
        app = web.Application(client_max_size=1024 ** 3)
        if kind == "api" and is_loopback_host(host):
            # Exact routes BEFORE the catch-all (aiohttp matches in
            # registration order) so the topology endpoint is not proxied.
            # Loopback-only (review): the payload names profiles, pids and
            # internal ports — never exposed on a non-loopback bind.
            app.router.add_get("/multiplex/status", self._handle_status)
        app.router.add_route("*", "/{tail:.*}", self._make_proxy_handler(kind))
        return app

    async def _handle_status(self, request: web.Request) -> web.Response:
        profiles = []
        for child in self._sup.children.values():
            profiles.append(
                {
                    "name": child.name,
                    "pid": child.proc.pid if child.proc is not None else None,
                    "ready": child.ready,
                    # lifecycle label ("ready" / "starting" / "own-home" /
                    # "rejected" / ...) from the supervisor's classifier
                    "state": self._sup.profile_state(child),
                    "restarts": child.restarts,
                    "desired": child.desired,
                    "port_rejected": child.port_rejected,
                    "listeners": dict(child.listeners),
                }
            )
        return web.json_response(
            {
                "ok": True,
                "role": "supervisor-multiplex",
                "front_end": {
                    kind: f"{host}:{port}" for kind, (host, port) in self.bound.items()
                },
                "profiles": profiles,
            }
        )

    def _make_proxy_handler(self, kind: str):
        platform = "api_server" if kind == "api" else "webhook"

        async def handler(request: web.Request) -> web.StreamResponse:
            return await self._proxy(request, kind, platform)

        return handler

    async def _proxy(
        self, request: web.Request, kind: str, platform: str
    ) -> web.StreamResponse:
        raw = request.raw_path or "/"
        path, _, query = raw.partition("?")

        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._proxy_ws(request, platform, path, query)

        try:
            name, rest = _split_profile_prefix(path)
        except ValueError as exc:
            return _error_response(404, "invalid_profile", str(exc))

        if name is None:
            name = "default"
        child = self._sup.children.get(name)
        if child is None:
            return _error_response(
                404,
                "unknown_profile",
                f"profile {name!r} is not served by this multiplex gateway",
                profile=name,
            )
        port = child.listeners.get(platform)
        if port is None and not child.port_rejected and not getattr(
            child, "skipped_own_home", False
        ):
            # On-demand discovery: the monitor loop polls every 2s, but a
            # freshly-ready child would otherwise 503 for up to one cycle.
            # The child's api_server binds BEFORE it reports ready, so the
            # port is already in its runtime status here.
            import asyncio

            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._sup.discover_listeners, child),
                    timeout=5.0,
                )
            except Exception:
                pass
            port = child.listeners.get(platform)
        if port is None:
            if child.port_rejected or getattr(child, "skipped_own_home", False):
                return _error_response(
                    404,
                    "profile_rejected",
                    f"profile {name!r} was rejected at startup and is not "
                    "served",
                    profile=name,
                )
            return _error_response(
                503,
                ERROR_UNREADY,
                f"profile {name!r} has no ready {platform} listener yet",
                profile=name,
            )

        upstream_url = f"http://127.0.0.1:{port}{rest}"
        if query:
            upstream_url += "?" + query

        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in _HOP_BY_HOP
        }
        assert self._session is not None
        try:
            upstream_resp = await self._session.request(
                request.method,
                upstream_url,
                headers=headers,
                data=request.content,
                allow_redirects=False,
            )
        except Exception as exc:
            logger.warning("Upstream %s (profile %r) unreachable: %s",
                           upstream_url, name, exc)
            return _error_response(
                502,
                "upstream_error",
                f"profile {name!r} did not answer the forwarded request",
                profile=name,
            )

        resp = web.StreamResponse(
            status=upstream_resp.status, reason=upstream_resp.reason
        )
        for key, value in upstream_resp.headers.items():
            if key.lower() in _HOP_BY_HOP or key.lower() == "content-length":
                # content-length is re-derived by aiohttp for the relayed
                # (possibly re-chunked) body.
                continue
            # add(), not __setitem__: repeatable headers (Set-Cookie) must
            # survive the relay instead of collapsing to the last one.
            resp.headers.add(key, value)
        try:
            await resp.prepare(request)
            async for chunk in upstream_resp.content.iter_any():
                await resp.write(chunk)
            await resp.write_eof()
        except (ConnectionResetError, ConnectionError):
            pass  # client went away mid-stream — nothing to salvage
        finally:
            upstream_resp.release()
        return resp

    # ── WebSocket pump (B1-5) ───────────────────────────────────────────

    async def _proxy_ws(
        self,
        request: web.Request,
        platform: str,
        path: str,
        query: str,
    ) -> web.WebSocketResponse:
        """Accept the client handshake, then relay frames to the profile's
        child endpoint. Routing failures reach the client as real WS close
        frames with the diagnostic code 4404 (GW-302 lineage: fail-closed
        profile routing). The client's token query/header passes through —
        the child enforces its own per-profile token (see
        ``tui_gateway.ws._expected_auth_token``)."""

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        async def _close(code: int, reason: str) -> None:
            try:
                # aiohttp's kwarg is `message` (not `reason`).
                await ws.close(code=code, message=reason[:100])
            except Exception:
                pass

        try:
            name, rest = _split_profile_prefix(path)
        except ValueError as exc:
            await _close(4404, str(exc))
            return ws
        if name is None:
            name = "default"
        child = self._sup.children.get(name)
        if child is None:
            await _close(4404, "unknown profile")
            return ws
        port = child.listeners.get(platform)
        if port is None and not child.port_rejected and not getattr(
            child, "skipped_own_home", False
        ):
            # Same on-demand discovery as the HTTP path.
            import asyncio

            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._sup.discover_listeners, child),
                    timeout=5.0,
                )
            except Exception:
                pass
            port = child.listeners.get(platform)
        if port is None:
            if child.port_rejected or getattr(child, "skipped_own_home", False):
                await _close(4404, "profile not served")
            else:
                await _close(4404, "profile unavailable")
            return ws

        upstream_url = f"http://127.0.0.1:{port}{rest}"
        if query:
            upstream_url += "?" + query
        # Relay app-level headers only; handshake + hop-by-hop headers are
        # regenerated by the aiohttp client.
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in _HOP_BY_HOP
            and not k.lower().startswith("sec-websocket")
        }
        assert self._session is not None
        try:
            async with self._session.ws_connect(
                upstream_url, headers=headers
            ) as up:
                await self._pump_ws(ws, up)
        except Exception as exc:
            logger.debug(
                "WS upstream %s (profile %r) unavailable: %s",
                upstream_url, name, exc,
            )
            await _close(4404, "profile unavailable")
        return ws

    async def _pump_ws(self, client_ws: Any, up_ws: Any) -> None:
        """Bidirectional frame pump. Whichever side ends first tears down
        both; the upstream's close code is propagated to the client."""
        import asyncio

        import aiohttp

        closed: Dict[str, Any] = {"code": None, "reason": None}

        async def pump(src: Any, upstream_side: bool, dst: Any) -> None:
            try:
                async for msg in src:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await dst.send_str(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await dst.send_bytes(msg.data)
                    else:  # ERROR / CLOSE / CLOSING / CLOSED — terminal
                        break
            except Exception:
                pass
            finally:
                if upstream_side:
                    # aiohttp's async iterator swallows the terminal CLOSE
                    # frame (it ends with StopAsyncIteration without yielding
                    # it) — read the negotiated code off the response object.
                    code = getattr(up_ws, "close_code", None)
                    if code:
                        closed["code"] = code
                        closed["reason"] = getattr(up_ws, "close_reason", None)

        t1 = asyncio.create_task(pump(client_ws, False, up_ws))
        t2 = asyncio.create_task(pump(up_ws, True, client_ws))
        done, pending = await asyncio.wait(
            {t1, t2}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        try:
            await client_ws.close(
                code=closed.get("code"), message=closed.get("reason") or ""
            )
        except Exception:
            pass
        try:
            await up_ws.close()
        except Exception:
            pass
