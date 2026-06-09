"""Graphiti memory plugin configuration management.

Config file location: $INTELLECT_HOME/graphiti/config.json
Environment variables override config file values.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CONFIG_DIR_NAME = "graphiti"
CONFIG_FILE_NAME = "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    # Phase 5.4: backend selector.  Defaults to falkordb (the
    # recommended setup for v0.5.0); neo4j is an opt-in for shops that
    # already run Neo4j Enterprise (multi-database) or Community (single
    # database with group_id filtering).
    "backend": "falkordb",
    "falkordb_host": "localhost",
    "falkordb_port": 6380,
    "falkordb_password": "",
    # Neo4j connection (only consulted when backend == "neo4j").
    "neo4j_uri": "",                 # e.g. bolt://neo4j.example.com:7687
    "neo4j_user": "neo4j",
    "neo4j_multi_db": True,          # False for Community (single db only)
    # Phase 5c: embedder selection.  `local` = fastembed (no API key,
    # bge-m3 default — strong multilingual general-purpose model).
    # `openai` = graphiti-core's default; needs OPENAI_API_KEY.
    "embedding_provider": "local",
    "embedding_model": "bge-m3",
    # Phase 5c: LLM endpoint for entity extraction.  `openai` with
    # no base_url uses graphiti-core's default (needs OPENAI_API_KEY).
    # Set llm_base_url to point at an OpenAI-compatible endpoint
    # (Ollama / vLLM / LiteLLM / LM Studio) to run fully local.
    "llm_provider": "openai",
    "llm_base_url": "",              # e.g. http://localhost:11434/v1 for Ollama
    "llm_api_key": "",               # placeholder for local servers
    "llm_model": "",                 # default model used for extraction
    "llm_small_model": "",           # smaller / cheaper model for trivial calls
    "auto_ingest": True,
    "ingest_every_n_turns": 1,
    "default_max_nodes": 10,
    "mcp_enabled": False,
}


def _config_dir(intellect_home: str) -> Path:
    return Path(intellect_home) / CONFIG_DIR_NAME


def _config_path(intellect_home: str) -> Path:
    return _config_dir(intellect_home) / CONFIG_FILE_NAME


def load_config(intellect_home: str = "") -> Dict[str, Any]:
    """Load Graphiti config from file, overlaid with env vars."""
    if not intellect_home:
        intellect_home = os.environ.get(
            "INTELLECT_HOME", str(Path.home() / ".intellect")
        )

    cfg = dict(DEFAULT_CONFIG)
    path = _config_path(intellect_home)

    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                cfg.update(stored)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load graphiti config from %s: %s", path, exc)

    # Env var overrides
    env_map = {
        "GRAPHITI_BACKEND": "backend",
        "GRAPHITI_FALKORDB_HOST": "falkordb_host",
        "GRAPHITI_FALKORDB_PORT": "falkordb_port",
        "GRAPHITI_FALKORDB_PASSWORD": "falkordb_password",
        "GRAPHITI_NEO4J_URI": "neo4j_uri",
        "GRAPHITI_NEO4J_USER": "neo4j_user",
        "GRAPHITI_NEO4J_PASSWORD": "falkordb_password",   # single password slot
        "GRAPHITI_NEO4J_MULTI_DB": "neo4j_multi_db",
        "GRAPHITI_EMBEDDING_PROVIDER": "embedding_provider",
        "GRAPHITI_EMBEDDING_MODEL": "embedding_model",
        "GRAPHITI_LLM_PROVIDER": "llm_provider",
        "GRAPHITI_LLM_BASE_URL": "llm_base_url",
        "GRAPHITI_LLM_API_KEY": "llm_api_key",
        "GRAPHITI_LLM_MODEL": "llm_model",
        "GRAPHITI_LLM_SMALL_MODEL": "llm_small_model",
    }
    for env_var, cfg_key in env_map.items():
        val = os.environ.get(env_var)
        if val is not None:
            if cfg_key == "falkordb_port":
                try:
                    cfg[cfg_key] = int(val)
                except ValueError:
                    pass
            elif cfg_key in ("auto_ingest", "neo4j_multi_db"):
                cfg[cfg_key] = val.lower() in ("1", "true", "yes")
            else:
                cfg[cfg_key] = val

    return cfg


def save_config(values: Dict[str, Any], intellect_home: str) -> None:
    """Write non-secret config to $INTELLECT_HOME/graphiti/config.json."""
    path = _config_path(intellect_home)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing to merge
    existing = load_config(intellect_home)
    existing.update(values)

    # Remove secrets (they go to .env).  Derive the secret-key list
    # from the schema so it never falls out of sync.
    _secret_keys = {
        f["key"] for f in get_config_schema() if f.get("secret") is True
    }
    for k in _secret_keys:
        existing.pop(k, None)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, sort_keys=True)
        f.write("\n")


def get_config_schema() -> List[Dict[str, Any]]:
    """Return config fields for 'intellect memory setup' wizard."""
    return [
        {
            "key": "backend",
            "description": (
                "Graph backend.  falkordb = recommended (Redis with graph "
                "module; multi-tenant via per-graph databases).  neo4j = "
                "use an existing Neo4j Enterprise (multi-database) or "
                "Community (single database + group_id filtering) deploy."
            ),
            "choices": ["falkordb", "neo4j"],
            "default": "falkordb",
        },
        {
            "key": "falkordb_host",
            "description": "FalkorDB server host (e.g. localhost, falkordb.example.com)",
            "default": "localhost",
            "required": True,
            "when": {"backend": "falkordb"},
        },
        {
            "key": "falkordb_port",
            "description": "FalkorDB server port",
            "default": "6380",
            "required": True,
            "when": {"backend": "falkordb"},
        },
        {
            "key": "falkordb_password",
            "description": "FalkorDB password (leave empty if no auth; also used as Neo4j password)",
            "secret": True,
            "env_var": "GRAPHITI_FALKORDB_PASSWORD",
            "required": False,
        },
        {
            "key": "neo4j_uri",
            "description": "Neo4j bolt URI (e.g. bolt://neo4j.example.com:7687)",
            "default": "",
            "when": {"backend": "neo4j"},
        },
        {
            "key": "neo4j_user",
            "description": "Neo4j username",
            "default": "neo4j",
            "when": {"backend": "neo4j"},
        },
        {
            "key": "neo4j_multi_db",
            "description": (
                "Enterprise (multi-database) → true.  Community → false "
                "(falls back to a single database + group_id filtering for "
                "tenant isolation)."
            ),
            "choices": ["true", "false"],
            "default": "true",
            "when": {"backend": "neo4j"},
        },
        {
            "key": "embedding_provider",
            "description": "Embedding provider for vector search",
            "choices": ["local", "openai"],
            "default": "local",
        },
        {
            "key": "embedding_model",
            "description": "Embedding model name (e.g. bge-m3, text-embedding-3-small)",
            "default": "bge-m3",
            "when": {"embedding_provider": "local"},
        },
        {
            "key": "llm_provider",
            "description": (
                "LLM provider for entity extraction.  openai = graphiti-core "
                "default (requires OPENAI_API_KEY).  Use openai_compat / ollama / "
                "vllm / litellm with a base_url for local models."
            ),
            "choices": ["openai", "openai_compat", "ollama", "vllm", "litellm"],
            "default": "openai",
        },
        {
            "key": "llm_base_url",
            "description": (
                "Base URL for OpenAI-compatible LLM endpoint "
                "(e.g. http://localhost:11434/v1 for Ollama, "
                "http://localhost:8000/v1 for vLLM).  Leave empty to use "
                "graphiti-core's default (needs OPENAI_API_KEY)."
            ),
            "default": "",
        },
        {
            "key": "llm_api_key",
            "description": (
                "API key for the LLM endpoint.  Most local servers ignore the "
                "value but the SDK refuses to construct without one."
            ),
            "secret": True,
            "default": "",
        },
        {
            "key": "llm_model",
            "description": "LLM model name for entity extraction (e.g. llama3-8b, gpt-4o-mini)",
            "default": "",
        },
        {
            "key": "llm_small_model",
            "description": (
                "Smaller / cheaper LLM model for trivial extraction calls "
                "(e.g. phi3-mini, gpt-4.1-nano).  Leave empty to use the "
                "primary model for everything."
            ),
            "default": "",
        },
        {
            "key": "auto_ingest",
            "description": "Automatically ingest conversations into knowledge graph",
            "choices": ["true", "false"],
            "default": "true",
        },
    ]
