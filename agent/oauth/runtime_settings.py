"""OAuth runtime feature flags (auth.json deprecation A5–A6)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


@dataclass(frozen=True)
class OAuthRuntimeSettings:
    """Resolved OAuth migration flags."""

    read_auth_json_fallback: bool = False  # PR-A10: runtime OAuth does not read auth.json
    write_auth_json: bool = False  # PR-A6: default single-write to state.db
    credential_pool_backend: str = "db"  # db | auto | auth_json

    def db_read_first(self) -> bool:
        return self.credential_pool_backend in ("auto", "db")

    def pool_uses_db(self) -> bool:
        return self.credential_pool_backend in ("auto", "db")

    def auth_json_read_allowed(self) -> bool:
        if self.read_auth_json_fallback:
            return True
        if self.credential_pool_backend == "auth_json":
            return True
        if self.credential_pool_backend == "db":
            return False
        return self.read_auth_json_fallback


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


_RUNTIME_FLAG_KEYS = (
    "read_auth_json_fallback",
    "write_auth_json",
    "credential_pool_backend",
)


def _config_oauth_section() -> dict[str, Any]:
    try:
        from intellect_cli.config import load_config

        cfg = load_config()
        if not isinstance(cfg, dict):
            return {}

        section: dict[str, Any] = {}
        oauth = cfg.get("oauth")
        if isinstance(oauth, dict):
            section.update(oauth)
        members = cfg.get("members")
        if isinstance(members, dict):
            members_oauth = members.get("oauth")
            if isinstance(members_oauth, dict):
                for key in _RUNTIME_FLAG_KEYS:
                    if key in members_oauth:
                        section[key] = members_oauth[key]
        return section
    except Exception:
        pass
    return {}


@lru_cache(maxsize=1)
def get_oauth_runtime_settings() -> OAuthRuntimeSettings:
    """Load flags from config.yaml ``oauth:`` and env overrides."""
    section = _config_oauth_section()
    read_fallback = bool(section.get("read_auth_json_fallback", False))
    write_json = bool(section.get("write_auth_json", False))
    backend = str(section.get("credential_pool_backend") or "db").strip().lower()

    read_fallback = _env_bool("INTELLECT_OAUTH_READ_AUTH_JSON", read_fallback)
    write_json = _env_bool("INTELLECT_OAUTH_WRITE_AUTH_JSON", write_json)

    env_backend = os.environ.get("INTELLECT_OAUTH_CREDENTIAL_POOL_BACKEND", "").strip().lower()
    if env_backend in ("auto", "db", "auth_json"):
        backend = env_backend

    return OAuthRuntimeSettings(
        read_auth_json_fallback=bool(read_fallback),
        write_auth_json=bool(write_json),
        credential_pool_backend=backend if backend in ("auto", "db", "auth_json") else "auto",
    )


def should_write_auth_json() -> bool:
    return get_oauth_runtime_settings().write_auth_json


def should_read_auth_json() -> bool:
    return get_oauth_runtime_settings().auth_json_read_allowed()
