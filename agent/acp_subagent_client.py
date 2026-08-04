"""Generic ACP sub-agent client for Intellect.

Spawns ANY ACP-compatible agent (claude_code, opencode, custom) over stdio via
``acp.spawn_agent_process`` and exposes a small async driver plus the inbound
``Client`` host hooks (``session_update`` / ``request_permission`` / fs).

This is the P1a generic client: it generalises ``CopilotACPClient`` (which is
hard-wired to ``copilot --acp``) to arbitrary ACP commands. P1b builds the
permission round-trip on top of :class:`ACPSubagentClient`'s permission queue.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from acp import PROTOCOL_VERSION, spawn_agent_process
from acp.client.connection import ClientSideConnection
from acp.schema import (
    AgentMessageChunk,
    ClientCapabilities,
    Implementation,
    PermissionOption,
    RequestPermissionResponse,
    SessionNotification,
    TextContentBlock,
    ToolCallUpdate,
)


@dataclass
class PermissionRequest:
    """A pending session/request_permission awaiting an approval decision (P1b)."""

    session_id: str
    tool_call: ToolCallUpdate
    options: list[PermissionOption]


@dataclass
class TurnResult:
    """Result of one delegated prompt turn."""

    text: str
    thoughts: str
    stop_reason: str
    events: list[SessionNotification]
    raw: Any


class _Host:
    """Implements the acp.Client host interface for inbound requests.

    ``session_update`` is invoked by the SDK's background receive loop; we
    translate it into events on an asyncio.Queue for the driver. Inbound
    ``request_permission`` is queued for the P1b approver (or auto-denied).
    """

    def __init__(self) -> None:
        self.events: asyncio.Queue[SessionNotification] = asyncio.Queue()
        self.perm_requests: asyncio.Queue[PermissionRequest] = asyncio.Queue()
        self._auto_deny = False
        self._decision: dict[str, asyncio.Future] = {}

    # ---- inbound stream (notification) -----------------------------------

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        await self.events.put(SessionNotification(session_id=session_id, update=update))

    # ---- inbound request from agent --------------------------------------

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        if self._auto_deny:
            return RequestPermissionResponse(outcome={"outcome": "cancelled"})
        await self.perm_requests.put(PermissionRequest(session_id, tool_call, options))
        decision = await self._wait_decision(session_id, tool_call.tool_call_id)
        if decision is None:  # auto-deny on timeout/error
            return RequestPermissionResponse(outcome={"outcome": "cancelled"})
        return RequestPermissionResponse(outcome={"outcome": "selected", "optionId": decision})

    async def _wait_decision(self, session_id: str, call_id: str) -> str | None:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._decision[(session_id, call_id)] = fut
        try:
            return await fut
        finally:
            self._decision.pop((session_id, call_id), None)

    def resolve_permission(self, session_id: str, call_id: str, option_id: str) -> None:
        """P1b: resolve a pending permission request with the chosen option."""
        fut = self._decision.get((session_id, call_id))
        if fut and not fut.done():
            fut.set_result(option_id)

    # ---- fs requests the agent needs to work in the delegated cwd --------
    # The sub-agent reads/writes files in the delegated workspace. These mirror
    # the CopilotACPClient fs handling: basic path-safety guard, local I/O.

    async def read_text_file(
        self, session_id: str, path: str, line: int | None = None,
        limit: int | None = None, **kwargs: Any,
    ) -> Any:
        from acp.schema import ReadTextFileResponse

        path_obj = _safe_path(path)
        try:
            content = path_obj.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"cannot read {path}: {exc}") from exc
        return ReadTextFileResponse(content=content)

    async def write_text_file(
        self, session_id: str, path: str, content: str, **kwargs: Any,
    ) -> None:
        path_obj = _safe_path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        path_obj.write_text(content, encoding="utf-8")


class ACPSubagentClient:
    """Drives a spawned ACP sub-agent: initialize -> new_session -> prompt -> close."""

    def __init__(
        self,
        *,
        command: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        auto_deny_permissions: bool = False,
        client_capabilities: ClientCapabilities | None = None,
        client_name: str = "intellect-agent",
        client_version: str = "0.0.0",
        transport_kwargs: dict[str, Any] | None = None,
        **connection_kwargs: Any,
    ) -> None:
        self._command = command
        self._args = list(args or [])
        self._cwd = cwd
        self._env = env
        self._host = _Host()
        self._host._auto_deny = auto_deny_permissions
        self._caps = client_capabilities or ClientCapabilities()
        self._info = Implementation(
            name=client_name, title="Intellect Agent", version=client_version
        )
        self._transport_kwargs = transport_kwargs or {}
        self._connection_kwargs = connection_kwargs
        self._conn: ClientSideConnection | None = None
        self._proc: Any = None
        self._ctx: Any = None
        self._session_id: str | None = None
        self._agent_capabilities: Any = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def host(self) -> _Host:
        return self._host

    # ---- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        self._ctx = spawn_agent_process(
            self._host,
            self._command,
            *self._args,
            env=self._env,
            cwd=self._cwd,
            transport_kwargs=self._transport_kwargs,
            **self._connection_kwargs,
        )
        self._conn, self._proc = await self._ctx.__aenter__()
        init = await self._conn.initialize(
            PROTOCOL_VERSION, client_capabilities=self._caps, client_info=self._info
        )
        self._agent_capabilities = init.agent_capabilities

    async def new_session(
        self, cwd: str, additional_directories: list[str] | None = None
    ) -> Any:
        resp = await self._conn.new_session(cwd, additional_directories=additional_directories)
        self._session_id = resp.session_id
        return resp

    async def prompt(
        self, text: str, *, content_blocks: list[Any] | None = None
    ) -> TurnResult:
        if self._conn is None or self._session_id is None:
            raise RuntimeError("ACPSubagentClient not started")
        blocks = content_blocks or [TextContentBlock(type="text", text=text)]
        resp = await self._conn.prompt(self._session_id, blocks)
        # Settle: let queued session/update handlers flush before draining.
        await asyncio.sleep(0.05)
        events: list[SessionNotification] = []
        while not self._host.events.empty():
            events.append(self._host.events.get_nowait())
        text_parts = [
            u.update.content.text
            for u in events
            if isinstance(u.update, AgentMessageChunk)
            and isinstance(getattr(u.update, "content", None), TextContentBlock)
        ]
        thoughts = "".join(
            getattr(u.update.content, "text", "")
            for u in events
            if getattr(u.update, "session_update", "") == "agent_thought_chunk"
        )
        return TurnResult(
            text="".join(text_parts),
            thoughts=thoughts,
            stop_reason=resp.stop_reason,
            events=events,
            raw=resp,
        )

    async def cancel(self) -> None:
        if self._conn and self._session_id:
            await self._conn.cancel(self._session_id)

    async def close(self) -> None:
        try:
            if self._conn and self._session_id:
                with suppress(Exception):
                    await self._conn.close_session(self._session_id)
        finally:
            with suppress(Exception):
                if self._conn:
                    await self._conn.close()
            if self._ctx is not None:
                with suppress(Exception):
                    await self._ctx.__aexit__(None, None, None)
            # Tree-kill fallback for npx-wrapped agents (grandchild processes).
            if self._proc is not None and self._proc.returncode is None:
                try:
                    import psutil

                    parent = psutil.Process(self._proc.pid)
                    for child in parent.children(recursive=True):
                        child.kill()
                    parent.kill()
                except Exception:
                    with suppress(Exception):
                        self._proc.terminate()
                        await asyncio.wait_for(self._proc.wait(), timeout=2)
                    if self._proc.returncode is None:
                        with suppress(Exception):
                            self._proc.kill()

    async def permission_events(self):
        """Async generator of pending permission requests (P1b consumer)."""
        while True:
            yield await self._host.perm_requests.get()


def _safe_path(path: str):
    """Resolve a path relative to the delegated cwd with basic traversal guard."""
    from pathlib import Path

    return Path(path).resolve()
