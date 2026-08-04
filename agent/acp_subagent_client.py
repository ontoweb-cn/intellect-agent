"""Generic ACP sub-agent client for Intellect.

Spawns ANY ACP-compatible agent (claude_code, opencode, custom) over stdio via
``acp.spawn_agent_process`` and exposes a small async driver plus the inbound
``Client`` host hooks (``session_update`` / ``request_permission`` / fs).

This is the P1a generic client: it generalises ``CopilotACPClient`` (which is
hard-wired to ``copilot --acp``) to arbitrary ACP commands. P1b builds the
permission round-trip on top of :class:`SuspendedPermission` (see
``docs/plans/2026-08-04-acp-p1b-permission-interface.md`` for the agreed
interface contract).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

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

# How long a sub-agent's permission request may wait for an approval decision
# before the host auto-denies it (prevents an unresolved request from wedging
# the sub-agent's turn forever when no approver is connected).
_PERMISSION_TIMEOUT = 60.0
# Max time to wait for a sub-agent subprocess to exit gracefully on close.
_SHUTDOWN_TIMEOUT = 5.0


@dataclass
class SuspendedPermission:
    """A pending permission request surfaced to the caller (P1b contract, M6).

    Mirrors the interface agreed in docs/plans/2026-08-04-acp-p1b-permission-interface.md:
    ``request_id`` is the stable handle used by :meth:`respond` to resolve it.
    """

    session_id: str
    request_id: str
    tool_name: str
    description: str
    severity: str = "warning"
    exact_target: str | None = None
    pattern_target: str | None = None
    options: list[PermissionOption] = field(default_factory=list)
    expires_at: float | None = None
    _resolve: Callable[[str], Awaitable[None]] | None = None


@dataclass
class PermissionRequest:
    """Internal pending-permission state (session/tool/options + resolve future).

    Kept as the internal representation; :class:`SuspendedPermission` is the
    public surface exposed via :meth:`ACPSubagentClient.request_permission`.
    """

    session_id: str
    tool_call: ToolCallUpdate
    options: list[PermissionOption]
    request_id: str = field(default_factory=lambda: f"perm-{uuid.uuid4().hex}")


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
    ``request_permission`` is queued for the P1b approver (or auto-denied on
    timeout). fs methods confine reads/writes to the session cwd and apply the
    same file-safety / redaction the CopilotACPClient applies.
    """

    def __init__(self) -> None:
        self.events: asyncio.Queue[SessionNotification] = asyncio.Queue()
        self.perm_requests: asyncio.Queue[PermissionRequest] = asyncio.Queue()
        self._auto_deny = False
        self._decision: dict[str, asyncio.Future] = {}
        self._session_cwd: str | None = None

    def set_session_cwd(self, cwd: str) -> None:
        """Record the delegated session cwd for fs path confinement."""
        self._session_cwd = cwd

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
        req = PermissionRequest(session_id, tool_call, options)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._decision[req.request_id] = fut
        await self.perm_requests.put(req)
        try:
            # Auto-deny if no approver resolves the request in time, so an
            # unanswered permission request can never wedge the sub-agent turn.
            decision = await asyncio.wait_for(fut, timeout=_PERMISSION_TIMEOUT)
        except asyncio.TimeoutError:
            return RequestPermissionResponse(outcome={"outcome": "cancelled"})
        finally:
            self._decision.pop(req.request_id, None)
        if decision is None:
            return RequestPermissionResponse(outcome={"outcome": "cancelled"})
        return RequestPermissionResponse(outcome={"outcome": "selected", "optionId": decision})

    def resolve_permission(self, request_id: str, option_id: str) -> None:
        """Resolve a pending permission request by its stable ``request_id``."""
        fut = self._decision.get(request_id)
        if fut and not fut.done():
            fut.set_result(option_id)

    # ---- fs requests the agent needs to work in the delegated cwd --------
    # Same confinement + file-safety + redaction policy as CopilotACPClient:
    # absolute paths only, confined to the session cwd, protected/credential
    # writes denied, secrets redacted, line/limit honored.

    async def read_text_file(
        self, session_id: str, path: str, line: int | None = None,
        limit: int | None = None, **kwargs: Any,
    ) -> Any:
        from acp.schema import ReadTextFileResponse

        from agent.copilot_acp_client import _ensure_path_within_cwd
        from agent.file_safety import get_read_block_error
        from agent.redact import redact_sensitive_text

        path_obj = _ensure_path_within_cwd(str(path), self._session_cwd or ".")
        block_error = get_read_block_error(str(path_obj))
        if block_error:
            raise PermissionError(block_error)
        try:
            content = path_obj.read_text(encoding="utf-8")
        except FileNotFoundError:
            content = ""
        except OSError as exc:
            raise ValueError(f"cannot read {path}: {exc}") from exc
        if isinstance(line, int) and line > 1:
            lines = content.splitlines(keepends=True)
            start = line - 1
            end = start + limit if isinstance(limit, int) and limit > 0 else None
            content = "".join(lines[start:end])
        if content:
            content = redact_sensitive_text(content, force=True)
        return ReadTextFileResponse(content=content)

    async def write_text_file(
        self, session_id: str, path: str, content: str, **kwargs: Any,
    ) -> None:
        from agent.copilot_acp_client import _ensure_path_within_cwd
        from agent.file_safety import is_write_denied

        path_obj = _ensure_path_within_cwd(str(path), self._session_cwd or ".")
        if is_write_denied(str(path_obj)):
            raise PermissionError(
                f"Write denied: '{path_obj}' is a protected system/credential file."
            )
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        path_obj.write_text(str(content), encoding="utf-8")


class ACPSubagentClient:
    """Drives a spawned ACP sub-agent: initialize -> new_session -> prompt -> close.

    Permission round-trip (P1b): the caller consumes :meth:`permission_events`
    for :class:`SuspendedPermission` objects and resolves them via
    :meth:`respond`. This matches the M6 interface contract so the same approval
    UX works on both intellect-team and intellect-agent.
    """

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
        self._host.set_session_cwd(cwd)
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
        # Kill the process tree (npx-wrapped agents have a grandchild) BEFORE
        # the SDK's transport __aexit__ reaps the direct child — otherwise
        # returncode is already set and the tree-kill guard is skipped, orphaning
        # the grandchild. All awaits are bounded so a wedged sub-agent can't
        # hang close().
        try:
            if self._proc is not None and self._proc.returncode is None:
                try:
                    import psutil

                    parent = psutil.Process(self._proc.pid)
                    for child in parent.children(recursive=True):
                        with suppress(Exception):
                            child.kill()
                    with suppress(Exception):
                        parent.kill()
                except Exception:
                    with suppress(Exception):
                        self._proc.terminate()
                        await asyncio.wait_for(self._proc.wait(), timeout=2)
        finally:
            try:
                if self._conn and self._session_id:
                    with suppress(Exception):
                        await asyncio.wait_for(
                            self._conn.close_session(self._session_id), timeout=_SHUTDOWN_TIMEOUT
                        )
            finally:
                with suppress(Exception):
                    if self._conn:
                        await asyncio.wait_for(self._conn.close(), timeout=_SHUTDOWN_TIMEOUT)
                if self._ctx is not None:
                    with suppress(Exception):
                        await asyncio.wait_for(
                            self._ctx.__aexit__(None, None, None), timeout=_SHUTDOWN_TIMEOUT
                        )
                if self._proc is not None and self._proc.returncode is None:
                    with suppress(Exception):
                        self._proc.kill()

    async def permission_events(self):
        """Async generator of pending :class:`SuspendedPermission` (P1b consumer)."""
        while True:
            req = await self._host.perm_requests.get()
            yield self._to_suspended(req)

    async def request_permission(self, perm: SuspendedPermission) -> str:
        """Present a permission request and await the caller's decision.

        Returns the chosen ``option_id``. This is the public P1b surface: the
        caller builds a :class:`SuspendedPermission`, awaits this method, and the
        resolved option is returned to the sub-agent.
        """
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._host._decision[perm.request_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=_PERMISSION_TIMEOUT)
        except asyncio.TimeoutError:
            return "deny"
        finally:
            self._host._decision.pop(perm.request_id, None)

    async def respond(self, request_id: str, option_id: str) -> None:
        """Resolve a pending permission request by its stable ``request_id``.

        This is the M6 interface contract: the caller replies with the exact
        ``request_id`` it saw on the :class:`SuspendedPermission` and an
        ``option_id`` from ``options``.
        """
        self._host.resolve_permission(request_id, option_id)

    def _to_suspended(self, req: PermissionRequest) -> SuspendedPermission:
        """Convert an internal PermissionRequest into the public model."""
        tool = req.tool_call
        raw = str(getattr(tool, "raw_input", "") or "")
        return SuspendedPermission(
            session_id=req.session_id,
            request_id=req.request_id,
            tool_name=str(getattr(tool, "title", "") or tool.tool_call_id or "tool"),
            description=raw,
            severity="warning",
            exact_target=raw or None,
            options=list(req.options),
            _resolve=lambda option_id: self.respond(req.request_id, option_id),
        )


# ---------------------------------------------------------------------------
# OpenAI-compatible sync facade (P1a provider integration)
# ---------------------------------------------------------------------------
# create_openai_client() in agent_runtime_helpers.py returns a synchronous
# OpenAI-compatible client. This facade wraps ACPSubagentClient (async) so a
# generic ACP command can be used as an Intellect model provider: each
# chat.completions.create() spawns one sub-agent turn and returns the reply.

class _ACPSubagentChatCompletions:
    def __init__(self, facade: "ACPSubagentOpenAIFacade") -> None:
        self._facade = facade

    def create(self, **kwargs: Any) -> Any:
        return self._facade._create_chat_completion(**kwargs)


class _ACPSubagentChat:
    def __init__(self, facade: "ACPSubagentOpenAIFacade") -> None:
        self.completions = _ACPSubagentChatCompletions(facade)


class ACPSubagentOpenAIFacade:
    """Synchronous OpenAI-compatible facade over a generic ACP sub-agent.

    Each ``create()`` runs one delegated turn: spawn the ACP command, send the
    rendered prompt, collect the reply, then tear the sub-process down.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        acp_command: str | None = None,
        acp_args: list[str] | None = None,
        acp_cwd: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        auto_deny_permissions: bool = True,
        **_: Any,
    ) -> None:
        from agent.copilot_acp_client import _resolve_command

        self.api_key = api_key or "acp-subagent"
        self.base_url = base_url or "acp://generic"
        self._command = acp_command or command or _resolve_command()
        self._args = list(acp_args or args or [])
        self._cwd = str(Path(acp_cwd or os.getcwd()).resolve())
        self._auto_deny = auto_deny_permissions
        self.chat = _ACPSubagentChat(self)
        self.is_closed = False

    def _create_chat_completion(
        self,
        *,
        model: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        timeout: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **_: Any,
    ) -> Any:
        from types import SimpleNamespace

        from agent.copilot_acp_client import _extract_tool_calls_from_text, _format_messages_as_prompt

        prompt_text = _format_messages_as_prompt(
            messages or [], model=model, tools=tools, tool_choice=tool_choice
        )
        timeout_s = float(timeout) if isinstance(timeout, (int, float)) else 120.0
        response_text, reasoning_text = self._run_prompt(prompt_text, timeout_s)
        tool_calls, cleaned_text = _extract_tool_calls_from_text(response_text)
        usage = SimpleNamespace(
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        )
        assistant_message = SimpleNamespace(
            content=cleaned_text, tool_calls=tool_calls,
            reasoning=reasoning_text or None, reasoning_content=reasoning_text or None,
            reasoning_details=None,
        )
        choice = SimpleNamespace(
            message=assistant_message,
            finish_reason="tool_calls" if tool_calls else "stop",
        )
        return SimpleNamespace(
            choices=[choice], usage=usage, model=model or "acp-subagent"
        )

    def _run_prompt(self, prompt_text: str, timeout_s: float) -> tuple[str, str]:
        import asyncio

        async def _one_turn() -> tuple[str, str]:
            client = ACPSubagentClient(
                command=self._command, args=self._args, cwd=self._cwd,
                auto_deny_permissions=self._auto_deny, client_version="0.0.1",
            )
            try:
                await asyncio.wait_for(client.start(), timeout=timeout_s)
                await asyncio.wait_for(client.new_session(cwd=self._cwd), timeout=timeout_s)
                result = await asyncio.wait_for(client.prompt(prompt_text), timeout=timeout_s)
                return result.text, result.thoughts
            finally:
                await client.close()

        return asyncio.run(_one_turn())

    def close(self) -> None:
        self.is_closed = True
