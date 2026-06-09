"""LightRAG plugin configuration — $INTELLECT_HOME/lightrag/config.json."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

CONFIG_DIR_NAME = "lightrag"
CONFIG_FILE_NAME = "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "mode": "remote",
    "server": {
        "base_url": "http://127.0.0.1:9621",
        "api_key": "",
        "timeout_seconds": 120,
        "api_prefix": "",
    },
    "workspace": {
        "default": "global",
        "session_prefix": "session_",
        "member_prefix": "member_",
        "team_prefix": "team_",
        "project_prefix": "project_",
    },
    "query": {
        "default_mode": "mix",
        "prefetch_mode": "hybrid",
        "enable_rerank": False,
        "only_need_context": True,
        "max_context_tokens": 4000,
    },
    "ingest": {
        "auto_mode": "off",
        "summary_max_tokens": 256,
        "session_end_min_turns": 3,
        "pre_compress": True,
    },
    "circuit_breaker": {
        "threshold": 3,
        "cooldown_seconds": 30,
    },
    "upload": {
        "default_parse_engine": "",
        "multimodal_default_options": "",
        "analyze_images": False,
        "analyze_tables": False,
        "analyze_equations": False,
        "chunking": "",
        "skip_kg": False,
    },
}


def _config_path(intellect_home: str) -> Path:
    return Path(intellect_home) / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def load_config(intellect_home: str = "") -> Dict[str, Any]:
    if not intellect_home:
        intellect_home = os.environ.get(
            "INTELLECT_HOME", str(Path.home() / ".intellect")
        )
    cfg: Dict[str, Any] = json.loads(json.dumps(DEFAULT_CONFIG))
    path = _config_path(intellect_home)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                _deep_merge(cfg, stored)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load lightrag config from %s: %s", path, exc)

    server = cfg.setdefault("server", {})
    if os.environ.get("LIGHTRAG_BASE_URL"):
        server["base_url"] = os.environ["LIGHTRAG_BASE_URL"]
    api_key = os.environ.get("LIGHTRAG_API_KEY") or os.environ.get(
        "LIGHTRAG_SERVER_API_KEY"
    )
    if api_key is not None:
        server["api_key"] = api_key
    if os.environ.get("LIGHTRAG_TIMEOUT"):
        try:
            server["timeout_seconds"] = int(os.environ["LIGHTRAG_TIMEOUT"])
        except ValueError:
            pass
    return cfg


def save_config(values: Dict[str, Any], intellect_home: str) -> None:
    path = _config_path(intellect_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_config(intellect_home)
    _deep_merge(existing, values)
    existing.get("server", {}).pop("api_key", None)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, sort_keys=True)
        f.write("\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> None:
    for key, val in overlay.items():
        if (
            key in base
            and isinstance(base[key], dict)
            and isinstance(val, dict)
        ):
            _deep_merge(base[key], val)
        else:
            base[key] = val


def get_config_schema() -> List[Dict[str, Any]]:
    return [
        {
            "key": "server.base_url",
            "description": "LightRAG API server URL (e.g. http://127.0.0.1:9621)",
            "default": "http://127.0.0.1:9621",
            "required": True,
        },
        {
            "key": "ingest.auto_mode",
            "description": "Auto-ingest conversations: off, summary, or full",
            "choices": ["off", "summary", "full"],
            "default": "off",
        },
    ]
