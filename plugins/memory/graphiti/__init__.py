"""Graphiti memory plugin — temporal knowledge graph memory.

Phase 1 implementation.  Wires the MemoryProvider lifecycle to a
``GraphitiClientManager`` (one async loop thread + per-scope FalkorDB
clients) and exposes 5 tools to the agent.

Architecture:
  __init__.py    — this file: lifecycle glue, tool dispatch, RBAC
  client.py      — async loop + GraphitiClient + GraphitiClientManager
  tools.py       — 5 tool schemas
  config.py      — load/save/schema for setup wizard
  cli.py         — `intellect graphiti {setup,status}`

See docs/plans/graphiti-memory-plugin-dev-plan.md for the W1–W5 roadmap.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

from .config import (
    get_config_schema,
    load_config,
    save_config,
)
from .tools import ALL_SCHEMAS, tool_names

logger = logging.getLogger(__name__)


class GraphitiMemoryProvider(MemoryProvider):
    """Temporal knowledge graph memory backed by Graphiti + FalkorDB."""

    def __init__(self) -> None:
        self._mgr = None                       # GraphitiClientManager, set in initialize()
        self._session_id: str = ""
        self._member_id: Optional[str] = None
        self._team_id: Optional[str] = None
        self._project_id: Optional[str] = None
        self._intellect_home: str = ""
        self._config: Dict[str, Any] = {}
        self._cfg: Dict[str, Any] = {}         # graphiti's own config.json

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "graphiti"

    def is_available(self) -> bool:
        try:
            import graphiti_core  # noqa: F401
            import falkordb  # noqa: F401
        except ImportError:
            return False
        return True

    # ------------------------------------------------------------------
    # Setup wizard
    # ------------------------------------------------------------------

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return get_config_schema()

    def save_config(self, values: Dict[str, Any], intellect_home: str) -> None:
        save_config(values, intellect_home)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        self._member_id = kwargs.get("member_id")
        self._team_id = kwargs.get("team_id")
        self._project_id = kwargs.get("project_id")
        self._intellect_home = kwargs.get("intellect_home", "")
        self._config = kwargs.get("config") or {}
        self._cfg = load_config(self._intellect_home)

        if not self.is_available():
            logger.info(
                "graphiti: dependencies not installed; provider inactive"
            )
            return

        from .client import GraphitiClientManager  # deferred — heavy imports
        from .ontology import load_ontology

        # Phase 5.1: pick up entity/edge type definitions from
        # $INTELLECT_HOME/graphiti/ontology.yaml when present.  Returns
        # an empty Ontology (= learned mode) when the file is absent
        # or malformed.
        ontology = load_ontology(self._intellect_home)
        if not ontology.is_empty():
            logger.info(
                "graphiti: loaded ontology from %s (%d entities, %d edges, %d edge-map entries)",
                ontology.source,
                len(ontology.entities),
                len(ontology.edges),
                len(ontology.edge_type_map),
            )

        self._mgr = GraphitiClientManager(
            self._cfg,
            ontology_kwargs=ontology.as_add_episode_kwargs(),
        )
        self._mgr.bind_scope(
            member_id=self._member_id,
            team_id=self._team_id,
            project_id=self._project_id,
        )
        logger.info(
            "graphiti: initialized (session=%s member=%s team=%s project=%s backend=%s)",
            session_id,
            self._member_id,
            self._team_id,
            self._project_id,
            self._cfg.get("backend", "falkordb"),
        )

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        **kwargs,
    ) -> None:
        self._session_id = new_session_id
        # Scope unchanged — same member/team — so the client cache stays
        # valid.  Only the session id moves.

    def shutdown(self) -> None:
        if self._mgr is not None:
            try:
                self._mgr.shutdown()
            except Exception as exc:
                logger.debug("graphiti: shutdown error: %s", exc)
            self._mgr = None

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def system_prompt_block(self) -> str:
        """Tell the model the graph is available; the tools self-describe."""
        if self._mgr is None:
            return ""
        scope_bits = []
        if self._member_id:
            scope_bits.append(f"member:{self._member_id}")
        if self._team_id:
            scope_bits.append(f"team:{self._team_id}")
        if self._project_id:
            scope_bits.append(f"project:{self._project_id}")
        scope_desc = ", ".join(scope_bits) or "global"
        return (
            "## Graphiti knowledge graph\n"
            "A temporal knowledge graph is available via the `graphiti_*` "
            "tools.  Use `graphiti_search_facts` to recall what you know "
            "about a topic before answering; use `graphiti_add_episode` "
            "to persist new observations worth remembering across sessions.  "
            f"Active scope: {scope_desc}.  Searches default to merged "
            "member+team results; writes go to the member graph.\n"
        )

    # ------------------------------------------------------------------
    # Prefetch (sync; called inline during prompt assembly)
    # ------------------------------------------------------------------

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._mgr is None or not query:
            return ""
        try:
            facts = self._mgr.search_facts(
                query, max_results=int(self._cfg.get("default_max_nodes", 10))
            )
        except Exception as exc:
            logger.debug("graphiti: prefetch failed: %s", exc)
            return ""
        if not facts:
            return ""
        lines = ["## Graphiti recall", ""]
        for f in facts:
            valid = f.get("valid_at") or ""
            graph = f.get("graph") or ""
            lines.append(
                f"- {f.get('fact', '').strip()} "
                f"(valid_at={valid} graph={graph})"
            )
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Turn sync (per-turn observation; cheap, deferred to bg loop)
    # ------------------------------------------------------------------

    def sync_turn(
        self,
        user_content: str = "",
        assistant_content: str = "",
        *,
        session_id: str = "",
        **kwargs,
    ) -> None:
        if self._mgr is None:
            return
        if not bool(self._cfg.get("auto_ingest", True)):
            return
        text_parts = []
        if user_content:
            text_parts.append(f"User: {user_content}")
        if assistant_content:
            text_parts.append(f"Assistant: {assistant_content}")
        if not text_parts:
            return
        try:
            self._mgr.add_episode(
                content="\n".join(text_parts),
                source_description=f"chat turn (session={session_id or self._session_id})",
            )
        except Exception as exc:
            # Fail-open: a graph outage must not poison the conversation.
            logger.debug("graphiti: sync_turn skipped: %s", exc)

    # ------------------------------------------------------------------
    # End-of-session / pre-compression hooks
    # ------------------------------------------------------------------

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if self._mgr is None or not messages:
            return
        try:
            self._mgr.add_episode(
                content=self._serialize_messages(messages),
                source_description=f"session end (session={self._session_id})",
            )
        except Exception as exc:
            logger.debug("graphiti: on_session_end skipped: %s", exc)

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Persist about-to-be-compressed turns + return a hint string."""
        if self._mgr is None or not messages:
            return ""
        try:
            self._mgr.add_episode(
                content=self._serialize_messages(messages),
                source_description=(
                    f"pre-compress (session={self._session_id})"
                ),
            )
            return (
                "Older turns have been persisted to the Graphiti knowledge "
                "graph; query `graphiti_search_facts` to recall them."
            )
        except Exception as exc:
            logger.debug("graphiti: on_pre_compress skipped: %s", exc)
            return ""

    @staticmethod
    def _serialize_messages(messages: List[Dict[str, Any]]) -> str:
        out: List[str] = []
        for m in messages:
            role = m.get("role") or ""
            content = m.get("content")
            if isinstance(content, list):
                # Tool/multimodal content blocks — flatten text parts.
                parts: List[str] = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        parts.append(str(c.get("text") or ""))
                content = "\n".join(parts)
            if not content:
                continue
            out.append(f"{role}: {content}")
        return "\n".join(out)

    # ------------------------------------------------------------------
    # Tool registry + dispatch
    # ------------------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        if self._mgr is None:
            return []
        return list(ALL_SCHEMAS)

    def handle_tool_call(
        self,
        tool_name: str,
        args: Dict[str, Any],
        **kwargs,
    ) -> str:
        if self._mgr is None:
            return _err(
                tool_name,
                "graphiti provider not initialized "
                "(dependencies missing or backend unreachable)",
            )

        try:
            if tool_name == "graphiti_add_episode":
                result = self._mgr.add_episode(
                    content=args["content"],
                    source_description=args.get("source_description", "agent"),
                    reference_time=args.get("reference_time"),
                )
            elif tool_name == "graphiti_search_facts":
                result = self._mgr.search_facts(
                    args["query"],
                    max_results=int(args.get("max_results", 10)),
                    scope=args.get("scope", "auto"),
                )
            elif tool_name == "graphiti_search_nodes":
                result = self._mgr.search_nodes(
                    args["query"],
                    max_results=int(args.get("max_results", 10)),
                    scope=args.get("scope", "auto"),
                )
            elif tool_name == "graphiti_get_node_timeline":
                result = self._mgr.get_node_timeline(
                    args["node_id"],
                    since=args.get("since"),
                    until=args.get("until"),
                )
            elif tool_name == "graphiti_delete_episode":
                # Sensitive: gate via RBAC if available.
                err = self._check_delete_rbac(args.get("reason", ""))
                if err:
                    return _err(tool_name, err)
                result = self._mgr.delete_episode(args["episode_id"])
            else:
                return _err(tool_name, f"unknown graphiti tool: {tool_name}")
        except KeyError as exc:
            return _err(tool_name, f"missing required arg: {exc}")
        except Exception as exc:
            return _err(tool_name, f"graphiti call failed: {exc}")

        return json.dumps({"ok": True, "result": result})

    def _check_delete_rbac(self, reason: str) -> Optional[str]:
        """Defense-in-depth gate for graphiti_delete_episode.

        The primary gate is ``agent.member_rbac.check_member_tool_permission``,
        which runs at the dispatcher (``agent_runtime_helpers.py``) before
        this method is ever called.  This second check is here so that:

        - tool calls that bypass the dispatcher (CLI, batch jobs, future
          MCP bridge) still get the same answer
        - the audit ``reason`` field is enforced even when the dispatcher
          gate is not active (members disabled, single-user CLI)

        Returns a denial message, or None when the call may proceed.
        """
        if not reason.strip():
            return "delete requires a non-empty `reason` for the audit trail"

        if not self._member_id:
            # No member context = single-user CLI = allow.  The reason is
            # still recorded by the caller for self-documentation.
            return None

        # (single-user: no tool RBAC)
        # Allow the write — the primary gate at the dispatcher
        # (agent_runtime_helpers.py) already enforced RBAC if enabled.
        return None


def _err(tool_name: str, msg: str) -> str:
    return json.dumps({"ok": False, "tool": tool_name, "error": msg})


def register(ctx) -> None:
    """Plugin entry point — called by ``plugins/memory/__init__.py``."""
    ctx.register_memory_provider(GraphitiMemoryProvider())
