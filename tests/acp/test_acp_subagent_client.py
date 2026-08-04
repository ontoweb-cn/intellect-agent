"""Unit tests for ACPSubagentClient (P1a generic ACP client).

These mock ``spawn_agent_process`` to exercise the client's lifecycle
(start / new_session / prompt / permission queue / close) without a real
ACP binary. A real stdio smoke test lives alongside once a sub-agent is
available (see fake_acp_agent.py).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.acp_subagent_client import ACPSubagentClient, PermissionRequest
from acp.schema import AgentMessageChunk, TextContentBlock


def _fake_conn():
    conn = MagicMock()
    conn.initialize = AsyncMock(return_value=SimpleNamespace(agent_capabilities=None))
    conn.new_session = AsyncMock(
        return_value=SimpleNamespace(session_id="fake-session")
    )
    conn.prompt = AsyncMock(return_value=SimpleNamespace(stop_reason="end_turn"))
    conn.close_session = AsyncMock(return_value=None)
    conn.cancel = AsyncMock(return_value=None)
    conn.close = AsyncMock(return_value=None)
    return conn


def _fake_proc():
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = None
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


@pytest.fixture()
def client():
    return ACPSubagentClient(command="fake-acp", args=["--stdio"], cwd="/tmp")


@pytest.mark.asyncio
async def test_start_initializes_and_opens_session(client):
    conn = _fake_conn()
    proc = _fake_proc()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=(conn, proc))
    ctx.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "agent.acp_subagent_client.spawn_agent_process", return_value=ctx
    ):
        await client.start()
        assert client.session_id is None
        conn.initialize.assert_awaited_once()

        resp = await client.new_session(cwd="/tmp/work")
        assert resp.session_id == "fake-session"
        assert client.session_id == "fake-session"


@pytest.mark.asyncio
async def test_prompt_returns_text_and_thoughts(client):
    conn = _fake_conn()
    proc = _fake_proc()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=(conn, proc))
    ctx.__aexit__ = AsyncMock(return_value=None)

    async def _fake_session_update(session_id, update, **kwargs):
        client.host.events.put_nowait(
            SimpleNamespace(
                session_id=session_id,
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    content=TextContentBlock(type="text", text="hello back"),
                ),
            )
        )

    conn.session_update = AsyncMock(side_effect=_fake_session_update)

    with patch(
        "agent.acp_subagent_client.spawn_agent_process", return_value=ctx
    ):
        await client.start()
        await client.new_session(cwd="/tmp/work")

        # Directly queue an agent message as the host would receive it.
        await client.host.session_update(
            "fake-session",
            AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text="hello back"),
            ),
        )
        result = await client.prompt("hi")
        assert result.stop_reason == "end_turn"
        assert "hello back" in result.text


@pytest.mark.asyncio
async def test_request_permission_queues_and_resolves(client):
    conn = _fake_conn()
    proc = _fake_proc()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=(conn, proc))
    ctx.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "agent.acp_subagent_client.spawn_agent_process", return_value=ctx
    ):
        await client.start()

        # A request_permission from the host side puts a PermissionRequest on
        # the queue and awaits a decision.
        perm_task = asyncio.create_task(
            client.host.request_permission(
                "fake-session",
                MagicMock(tool_call_id="call-1"),
                [MagicMock(option_id="allow_once")],
            )
        )
        await asyncio.sleep(0.05)

        # Consume the queued request and resolve it.
        req = await client.host.perm_requests.get()
        assert isinstance(req, PermissionRequest)
        assert req.session_id == "fake-session"

        client.host.resolve_permission("fake-session", "call-1", "allow_once")
        decision = await asyncio.wait_for(perm_task, timeout=1)
        assert decision.outcome.option_id == "allow_once"


@pytest.mark.asyncio
async def test_close_terminates_process(client):
    conn = _fake_conn()
    proc = _fake_proc()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=(conn, proc))
    ctx.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "agent.acp_subagent_client.spawn_agent_process", return_value=ctx
    ):
        await client.start()
        await client.new_session(cwd="/tmp/work")
        await client.close()

    conn.close_session.assert_awaited_once()
    ctx.__aexit__.assert_awaited_once()


# ---------------------------------------------------------------------------
# Real stdio smoke tests (drive tests/acp/fake_acp_agent.py as a real ACP
# sub-agent over stdio). Marked integration so default runs skip them.
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stdio_smoke_full_lifecycle():
    import sys

    client = ACPSubagentClient(
        command=sys.executable,
        args=["-m", "tests.acp.fake_acp_agent"],
        cwd=".",
        client_version="0.0.1",
    )
    try:
        await asyncio.wait_for(client.start(), timeout=10)
        await asyncio.wait_for(client.new_session(cwd="/tmp"), timeout=5)
        result = await asyncio.wait_for(client.prompt("hello world"), timeout=10)
        assert result.stop_reason == "end_turn"
        assert "echo: hello world" in result.text
    finally:
        await client.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stdio_smoke_permission_round_trip():
    import sys

    client = ACPSubagentClient(
        command=sys.executable,
        args=["-m", "tests.acp.fake_acp_agent"],
        cwd=".",
        client_version="0.0.1",
    )

    async def _approve():
        req = await client.host.perm_requests.get()
        client.host.resolve_permission(req.session_id, req.tool_call.tool_call_id, "allow_once")

    try:
        await asyncio.wait_for(client.start(), timeout=10)
        await asyncio.wait_for(client.new_session(cwd="/tmp"), timeout=5)
        approver = asyncio.create_task(_approve())
        result = await asyncio.wait_for(
            client.prompt("needs-permission rm -rf"), timeout=10
        )
        assert result.stop_reason == "end_turn"
        await approver
    finally:
        await client.close()
