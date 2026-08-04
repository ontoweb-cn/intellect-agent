"""A minimal fake ACP server for smoke-testing ACPSubagentClient.

Runs over stdio as ``python -m tests.acp.fake_acp_agent`` and answers the
core protocol methods (initialize / new_session / prompt / close_session)
so ACPSubagentClient's full lifecycle can be exercised end-to-end without a
real claude_code / opencode binary.
"""

import asyncio
import json
import sys

from acp import Agent, run_agent
from acp.schema import (
    AgentMessageChunk,
    ClientCapabilities,
    CloseSessionResponse,
    Implementation,
    InitializeResponse,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    TextContentBlock,
    ToolCallUpdate,
)


class FakeACPAgent(Agent):
    def __init__(self) -> None:
        self._sessions: set[str] = set()
        self._conn = None

    def on_connect(self, conn) -> None:
        # AgentSideConnection injects itself here (see SDK
        # AgentSideConnection.__init__: on_connect(agent, conn)).
        self._conn = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs,
    ) -> InitializeResponse:
        return InitializeResponse(
            agent_info=Implementation(name="fake-acp-agent", title="Fake", version="1.0.0"),
            protocol_version=protocol_version,
        )

    async def new_session(self, cwd: str, **kwargs) -> NewSessionResponse:
        session_id = f"fake-{len(self._sessions)}"
        self._sessions.add(session_id)
        return NewSessionResponse(session_id=session_id)

    async def prompt(self, session_id: str, prompt: list, **kwargs) -> PromptResponse:
        text = ""
        for block in prompt:
            if isinstance(block, TextContentBlock):
                text += block.text
        if text.startswith("needs-permission"):
            # Ask the host for permission to run a shell command.
            if self._conn is not None:
                outcome = await self._conn.request_permission(
                    session_id=session_id,
                    tool_call=ToolCallUpdate(
                        tool_call_id="perm-1",
                        title="Run shell command",
                        kind="execute",
                        status="in_progress",
                        raw_input="rm -rf /tmp/x",
                    ),
                    options=[PermissionOption(option_id="allow_once", kind="allow_once", name="Allow once")],
                )
                if outcome.outcome == "cancelled":
                    return PromptResponse(stop_reason="end_turn")
                return PromptResponse(stop_reason="end_turn")
        if self._conn is not None:
            await self._conn.session_update(
                session_id=session_id,
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    content=TextContentBlock(type="text", text=f"echo: {text}"),
                ),
            )
        return PromptResponse(stop_reason="end_turn")

    async def close_session(self, session_id: str, **kwargs) -> CloseSessionResponse | None:
        self._sessions.discard(session_id)
        return CloseSessionResponse()


def main() -> None:
    asyncio.run(run_agent(FakeACPAgent()))


if __name__ == "__main__":
    main()
