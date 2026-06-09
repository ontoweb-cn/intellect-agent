"""Graphiti tool schemas + dispatch helpers.

The 5 ``graphiti_*`` tools exposed to the agent.  Kept in its own module
so the schemas are inspectable / testable without importing
``graphiti_core`` (which pulls in heavy async deps).
"""

from __future__ import annotations

from typing import Any, Dict, List

# Canonical scope values.  Shared by client.py, cli.py, mcp_server.py,
# and the JSON schemas below.  Tuple so no caller can accidentally mutate it.
SCOPE_CHOICES: tuple[str, ...] = ("auto", "member", "team", "project", "all")

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

ADD_EPISODE_SCHEMA: Dict[str, Any] = {
    "name": "graphiti_add_episode",
    "description": (
        "Persist an observation into the temporal knowledge graph.  Use "
        "when the conversation surfaces a fact worth remembering across "
        "sessions (preferences, relationships, decisions, events).  "
        "Graphiti extracts entities + relations and timestamps them "
        "bi-temporally (when said vs when valid)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The observation text.  Plain prose; Graphiti handles extraction.",
            },
            "source_description": {
                "type": "string",
                "description": "Where this came from (e.g. 'chat turn', 'user note', 'web fetch').  Default: 'agent'.",
            },
            "reference_time": {
                "type": "string",
                "description": "ISO-8601 timestamp the episode VALIDITY refers to (not when said).  Default: now.",
            },
        },
        "required": ["content"],
    },
}

SEARCH_FACTS_SCHEMA: Dict[str, Any] = {
    "name": "graphiti_search_facts",
    "description": (
        "Hybrid search over the knowledge graph: semantic (vector) + BM25 "
        "+ graph traversal.  Returns ranked facts with provenance and "
        "validity windows.  Use when the agent needs to recall what's "
        "known about a topic / person / project."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language query."},
            "max_results": {
                "type": "integer",
                "description": "Cap on returned facts.  Default 10, max 50.",
            },
            "scope": {
                "type": "string",
                "enum": list(SCOPE_CHOICES),
                "description": (
                    "Which graphs to search.  'auto' = member + team merged "
                    "(default).  'all' requires admin."
                ),
            },
        },
        "required": ["query"],
    },
}

SEARCH_NODES_SCHEMA: Dict[str, Any] = {
    "name": "graphiti_search_nodes",
    "description": (
        "Search entity nodes (people, projects, concepts) by name or "
        "description.  Returns node summaries + connection counts.  "
        "Cheaper than search_facts when you just need to find an entity."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "description": "Default 10."},
            "scope": {
                "type": "string",
                "enum": list(SCOPE_CHOICES),
            },
        },
        "required": ["query"],
    },
}

GET_NODE_TIMELINE_SCHEMA: Dict[str, Any] = {
    "name": "graphiti_get_node_timeline",
    "description": (
        "Bi-temporal timeline for one entity: all facts about it ordered "
        "by validity window, with invalidation events.  Use to answer "
        "'what did we know about X over time' or 'when did Y change'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "node_id": {"type": "string", "description": "Entity node UUID."},
            "since": {
                "type": "string",
                "description": "ISO-8601 lower bound for valid_at.  Optional.",
            },
            "until": {
                "type": "string",
                "description": "ISO-8601 upper bound for valid_at.  Optional.",
            },
        },
        "required": ["node_id"],
    },
}

DELETE_EPISODE_SCHEMA: Dict[str, Any] = {
    "name": "graphiti_delete_episode",
    "description": (
        "Remove an episode and its derived facts from the graph.  "
        "Restricted: only the episode owner or member-admins may delete.  "
        "Audit-logged.  Use sparingly — prefer adding a corrective "
        "episode so the temporal trail is preserved."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "episode_id": {"type": "string"},
            "reason": {
                "type": "string",
                "description": "Audit reason (e.g. 'PII leak', 'duplicate', 'user requested').",
            },
        },
        "required": ["episode_id", "reason"],
    },
}


ALL_SCHEMAS: List[Dict[str, Any]] = [
    ADD_EPISODE_SCHEMA,
    SEARCH_FACTS_SCHEMA,
    SEARCH_NODES_SCHEMA,
    GET_NODE_TIMELINE_SCHEMA,
    DELETE_EPISODE_SCHEMA,
]

# Tool name → RBAC sensitivity.  Consumed by agent/member_rbac.py
# allowlist (Phase 2.5).  add/search are routine; delete is sensitive.
SENSITIVITY: Dict[str, str] = {
    "graphiti_add_episode": "routine",
    "graphiti_search_facts": "routine",
    "graphiti_search_nodes": "routine",
    "graphiti_get_node_timeline": "routine",
    "graphiti_delete_episode": "sensitive",
}


def tool_names() -> List[str]:
    return [s["name"] for s in ALL_SCHEMAS]
