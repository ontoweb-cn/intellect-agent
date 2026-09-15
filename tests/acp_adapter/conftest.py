"""Shared fakes for the ACP adapter tests.

Kept in one place because three things here are easy to get subtly wrong and
must stay in sync with the adapter:

* ``NoopDb`` has to cover **every** SessionDB method the adapter calls. A
  missing one (``replace_messages`` was) makes ``session_manager._persist``
  log a failed-persistence warning on every turn, and that noise hides real
  failures.
* ``FakeAgent.run_conversation`` returns the result-dict shape the real agent
  produces, so the adapter's result handling is exercised for real. Pass
  ``result`` to script an outcome (truncated, failed, …).
* ``CaptureConn`` records what the adapter actually sent, which is how tests
  read back stop reasons and streamed messages.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class FakeAgent:
    """Minimal AIAgent stand-in the adapter can drive."""

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.model = "fake-model"
        self.provider = "fake-provider"
        self.enabled_toolsets = ["intellect-acp"]
        self.disabled_toolsets: list[str] = []
        self.tools: list = []
        self.valid_tool_names: set[str] = set()
        self.steers: list[str] = []
        self.runs: list[str] = []
        self._result = result

    def steer(self, text: str) -> bool:
        self.steers.append(text)
        return True

    def run_conversation(self, *, user_message, conversation_history, task_id, **kwargs):
        self.runs.append(user_message)
        messages = list(conversation_history or [])
        messages.append({"role": "user", "content": user_message})
        if self._result is not None:
            result = dict(self._result)
            result.setdefault("messages", messages)
            return result
        final = f"ran: {user_message}"
        messages.append({"role": "assistant", "content": final})
        return {"final_response": final, "messages": messages}


class CaptureConn:
    """Records every ``session_update`` and auto-allows permissions."""

    def __init__(self) -> None:
        self.updates: list[tuple] = []

    async def session_update(self, *args, **kwargs):
        if kwargs:
            self.updates.append((kwargs.get("session_id"), kwargs.get("update")))
        else:
            self.updates.append((args[0], args[1]))

    async def request_permission(self, *args, **kwargs):
        return SimpleNamespace(outcome="allow")


class NoopDb:
    """In-memory SessionDB stand-in covering the adapter's call surface."""

    def __init__(self) -> None:
        self.messages: dict[str, list] = {}

    def get_session(self, *_args, **_kwargs):
        return None

    def create_session(self, *_args, **_kwargs):
        return None

    def update_session(self, *_args, **_kwargs):
        return None

    def replace_messages(self, session_id, messages):
        self.messages[session_id] = list(messages or [])

    def get_messages_as_conversation(self, *_args, **_kwargs):
        return []


def make_agent_and_state(result: dict[str, Any] | None = None):
    """``(agent, state, fake, conn)``: a connected adapter with one session."""
    from acp_adapter.server import intellectACPAgent
    from acp_adapter.session import SessionManager

    fake = FakeAgent(result)
    manager = SessionManager(agent_factory=lambda **kwargs: fake, db=NoopDb())
    agent = intellectACPAgent(session_manager=manager)
    state = manager.create_session(cwd=".")
    conn = CaptureConn()
    agent.on_connect(conn)
    return agent, state, fake, conn
