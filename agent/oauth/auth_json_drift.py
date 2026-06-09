"""Doctor checks for auth.json vs state.db OAuth drift (PR-A9)."""

from __future__ import annotations

from typing import Any


def auth_json_has_oauth_material(auth: dict[str, Any] | None = None) -> bool:
    from agent.oauth.migrate_from_auth_json import summarize_auth_json_oauth

    summary = summarize_auth_json_oauth(auth)
    return bool(summary.get("pool_entry_count")) or bool(summary.get("singleton_providers"))


def check_auth_json_oauth_drift(issues: list[str]) -> None:
    """Append doctor issues when legacy auth.json OAuth data may be stale vs DB."""
    from intellect_cli.auth import _auth_file_path

    path = _auth_file_path()
    if not path.exists():
        return

    try:
        from intellect_cli.auth import _load_auth_store

        auth = _load_auth_store()
    except Exception:
        return

    if not auth_json_has_oauth_material(auth):
        return

    try:
        from agent.oauth.migrate_from_auth_json import migration_marker_exists
        from agent.oauth.runtime_settings import get_oauth_runtime_settings

        settings = get_oauth_runtime_settings()
        if settings.write_auth_json:
            issues.append(
                "oauth.write_auth_json is enabled — model OAuth still mirrors to auth.json; "
                "set oauth.write_auth_json: false after migrate-from-auth-json"
            )
        if not migration_marker_exists():
            issues.append(
                "auth.json still holds model OAuth credentials — run: "
                "intellect oauth migrate-from-auth-json"
            )
    except Exception:
        pass
