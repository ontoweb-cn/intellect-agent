"""Tests for the multiplex front end (B1-4): /p/<profile>/ prefix routing.

Real loopback sockets on ephemeral ports only — upstreams are aiohttp
TestServers, the front end binds 127.0.0.1:0. No fixed ports, no external
network.
"""

import asyncio

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gateway.multiplex_front import MultiplexFront, _split_profile_prefix
from gateway.supervisor import Supervisor


def _make_sup(tmp_path, names=("default", "alpha")):
    serve_set = [(n, tmp_path / n) for n in names]
    for _, home in serve_set:
        home.mkdir(parents=True, exist_ok=True)
    return Supervisor(serve_set, spawn_factory=lambda c: None)


async def _start_upstream(marker):
    async def echo(request):
        body = await request.text()
        return web.json_response({
            "marker": marker,
            "path": request.path,
            "method": request.method,
            "query": dict(request.query),
            "x_custom": request.headers.get("X-Custom", ""),
            "auth": request.headers.get("Authorization", ""),
            "body": body,
        })

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", echo)
    server = TestServer(app)
    await server.start_server()
    return server


async def _start_front(sup, sites):
    front = MultiplexFront(sup, sites=sites)
    task = asyncio.create_task(front.run())
    for _ in range(200):
        if front.bound:
            return front, task
        await asyncio.sleep(0.02)
    front.request_stop()
    await task
    pytest.fail("front end never bound a site")


async def _stop_front(front, task, upstreams=()):
    front.request_stop()
    await asyncio.wait_for(task, timeout=5)
    for server in upstreams:
        await server.close()


# ── prefix parse (pure, three-state) ───────────────────────────────────

def test_prefix_parse_unprefixed():
    assert _split_profile_prefix("/v1/chat") == (None, "/v1/chat")
    assert _split_profile_prefix("/prefix/nope") == (None, "/prefix/nope")
    assert _split_profile_prefix("/p") == (None, "/p")


def test_prefix_parse_named():
    assert _split_profile_prefix("/p/alpha/v1/health") == ("alpha", "/v1/health")
    # Mixed case is normalized to the canonical lowercase id.
    assert _split_profile_prefix("/p/Alpha/v1/health") == ("alpha", "/v1/health")
    # Profile root itself: rest normalizes to "/".
    assert _split_profile_prefix("/p/alpha") == ("alpha", "/")
    assert _split_profile_prefix("/p/alpha/") == ("alpha", "/")
    # Deeper paths survive intact (regression: a maxsplit truncation used to
    # drop the tail segments).
    assert _split_profile_prefix("/p/alpha/webhooks/r1") == ("alpha", "/webhooks/r1")


def test_prefix_parse_rejects_invalid():
    with pytest.raises(ValueError):
        _split_profile_prefix("/p/")
    with pytest.raises(ValueError):
        _split_profile_prefix("/p/root/x")  # reserved name
    with pytest.raises(ValueError):
        _split_profile_prefix("/p/bad!name/x")  # outside the profile id charset


# ── HTTP routing ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unprefixed_routes_to_default_and_prefix_routes_to_named(tmp_path):
    sup = _make_sup(tmp_path)
    ups = {
        "default": await _start_upstream("default"),
        "alpha": await _start_upstream("alpha"),
    }
    for name, server in ups.items():
        sup.children[name].listeners["api_server"] = server.port
    front, task = await _start_front(sup, [("api", "127.0.0.1", 0)])
    try:
        _, port = front.bound["api"]
        async with aiohttp.ClientSession() as http:
            async with http.get(f"http://127.0.0.1:{port}/v1/health") as resp:
                assert resp.status == 200
                assert (await resp.json())["marker"] == "default"
            async with http.get(
                f"http://127.0.0.1:{port}/p/alpha/v1/health?x=1",
                headers={"X-Custom": "abc", "Authorization": "Bearer k"},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                # Prefix stripped, query + app-level headers forwarded.
                assert data["marker"] == "alpha"
                assert data["path"] == "/v1/health"
                assert data["query"] == {"x": "1"}
                assert data["x_custom"] == "abc"
                assert data["auth"] == "Bearer k"
    finally:
        await _stop_front(front, task, ups.values())


@pytest.mark.asyncio
async def test_unknown_and_invalid_profiles_404(tmp_path):
    sup = _make_sup(tmp_path)
    front, task = await _start_front(sup, [("api", "127.0.0.1", 0)])
    try:
        _, port = front.bound["api"]
        async with aiohttp.ClientSession() as http:
            async with http.get(f"http://127.0.0.1:{port}/p/ghost/v1/x") as resp:
                assert resp.status == 404
                err = (await resp.json())["error"]
                assert err["code"] == "unknown_profile"
                assert err["profile"] == "ghost"
            async with http.get(f"http://127.0.0.1:{port}/p/root/v1/x") as resp:
                assert resp.status == 404
                assert (await resp.json())["error"]["code"] == "invalid_profile"
    finally:
        await _stop_front(front, task)


@pytest.mark.asyncio
async def test_unready_profile_503_and_rejected_profile_404(tmp_path):
    sup = _make_sup(tmp_path)
    sup.children["alpha"].port_rejected = False
    front, task = await _start_front(sup, [("api", "127.0.0.1", 0)])
    try:
        _, port = front.bound["api"]
        async with aiohttp.ClientSession() as http:
            # alpha enabled but never reported a listener port.
            async with http.get(f"http://127.0.0.1:{port}/p/alpha/v1/x") as resp:
                assert resp.status == 503
                assert (await resp.json())["error"]["code"] == "profile_unready"
        sup.children["alpha"].port_rejected = True
        async with aiohttp.ClientSession() as http:
            async with http.get(f"http://127.0.0.1:{port}/p/alpha/v1/x") as resp:
                assert resp.status == 404
                assert (await resp.json())["error"]["code"] == "profile_rejected"
    finally:
        await _stop_front(front, task)


@pytest.mark.asyncio
async def test_webhook_site_prefix_fanout(tmp_path):
    sup = _make_sup(tmp_path)
    ups = {
        "default": await _start_upstream("default-hook"),
        "alpha": await _start_upstream("alpha-hook"),
    }
    for name, server in ups.items():
        sup.children[name].listeners["webhook"] = server.port
    front, task = await _start_front(sup, [("webhook", "127.0.0.1", 0)])
    try:
        _, port = front.bound["webhook"]
        async with aiohttp.ClientSession() as http:
            # Two profiles, same external port, different prefixes — each
            # webhook provider's callback lands on its own child.
            async with http.post(
                f"http://127.0.0.1:{port}/p/alpha/webhooks/r1", data=b"hi"
            ) as resp:
                data = await resp.json()
                assert data["marker"] == "alpha-hook"
                assert data["path"] == "/webhooks/r1"
                assert data["body"] == "hi"
            async with http.post(
                f"http://127.0.0.1:{port}/webhooks/r0", data=b"ho"
            ) as resp:
                data = await resp.json()
                assert data["marker"] == "default-hook"
                assert data["path"] == "/webhooks/r0"
    finally:
        await _stop_front(front, task, ups.values())


@pytest.mark.asyncio
async def test_multiplex_status_endpoint(tmp_path):
    sup = _make_sup(tmp_path)
    sup.children["alpha"].listeners = {"api_server": 41234}
    front, task = await _start_front(sup, [("api", "127.0.0.1", 0)])
    try:
        _, port = front.bound["api"]
        async with aiohttp.ClientSession() as http:
            async with http.get(f"http://127.0.0.1:{port}/multiplex/status") as resp:
                assert resp.status == 200
                data = await resp.json()
        assert data["ok"] is True
        assert data["role"] == "supervisor-multiplex"
        names = {p["name"] for p in data["profiles"]}
        assert names == {"default", "alpha"}
        alpha = next(p for p in data["profiles"] if p["name"] == "alpha")
        assert alpha["listeners"] == {"api_server": 41234}
        assert "api" in data["front_end"]
    finally:
        await _stop_front(front, task)


@pytest.mark.asyncio
async def test_status_route_is_not_proxied_to_default_child(tmp_path):
    """The exact /multiplex/status route must win over the catch-all even
    when the default child also serves a path of the same name."""
    sup = _make_sup(tmp_path)
    upstream = await _start_upstream("default")
    sup.children["default"].listeners["api_server"] = upstream.port
    front, task = await _start_front(sup, [("api", "127.0.0.1", 0)])
    try:
        _, port = front.bound["api"]
        async with aiohttp.ClientSession() as http:
            async with http.get(f"http://127.0.0.1:{port}/multiplex/status") as resp:
                assert resp.status == 200
                assert (await resp.json())["role"] == "supervisor-multiplex"
    finally:
        await _stop_front(front, task, [upstream])


@pytest.mark.asyncio
async def test_ws_routes_and_pumps_per_profile(tmp_path):
    """WS upgrades route by the same prefix rules and frames pump both ways."""
    import json

    sup = _make_sup(tmp_path)
    ups = {}
    for name in ("default", "alpha"):
        marker = name

        async def handler(request, _marker=marker):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_str(json.dumps({"marker": _marker}))
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await ws.send_str(msg.data.upper())
                else:
                    break
            return ws

        app = web.Application()
        app.router.add_get("/api/ws", handler)
        server = TestServer(app)
        await server.start_server()
        ups[name] = server
        sup.children[name].listeners["api_server"] = server.port

    front, task = await _start_front(sup, [("api", "127.0.0.1", 0)])
    try:
        _, port = front.bound["api"]
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(
                f"http://127.0.0.1:{port}/p/alpha/api/ws"
            ) as ws:
                hello = await ws.receive()  # first frame: marker announce
                assert json.loads(hello.data)["marker"] == "alpha"
                await ws.send_str("ping")
                echo = await ws.receive()
                assert echo.data == "PING"
    finally:
        await _stop_front(front, task, ups.values())


@pytest.mark.asyncio
async def test_ws_upstream_close_code_propagates(tmp_path):
    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str("bye")
        code = int(request.query.get("code", "1000"))
        await ws.close(code=code, message=str(code))
        return ws

    sup = _make_sup(tmp_path, names=("default",))
    app = web.Application()
    app.router.add_get("/api/ws", handler)
    server = TestServer(app)
    await server.start_server()
    sup.children["default"].listeners["api_server"] = server.port

    front, task = await _start_front(sup, [("api", "127.0.0.1", 0)])
    try:
        _, port = front.bound["api"]
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(
                f"http://127.0.0.1:{port}/api/ws?code=4321"
            ) as ws:
                msg = await ws.receive()
                assert msg.data == "bye"
                msg = await ws.receive()
                assert msg.type == aiohttp.WSMsgType.CLOSE
                assert msg.data == 4321
    finally:
        await _stop_front(front, task, [server])


@pytest.mark.asyncio
async def test_ws_unknown_and_unready_profiles_close_4404(tmp_path):
    sup = _make_sup(tmp_path)
    front, task = await _start_front(sup, [("api", "127.0.0.1", 0)])
    try:
        _, port = front.bound["api"]
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(
                f"http://127.0.0.1:{port}/p/ghost/api/ws"
            ) as ws:
                msg = await ws.receive()
                assert msg.type == aiohttp.WSMsgType.CLOSE
                assert msg.data == 4404
                assert msg.extra == "unknown profile"
            # alpha is known but never reported a listener port.
            async with http.ws_connect(
                f"http://127.0.0.1:{port}/p/alpha/api/ws"
            ) as ws:
                msg = await ws.receive()
                assert msg.type == aiohttp.WSMsgType.CLOSE
                assert msg.data == 4404
                assert msg.extra == "profile unavailable"
    finally:
        await _stop_front(front, task)


# ── site planning ───────────────────────────────────────────────────────

def test_plan_sites_uses_default_profile_binding(tmp_path):
    sup = _make_sup(tmp_path)
    (tmp_path / "default" / "config.yaml").write_text(
        "platforms:\n"
        "  api_server:\n    enabled: true\n    port: 9001\n",
        encoding="utf-8",
    )
    (tmp_path / "alpha" / "config.yaml").write_text(
        "platforms:\n  webhook:\n    enabled: true\n", encoding="utf-8"
    )
    sites = dict(
        (kind, (host, port))
        for kind, host, port in MultiplexFront(sup)._plan_sites()
    )
    # Default profile pins api_server:9001; webhook only wanted by a
    # secondary → contractual default host/port.
    assert sites["api"] == ("127.0.0.1", 9001)
    assert sites["webhook"] == ("0.0.0.0", 8644)


def test_plan_sites_empty_when_no_listener_platforms(tmp_path):
    sup = _make_sup(tmp_path)
    assert MultiplexFront(sup)._plan_sites() == []
