"""Centralized feature flag system (P4-6).

Feature flags are gated via ``config.yaml`` under the ``features`` key.
Each flag has a default value (safe/false) and can be overridden per-profile
or via environment variables.

Usage::

    from agent.feature_flags import is_enabled

    if is_enabled("structured_logging", config):
        ...

Flags are discoverable::

    from agent.feature_flags import list_flags
    for flag in list_flags():
        print(flag["name"], flag["default"], flag["description"])
"""

from __future__ import annotations

import os
from typing import Any

# ── Flag registry ────────────────────────────────────────────────────────
# Add new flags here with a safe default (usually False for experimental
# features).  The registry is the single source of truth — all call sites
# use is_enabled() instead of ad-hoc config.get() calls.

_FLAGS: list[dict[str, Any]] = [
    {
        "name": "structured_logging",
        "default": False,
        "env_var": "INTELLECT_LOG_FORMAT",       # set to "json" to enable
        "description": "Emit log records as JSON Lines (see P4-4 JsonLogFormatter)",
    },
    {
        "name": "experimental_inference_registry",
        "default": False,
        "env_var": None,
        "description": "Use the DB-backed inference registry (P1-7 Phase 1) for provider resolution",
    },
    {
        "name": "skill_analytics_collect_on_startup",
        "default": False,
        "env_var": None,
        "description": "Collect skill analytics on agent startup (I/O overhead for large repos)",
    },
    {
        "name": "credential_encryption_enforce",
        "default": True,
        "env_var": None,
        "description": "Require encrypted credential storage; refuse to read plaintext files",
    },
    {
        "name": "bluebubbles_header_auth",
        "default": True,
        "env_var": None,
        "description": "Send BlueBubbles password via X-BlueBubbles-Password header",
    },
    {
        "name": "url_query_log_redaction",
        "default": True,
        "env_var": None,
        "description": "Redact sensitive URL query parameters in log output",
    },
    {
        "name": "dns_rebinding_protection",
        "default": True,
        "env_var": None,
        "description": "Validate TCP peer IP at connect time for HTTP fetches",
    },
]

_FLAG_MAP: dict[str, dict[str, Any]] = {f["name"]: f for f in _FLAGS}


def is_enabled(name: str, config: dict | None = None) -> bool:
    """Return True if feature *name* is enabled.

    Resolution order:
    1. Environment variable (if the flag declares ``env_var``)
    2. ``config.yaml`` → ``features.<name>``
    3. Registry default
    """
    flag = _FLAG_MAP.get(name)
    if flag is None:
        return False  # unknown flag → safe default

    # 1. Environment variable
    env_var = flag.get("env_var")
    if env_var:
        val = os.environ.get(env_var)
        if val is not None:
            return val.lower() in ("1", "true", "yes", "on", "json")

    # 2. config.yaml
    if isinstance(config, dict):
        features = config.get("features")
        if isinstance(features, dict):
            cfg_val = features.get(name)
            if cfg_val is not None:
                return bool(cfg_val)

    # 3. Registry default
    return bool(flag.get("default", False))


def list_flags() -> list[dict[str, Any]]:
    """Return all registered feature flags with metadata."""
    return [dict(f) for f in _FLAGS]


def get_flag(name: str) -> dict[str, Any] | None:
    """Return flag metadata dict, or None if unknown."""
    return dict(_FLAG_MAP.get(name, {})) or None
