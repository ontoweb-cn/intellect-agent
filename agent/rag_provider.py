"""Abstract base class for pluggable RAG providers.

RAG providers give the agent document knowledge retrieval alongside
memory providers.  The RAGManager enforces a one-external-provider
limit (parallel to MemoryManager).

Registration:
  Plugins ship in plugins/rag/<name>/ and are activated via
  the rag.provider config key.

Lifecycle (called by RAGManager, wired in agent_init.py):
  initialize()          — connect, health check, bind scope
  system_prompt_block() — static text for the system prompt
  prefetch(query)       — background recall before each turn
  sync_turn(user, asst) — optional post-turn ingest (usually off)
  get_tool_schemas()    — tool schemas to expose to the model
  handle_tool_call()    — dispatch a tool call
  shutdown()            — clean exit
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class RAGProvider(ABC):
    """Abstract base class for RAG providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier (e.g. 'lightrag')."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if configured and ready (no network calls)."""

    @abstractmethod
    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialize for a session.

        kwargs may include intellect_home, platform, member_id, team_id,
        project_id, agent_context, config (agent config dict), etc.
        """

    def system_prompt_block(self) -> str:
        """Static provider instructions for the system prompt."""
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return raw recall text for the upcoming turn (unfenced)."""
        return ""

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: List[Dict[str, Any]] | None = None,
    ) -> None:
        """Optional post-turn ingest.  Default no-op."""

    @abstractmethod
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """OpenAI-style function schemas."""

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        """Handle a tool call; must return a JSON string."""
        return json.dumps({"success": False, "error": f"unknown tool: {tool_name}"})

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Optional end-of-session ingest hook."""

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Optional pre-compression ingest hook; may return a hint string."""
        return ""

    def shutdown(self) -> None:
        """Clean shutdown."""
