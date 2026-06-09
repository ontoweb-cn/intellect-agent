"""Temporary gate for profile create / switch / delete (CLI + WebUI).

Controlled by ``profiles.management_enabled`` in config.yaml. When false:

- CLI blocks mutating ``intellect profile`` subcommands (create, use, delete, …).
- WebUI hides Profiles UI and returns 403 on mutating profile APIs.
- ``intellect -p <existing>`` and read-only ``intellect profile list`` stay available.

Set ``profiles.management_enabled: true`` to restore full profile management.
"""

from __future__ import annotations

from typing import Any

# Subcommands blocked while management is disabled (mutations only).
CLI_MUTATING_PROFILE_ACTIONS = frozenset({
    "use",
    "create",
    "delete",
    "rename",
    "import",
    "install",
    "alias",
})


def is_profile_management_enabled(config: dict[str, Any] | None = None) -> bool:
    """Return True when users may create, switch, or delete profiles."""
    if config is None:
        from intellect_cli.config import load_config

        config = load_config()
    profiles = config.get("profiles") if isinstance(config.get("profiles"), dict) else {}
    # Default false: temporary product lock until multi-profile UX is re-enabled.
    return bool(profiles.get("management_enabled", False))


def profile_management_disabled_message() -> str:
    return (
        "Profile management is temporarily disabled "
        "(set profiles.management_enabled: true in config.yaml to re-enable). "
        "Existing profiles remain usable via intellect -p <name>."
    )
