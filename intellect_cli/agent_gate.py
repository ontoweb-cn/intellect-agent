"""Temporary gate for agent (formerly profile) create / switch / delete.

Controlled by ``agents.management_enabled`` in config.yaml (legacy key:
``profiles.management_enabled``). When false:

- CLI blocks mutating ``intellect agent`` / ``intellect profile`` subcommands.
- WebUI hides Agents UI and returns 403 on mutating APIs.
- ``intellect -a <existing>`` / ``-p`` and read-only list stay available.

Set ``agents.management_enabled: true`` to restore full management.
"""

from __future__ import annotations

from typing import Any

# Subcommands blocked while management is disabled (mutations only).
CLI_MUTATING_AGENT_ACTIONS = frozenset({
    "use",
    "create",
    "delete",
    "rename",
    "import",
    "install",
    "alias",
})

# Legacy alias during profile→agent rename.
CLI_MUTATING_PROFILE_ACTIONS = CLI_MUTATING_AGENT_ACTIONS


def is_agent_management_enabled(config: dict[str, Any] | None = None) -> bool:
    """Return True when users may create, switch, or delete agents.

    Reads ``agents.management_enabled`` (canonical) and legacy
    ``profiles.management_enabled``. Either True enables management so older
    configs that only set ``profiles.*`` keep working after DEFAULT_CONFIG
    gained an ``agents`` block (deep-merge would otherwise leave agents=false).
    """
    if config is None:
        from intellect_cli.config import load_config

        config = load_config()
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    profiles = config.get("profiles") if isinstance(config.get("profiles"), dict) else {}
    return bool(agents.get("management_enabled")) or bool(
        profiles.get("management_enabled")
    )


is_profile_management_enabled = is_agent_management_enabled


def agent_management_disabled_message() -> str:
    return (
        "Agent management is temporarily disabled "
        "(set agents.management_enabled: true in config.yaml to re-enable). "
        "Existing agents remain usable via intellect -a <name> "
        "(or legacy -p / intellect profile)."
    )


profile_management_disabled_message = agent_management_disabled_message
