"""Tests for the team_bridge platform adapter plugin.

Covers the pull→claim→dispatch→complete lifecycle over a mocked upstream
gateway (httpx.MockTransport), the headless interaction semantics (P0-3:
clarify/approval prompts must never leak through ``send()`` — a send()
completes a task), and the HMAC task signature gate.
"""

import asyncio
import json

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock

from tests.gateway._plugin_adapter_loader import load_plugin_adapter

# Load plugins/platforms/team_bridge/adapter.py under a unique module name
# (plugin_adapter_team_bridge) so it cannot collide with other plugin
# adapters loaded by sibling tests in the same xdist worker.
_bridge_mod = load_plugin_adapter("team_bridge")

TeamBridgeAdapter = _bridge_mod.TeamBridgeAdapter
check_requirements = _bridge_mod.check_requirements
validate_config = _bridge_mod.validate_config
is_connected = _bridge_mod.is_connected
register = _bridge_mod.register
compute_task_signature = _bridge_mod.compute_task_signature
split_signature_line = _bridge_mod.split_signature_line
verify_task_signature = _bridge_mod.verify_task_signature
_env_enablement = _bridge_mod._env_enablement

ENV_KEYS = (
    "TEAM_BRIDGE_SERVER_URL",
    "TEAM_BRIDGE_API_TOKEN",
    "TEAM_BRIDGE_BOARD",
    "TEAM_BRIDGE_WORKER_ID",
    "TEAM_BRIDGE_POLL_SECONDS",
    "TEAM_BRIDGE_CLAIM_TTL_SECONDS",
    "TEAM_BRIDGE_MAX_CONCURRENT",
    "TEAM_BRIDGE_TASK_SIGNING_KEY",
    "TEAM_BRIDGE_APPROVAL_MODE",
    "TEAM_BRIDGE_ALLOWED_USERS",
    "TEAM_BRIDGE_ALLOW_ALL_USERS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _make_adapter(**extra):
    from gateway.config import PlatformConfig

    cfg = PlatformConfig(enabled=True)
    cfg.extra = dict(extra)
    return TeamBridgeAdapter(cfg)


def _signed_body(key, session_id, title, message):
    sig = compute_task_signature(key, session_id, title, message)
    return f"{message}\n\nsig: {sig}"


def _task(task_id="t_abc12345", status="ready", session_id="", title="Do a thing",
          body="Do a thing, please", assignee="intranet-w1"):
    return {
        "id": task_id,
        "title": title,
        "body": body,
        "assignee": assignee,
        "status": status,
        "session_id": session_id,
        "created_by": "webui",
    }


class _Upstream:
    """Scriptable upstream gateway: records requests, returns canned replies."""

    def __init__(self):
        self.requests = []
        self.tasks = []
        self.claim_results = {}
        self.complete_ok = True
        self.block_ok = True
        self.heartbeat_404 = set()
        self.heartbeat_fail = set()
        self._removed = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        method = request.method

        if method == "GET" and path == "/api/kanban/tasks":
            return httpx.Response(200, json=self.tasks)

        if method == "POST" and path.endswith("/claim"):
            task_id = path.rstrip("/").split("/")[-2]
            result = self.claim_results.get(task_id, "ok")
            if result == "conflict":
                return httpx.Response(409, json={"error": {"code": "conflict",
                                                            "message": "not claimable"}})
            # a successful claim consumes the task from the ready list
            self._removed[task_id] = self._find(task_id) or self._removed.get(task_id)
            self.tasks = [t for t in self.tasks if t["id"] != task_id]
            return httpx.Response(200, json={"ok": True, "task": self._claimed(task_id)})

        if method == "POST" and path.endswith("/complete"):
            if not self.complete_ok:
                return httpx.Response(500, json={"error": {"code": "internal_error",
                                                            "message": "boom"}})
            return httpx.Response(200, json={"ok": True})

        if method == "POST" and path.endswith("/block"):
            if not self.block_ok:
                return httpx.Response(500, json={"error": {"code": "internal_error",
                                                            "message": "boom"}})
            return httpx.Response(200, json={"ok": True})

        if method == "POST" and path.endswith("/heartbeat"):
            task_id = path.rstrip("/").split("/")[-2]
            if task_id in self.heartbeat_404:
                return httpx.Response(404, json={"error": {"code": "task_not_found",
                                                            "message": "claim expired"}})
            if task_id in self.heartbeat_fail:
                return httpx.Response(500, json={"error": {"code": "internal_error",
                                                            "message": "boom"}})
            return httpx.Response(200, json={"ok": True})

        return httpx.Response(404, json={"error": {"code": "task_not_found",
                                                    "message": "no route"}})

    def _find(self, task_id):
        for t in self.tasks:
            if t["id"] == task_id:
                return t
        return None

    def _claimed(self, task_id):
        """Task payload for a claim response — kept aside when consumed."""
        return self._removed.get(task_id) or self._find(task_id)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler), timeout=5.0,
            base_url="https://upstream.example",
        )


def _wire(adapter, upstream: _Upstream):
    adapter._http_client = upstream.client()
    adapter._message_handler = AsyncMock(return_value="all done")
    adapter._running = True


async def _drain_background_tasks():
    """Await spawned message-handler background tasks until settled."""
    for _ in range(50):
        pending = [t for t in asyncio.all_tasks() - {asyncio.current_task()}
                   if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


# ── Signature helpers ─────────────────────────────────────────────────────


class TestTaskSignature:

    def test_roundtrip(self):
        body = _signed_body("secret", "sess-1", "title", "hello world")
        core, sig = split_signature_line(body)
        assert core == "hello world"
        assert sig
        assert verify_task_signature("secret", "sess-1", "title", body)

    def test_multiline_body(self):
        msg = "line1\nline2\n\nline3"
        body = _signed_body("k", "", "t", msg)
        core, _ = split_signature_line(body)
        assert core == msg

    def test_wrong_key_rejected(self):
        body = _signed_body("k1", "s", "t", "m")
        assert not verify_task_signature("k2", "s", "t", body)

    def test_tampered_body_rejected(self):
        body = _signed_body("k", "s", "t", "innocent")
        tampered = body.replace("innocent", "evil")
        assert not verify_task_signature("k", "s", "t", tampered)

    def test_missing_sig_line_rejected_when_key_set(self):
        assert not verify_task_signature("k", "s", "t", "no signature here")

    def test_empty_key_accepts_everything(self):
        assert verify_task_signature("", "s", "t", "unsigned")
        assert verify_task_signature("", "s", "t", "x\n\nsig: deadbeef")

    def test_sig_not_last_line_ignored(self):
        body = "m\n\nsig: abc\ntail text"
        core, sig = split_signature_line(body)
        assert sig is None
        assert core == body

    def test_message_ending_with_sig_like_line(self):
        # A user message whose own last line looks like a sig line must
        # survive: split only strips the FINAL line (the real sig), and
        # the length-bound signature fails closed if anything else was
        # stripped (e.g. unsigned producer with a keyed worker).
        msg = f"look closely\nsig: {'a' * 64}"
        body = _signed_body("k", "s", "t", msg)
        core, sig = split_signature_line(body)
        assert core == msg  # user's own sig-like line preserved
        assert sig and verify_task_signature("k", "s", "t", body)

    def test_forged_sig_line_fails_closed(self):
        # A forged trailing sig that doesn't match the length-bound
        # signature must be rejected, never mis-executed.
        msg = "hello"
        body = _signed_body("k", "s", "t", msg)
        assert not verify_task_signature("k", "s", "t", body + "\nextra")
        assert not verify_task_signature("k", "s", "t", msg[:-1] + "\n\nsig: " + "c" * 64)


# ── Pre-flight hooks ──────────────────────────────────────────────────────


class TestPreflight:

    def test_check_requirements_requires_server_and_token(self):
        assert not check_requirements()
        import os
        os.environ["TEAM_BRIDGE_SERVER_URL"] = "https://up.example"
        assert not check_requirements()
        os.environ["TEAM_BRIDGE_API_TOKEN"] = "imt_x"
        assert check_requirements()

    def test_validate_config_reads_extra(self):
        cfg = MagicMock()
        cfg.extra = {"server_url": "https://up.example", "api_token": "imt_x"}
        assert validate_config(cfg)

    def test_validate_config_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("TEAM_BRIDGE_SERVER_URL", "https://up.example")
        monkeypatch.setenv("TEAM_BRIDGE_API_TOKEN", "imt_x")
        cfg = MagicMock()
        cfg.extra = {}
        assert validate_config(cfg)

    def test_env_enablement_seeds_extra(self, monkeypatch):
        monkeypatch.setenv("TEAM_BRIDGE_SERVER_URL", "https://up.example")
        monkeypatch.setenv("TEAM_BRIDGE_API_TOKEN", "imt_x")
        monkeypatch.setenv("TEAM_BRIDGE_BOARD", "ops")
        monkeypatch.setenv("TEAM_BRIDGE_WORKER_ID", "w1")
        seed = _env_enablement()
        assert seed["server_url"] == "https://up.example"
        assert seed["board"] == "ops"
        assert seed["worker_id"] == "w1"

    def test_env_enablement_none_when_unconfigured(self):
        assert _env_enablement() is None


# ── Adapter init ──────────────────────────────────────────────────────────


class TestAdapterInit:

    def test_defaults(self, monkeypatch):
        monkeypatch.setenv("TEAM_BRIDGE_SERVER_URL", "https://up.example")
        monkeypatch.setenv("TEAM_BRIDGE_API_TOKEN", "imt_x")
        a = _make_adapter()
        assert a._server == "https://up.example"
        assert a._board == "webui"
        assert a._worker_id == "team-bridge"
        assert a._poll_seconds == 5.0
        assert a._claim_ttl == 300
        assert a._max_concurrent == 2
        assert a._approval_mode == "deny"
        assert a._inflight == {}

    def test_env_wins_over_extra(self, monkeypatch):
        monkeypatch.setenv("TEAM_BRIDGE_SERVER_URL", "https://from-env.example")
        monkeypatch.setenv("TEAM_BRIDGE_API_TOKEN", "imt_x")
        a = _make_adapter(server_url="https://from-extra.example")
        assert a._server == "https://from-env.example"

    def test_numeric_garbage_falls_back(self, monkeypatch):
        monkeypatch.setenv("TEAM_BRIDGE_SERVER_URL", "https://up.example")
        monkeypatch.setenv("TEAM_BRIDGE_API_TOKEN", "imt_x")
        monkeypatch.setenv("TEAM_BRIDGE_POLL_SECONDS", "banana")
        monkeypatch.setenv("TEAM_BRIDGE_MAX_CONCURRENT", "zero")
        a = _make_adapter()
        assert a._poll_seconds == 5.0
        assert a._max_concurrent == 2


# ── Pull → claim → dispatch → complete lifecycle ─────────────────────────


class TestLifecycle:

    @pytest.mark.asyncio
    async def test_full_chat_task_roundtrip(self):
        upstream = _Upstream()
        body = _signed_body("key1", "sess-77", "[chat] hi", "What is 2+2?")
        upstream.tasks = [_task(session_id="sess-77", title="[chat] hi", body=body)]

        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
            task_signing_key="key1", worker_id="intranet-w1", board="webui",
        )
        _wire(adapter, upstream)

        try:
            claimed = await adapter._poll_once()
            assert claimed
            # local session key follows the upstream session id
            assert "sess-77" in adapter._inflight
            assert adapter._inflight["sess-77"]["task_id"] == "t_abc12345"
            await _drain_background_tasks()
            adapter._message_handler.assert_awaited_once()
            event = adapter._message_handler.await_args.args[0]
            # dispatch text carries the task frame without the sig line
            assert "sig:" not in event.text
            assert "What is 2+2?" in event.text
            assert "t_abc12345" in event.text
            assert event.source.chat_id == "sess-77"
            assert event.source.user_id == "intranet-w1"

            # Let the spawned background turn settle: the handler's reply
            # flows back through base → send() → task complete.
            completes = [r for r in upstream.requests
                         if r.url.path.endswith("/complete")]
            assert len(completes) == 1
            assert json.loads(completes[0].content) == {"result": "all done"}
            assert "sess-77" not in adapter._inflight
        finally:
            await adapter._http_client.aclose()

    @pytest.mark.asyncio
    async def test_plain_task_uses_task_id_as_chat(self):
        upstream = _Upstream()
        body = _signed_body("k", "", "Run lint", "run lint on repo")
        upstream.tasks = [_task(task_id="t_lint0001", title="Run lint", body=body)]

        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
            task_signing_key="k",
        )
        _wire(adapter, upstream)
        try:
            assert await adapter._poll_once()
            assert "t_lint0001" in adapter._inflight
            resp = await adapter.send("t_lint0001", "lint clean")
            assert resp.success
        finally:
            await adapter._http_client.aclose()

    @pytest.mark.asyncio
    async def test_unsigned_task_skipped_when_key_set(self):
        upstream = _Upstream()
        upstream.tasks = [_task(body="no signature at all")]
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
            task_signing_key="k",
        )
        _wire(adapter, upstream)
        try:
            assert not await adapter._poll_once()
            assert adapter._inflight == {}
            # no claim request was even attempted
            assert not [r for r in upstream.requests if "/claim" in str(r.url)]
        finally:
            await adapter._http_client.aclose()

    @pytest.mark.asyncio
    async def test_unsigned_task_accepted_without_key(self):
        upstream = _Upstream()
        upstream.tasks = [_task(body="no signature at all")]
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
        )
        _wire(adapter, upstream)
        try:
            assert await adapter._poll_once()
            assert adapter._inflight
        finally:
            await adapter._http_client.aclose()

    @pytest.mark.asyncio
    async def test_claim_conflict_skipped(self):
        upstream = _Upstream()
        upstream.tasks = [_task(task_id="t_taken0042")]
        upstream.claim_results["t_taken0042"] = "conflict"
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
        )
        _wire(adapter, upstream)
        try:
            assert not await adapter._poll_once()
            assert adapter._inflight == {}
            adapter._message_handler.assert_not_awaited()
        finally:
            await adapter._http_client.aclose()

    @pytest.mark.asyncio
    async def test_unauthorized_is_fatal(self):
        class _401(_Upstream):
            def handler(self, request):
                self.requests.append(request)
                return httpx.Response(401, json={"error": {"code": "unauthorized",
                                                            "message": "bad token"}})

        upstream = _401()
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="bad",
        )
        _wire(adapter, upstream)
        try:
            with pytest.raises(_bridge_mod._FatalBridgeError):
                await adapter._poll_once()
            assert adapter._fatal_error_code == "team_bridge_unauthorized"
            assert not adapter._running
        finally:
            await adapter._http_client.aclose()

    @pytest.mark.asyncio
    async def test_max_concurrent_gate(self):
        upstream = _Upstream()
        upstream.tasks = [
            _task(task_id="t_a0000001", session_id="sess-a"),
            _task(task_id="t_b0000002", session_id="sess-b"),
            _task(task_id="t_c0000003", session_id="sess-c"),
        ]
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
            max_concurrent="2",
        )
        _wire(adapter, upstream)
        try:
            # poll 1: claims sess-a
            assert await adapter._poll_once()
            assert set(adapter._inflight) == {"sess-a"}
            # poll 2: claims sess-b
            assert await adapter._poll_once()
            assert set(adapter._inflight) == {"sess-a", "sess-b"}
            # poll 3: gate blocks before any further claim
            assert await adapter._poll_once() is False
            claims = [r for r in upstream.requests if r.url.path.endswith("/claim")]
            assert len(claims) == 2
        finally:
            await adapter._http_client.aclose()


# ── send() edge cases ─────────────────────────────────────────────────────


class TestSend:

    @pytest.mark.asyncio
    async def test_send_without_claim_is_dropped(self):
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
        )
        resp = await adapter.send("nobody", "hello")
        assert resp.success  # silently dropped, not an error

    @pytest.mark.asyncio
    async def test_send_after_failure_is_swallowed(self):
        upstream = _Upstream()
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
        )
        _wire(adapter, upstream)
        try:
            adapter._inflight["sess-x"] = {
                "task_id": "t_fail0001", "claimed_at": 0.0,
                "failed": True, "failed_at": 0.0,
            }
            resp = await adapter.send("sess-x", "base-layer error text")
            assert resp.success  # swallowed
            completes = [r for r in upstream.requests
                         if r.url.path.endswith("/complete")]
            assert not completes
        finally:
            await adapter._http_client.aclose()

    @pytest.mark.asyncio
    async def test_send_truncates_long_results(self):
        upstream = _Upstream()
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
        )
        _wire(adapter, upstream)
        try:
            adapter._inflight["c"] = {
                "task_id": "t_long0001", "claimed_at": 0.0,
                "failed": False, "failed_at": 0.0,
            }
            resp = await adapter.send("c", "x" * (_bridge_mod.MAX_MESSAGE_LENGTH + 500))
            assert resp.success
            body = json.loads(
                [r for r in upstream.requests if r.url.path.endswith("/complete")][0].content
            )
            assert len(body["result"]) <= _bridge_mod.MAX_MESSAGE_LENGTH + 30
            assert "截断" in body["result"]
        finally:
            await adapter._http_client.aclose()

    @pytest.mark.asyncio
    async def test_send_upstream_failure_keeps_slot(self):
        upstream = _Upstream()
        upstream.complete_ok = False
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
        )
        _wire(adapter, upstream)
        try:
            adapter._inflight["c"] = {
                "task_id": "t_keep0001", "claimed_at": 0.0,
                "failed": False, "failed_at": 0.0,
            }
            resp = await adapter.send("c", "result")
            assert not resp.success
            assert resp.retryable
            assert "c" in adapter._inflight  # slot retained for retry
        finally:
            await adapter._http_client.aclose()


# ── Headless interaction semantics (P0-3) ─────────────────────────────────


class TestHeadlessSemantics:
    """Approval/clarify prompts must never flow through send() — a send()
    completes the task with the prompt text and the real reply is lost."""

    @pytest.mark.asyncio
    async def test_send_exec_approval_does_not_complete(self):
        upstream = _Upstream()
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
        )
        _wire(adapter, upstream)
        try:
            adapter._inflight["c"] = {
                "task_id": "t_appr0001", "claimed_at": 0.0,
                "failed": False, "failed_at": 0.0,
            }
            resp = await adapter.send_exec_approval(
                chat_id="c", command="rm -rf /", session_key="sk",
                description="destructive",
            )
            assert resp.success
            # no complete request; claim still held; send() from the
            # gateway's text fallback never happened either
            assert not [r for r in upstream.requests
                        if r.url.path.endswith("/complete")]
            assert "c" in adapter._inflight
            # the real reply afterwards still completes normally
            resp = await adapter.send("c", "actual final answer")
            assert resp.success
            completes = [r for r in upstream.requests
                         if r.url.path.endswith("/complete")]
            assert len(completes) == 1
            assert json.loads(completes[0].content) == {"result": "actual final answer"}
        finally:
            await adapter._http_client.aclose()

    @pytest.mark.asyncio
    async def test_send_clarify_autoresolves_without_send(self):
        upstream = _Upstream()
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
        )
        _wire(adapter, upstream)
        try:
            adapter._inflight["c"] = {
                "task_id": "t_clar0001", "claimed_at": 0.0,
                "failed": False, "failed_at": 0.0,
            }
            # register a pending clarify the way tools/clarify_gateway does
            from tools import clarify_gateway as cg

            cg._entries["clar-1"] = cg._ClarifyEntry(
                clarify_id="clar-1", session_key="sk", question="q?", choices=["a", "b"],
            )
            try:
                resp = await adapter.send_clarify(
                    chat_id="c", question="q?", choices=["a", "b"],
                    clarify_id="clar-1", session_key="sk",
                )
                assert resp.success
                assert cg._entries["clar-1"].event.is_set()
                assert "最合理" in cg._entries["clar-1"].response
            finally:
                cg._entries.pop("clar-1", None)
            sends = [r for r in upstream.requests
                     if r.url.path.endswith("/complete")]
            assert not sends  # nothing leaked through the completion channel
        finally:
            await adapter._http_client.aclose()

    @pytest.mark.asyncio
    async def test_class_defines_send_exec_approval_for_gateway(self):
        # gateway/run.py checks the CLASS for send_exec_approval to decide
        # between button-based and text-fallback approval delivery. Defining
        # it is what keeps the ⚠️ prompt text out of adapter.send().
        assert getattr(TeamBridgeAdapter, "send_exec_approval", None) is not None


# ── Heartbeat / watchdog ──────────────────────────────────────────────────


class TestHeartbeatWatchdog:

    @pytest.mark.asyncio
    async def test_heartbeat_refreshes_claims(self):
        upstream = _Upstream()
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
        )
        _wire(adapter, upstream)
        try:
            adapter._claim_ttl = 300
            adapter._inflight["c"] = {
                "task_id": "t_hb000001", "claimed_at": _bridge_mod.time.monotonic(),
                "failed": False, "failed_at": 0.0,
            }
            await adapter._heartbeat_once()
            assert "c" in adapter._inflight
            beats = [r for r in upstream.requests
                     if r.url.path.endswith("/heartbeat")]
            assert len(beats) == 1
            assert json.loads(beats[0].content) == {"ttl_seconds": 300}
        finally:
            await adapter._http_client.aclose()

    @pytest.mark.asyncio
    async def test_watchdog_releases_after_3x_ttl_without_heartbeat(self):
        # 3x TTL with NO successful heartbeat (upstream erroring) → the
        # local slot is released; the board reclaims the expired claim.
        upstream = _Upstream()
        upstream.heartbeat_fail.add("t_stale001")
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
        )
        _wire(adapter, upstream)
        try:
            adapter._claim_ttl = 100
            adapter._inflight["c"] = {
                "task_id": "t_stale001", "claimed_at": (
                    _bridge_mod.time.monotonic() - 301),
                "failed": False, "failed_at": 0.0,
            }
            await adapter._heartbeat_once()
            assert "c" not in adapter._inflight

            # same age but a young successful heartbeat → kept
            adapter._inflight["c"] = {
                "task_id": "t_stale001", "claimed_at": (
                    _bridge_mod.time.monotonic() - 301),
                "failed": False, "failed_at": 0.0,
            }
            upstream.heartbeat_fail.discard("t_stale001")
            await adapter._heartbeat_once()
            assert "c" in adapter._inflight
        finally:
            await adapter._http_client.aclose()

    @pytest.mark.asyncio
    async def test_healthy_heartbeat_keeps_long_task_slot(self):
        # A long-running turn with successful heartbeats must NOT be
        # force-released: claimed_at doubles as "last successful
        # heartbeat", so the 3x-TTL watchdog only fires when contact
        # with the board is actually lost. (Regression: the watchdog
        # previously used the raw claim time and killed any turn older
        # than 3x TTL even while heartbeats were succeeding — the agent
        # reply was then dropped and the reclaimed task re-executed.)
        upstream = _Upstream()
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
        )
        _wire(adapter, upstream)
        try:
            adapter._claim_ttl = 100
            adapter._inflight["c"] = {
                "task_id": "t_long001", "claimed_at": (
                    _bridge_mod.time.monotonic() - 500),
                "failed": False, "failed_at": 0.0,
            }
            await adapter._heartbeat_once()
            assert "c" in adapter._inflight  # heartbeat succeeded → kept
            assert (_bridge_mod.time.monotonic()
                    - adapter._inflight["c"]["claimed_at"]) < 100
            # a later reply still completes the task
            resp = await adapter.send("c", "finally done")
            assert resp.success
        finally:
            await adapter._http_client.aclose()

    @pytest.mark.asyncio
    async def test_heartbeat_404_releases_slot(self):
        upstream = _Upstream()
        upstream.heartbeat_404.add("t_gone0001")
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
        )
        _wire(adapter, upstream)
        try:
            adapter._claim_ttl = 100
            adapter._inflight["c"] = {
                "task_id": "t_gone0001", "claimed_at": 0.0,
                "failed": False, "failed_at": 0.0,
            }
            await adapter._heartbeat_once()
            assert "c" not in adapter._inflight
        finally:
            await adapter._http_client.aclose()

    @pytest.mark.asyncio
    async def test_failed_slot_cleared_after_grace(self):
        upstream = _Upstream()
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
        )
        _wire(adapter, upstream)
        try:
            adapter._inflight["c"] = {
                "task_id": "t_dead001", "claimed_at": 0.0,
                "failed": True,
                "failed_at": _bridge_mod.time.monotonic() - 120,
            }
            await adapter._heartbeat_once()
            assert "c" not in adapter._inflight
        finally:
            await adapter._http_client.aclose()


# ── Dispatch failure → block ──────────────────────────────────────────────


class TestDispatchFailure:

    @pytest.mark.asyncio
    async def test_handler_exception_blocks_task(self):
        upstream = _Upstream()
        upstream.tasks = [_task(task_id="t_err00001")]
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
        )
        _wire(adapter, upstream)
        adapter._message_handler = AsyncMock(side_effect=RuntimeError("pipeline exploded"))
        try:
            assert await adapter._poll_once()
            await _drain_background_tasks()
            entry = adapter._inflight["t_err00001"]
            assert entry["failed"]
            blocks = [r for r in upstream.requests if r.url.path.endswith("/block")]
            assert len(blocks) == 1
            # the later base-layer error send is swallowed, not completed
            await adapter.send("t_err00001", "error: pipeline exploded")
            assert not [r for r in upstream.requests
                        if r.url.path.endswith("/complete")]
        finally:
            await adapter._http_client.aclose()


# ── Approval mode ─────────────────────────────────────────────────────────


class TestApprovalMode:

    @pytest.mark.asyncio
    async def test_approve_mode_preapproves_session(self):
        upstream = _Upstream()
        upstream.tasks = [_task(task_id="t_yolo0001", session_id="sess-y")]
        adapter = _make_adapter(
            server_url="https://upstream.example", api_token="imt_x",
            approval_mode="approve",
        )
        _wire(adapter, upstream)
        try:
            from tools.approval import _session_yolo, _session_approved

            _session_yolo.clear()
            assert await adapter._poll_once()
            from gateway.session import SessionSource

            source = SessionSource(
                platform=adapter.platform, chat_id="sess-y", chat_type="dm",
                user_id=adapter._worker_id,
            )
            from gateway.session import build_session_key

            key = build_session_key(source)
            assert key in _session_yolo
            _session_yolo.clear()
        finally:
            await adapter._http_client.aclose()


# ── Registration contract ─────────────────────────────────────────────────


class TestRegistration:

    def test_register_wires_platform(self):
        ctx = MagicMock()
        register(ctx)
        ctx.register_platform.assert_called_once()
        kwargs = ctx.register_platform.call_args.kwargs
        assert kwargs["name"] == "team_bridge"
        assert kwargs["allowed_users_env"] == "TEAM_BRIDGE_ALLOWED_USERS"
        assert kwargs["allow_all_env"] == "TEAM_BRIDGE_ALLOW_ALL_USERS"
        adapter = kwargs["adapter_factory"](MagicMock(extra={}))
        assert isinstance(adapter, TeamBridgeAdapter)
