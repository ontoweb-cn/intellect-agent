"""WebUI members/teams API — single-user thin surface.

Multi-user CRUD/OAuth-login routes were removed with the membership stubs.
``GET /api/members/status`` remains so the frontend can detect ``enabled: false``.
Per-request hooks stay as no-ops so ``server.py`` / ``streaming.py`` keep working.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

from api.helpers import bad
from api.helpers import j as json_response

logger = logging.getLogger(__name__)

_tls = threading.local()

try:
    from agent.membership import MembershipStore
except Exception:  # pragma: no cover - agent package optional in some installs
    MembershipStore = None  # type: ignore[misc, assignment]


def _load_config() -> dict[str, Any]:
    from intellect_cli.config import load_config

    return load_config()


def agent_membership_available() -> bool:
    try:
        from api.config import _INTELLECT_FOUND

        if not _INTELLECT_FOUND:
            return False
        from agent.membership import MembershipStore as _MS  # noqa: F401

        return True
    except Exception:
        return False


def load_members_config() -> dict[str, Any]:
    """Public config loader for auth/login integration."""
    return _load_config()


def _store():
    from agent.membership import MembershipStore as _MS

    return _MS(config=_load_config())


def local_registration_requires_approval(config: Optional[dict[str, Any]] = None) -> bool:
    from api.config import load_settings
    from agent.membership import get_registration_config

    settings = load_settings()
    if "members_local_requires_approval" in settings:
        return bool(settings.get("members_local_requires_approval"))
    cfg = config if config is not None else _load_config()
    return bool(get_registration_config(cfg).get("local_requires_approval", True))


def _is_session_deeplink_path(path: str) -> bool:
    """True for GET /session/<session_id> app-shell routes (not static/manifest)."""
    if not path or not path.startswith("/session/"):
        return False
    tail = path[len("/session/") :].split("?", 1)[0].strip("/")
    if not tail:
        return False
    if tail in ("login", "manifest.json", "manifest.webmanifest"):
        return False
    if tail.startswith("static/"):
        return False
    return "/" not in tail


def _member_password_change_blocks_request(handler, parsed, actor: str) -> bool:
    return False


def _member_authorize(store, actor, action, *a, **kw) -> bool:
    return False


def _resolve_actor_display_name(actor: str) -> str:
    return str(actor or "")


def member_session_cookie_lines(member_id: str) -> list[str]:
    return []


def _resolve_or_create_member(store, display_name: str) -> str:
    raise ValueError("Members feature is disabled")


def resolve_member_id(handler, parsed) -> Optional[str]:
    return None


def resolve_team_id(handler, parsed, *, member_id: Optional[str] = None) -> Optional[str]:
    return None


def resolve_project_id(handler, parsed, *, member_id: Optional[str] = None) -> Optional[str]:
    return None


def bind_request_member_context(handler, parsed) -> None:
    """Reset thread-local member fields (single-user: no resolution)."""
    _tls.member_id = None
    _tls.team_id = None
    _tls.project_id = None
    _tls.runtime_context = None
    _tls.session_scope = None


def clear_request_member_context() -> None:
    _tls.member_id = None
    _tls.team_id = None
    _tls.project_id = None
    _tls.runtime_context = None
    _tls.session_scope = None


def get_bound_runtime_context():
    return getattr(_tls, "runtime_context", None)


def get_request_session_scope():
    return getattr(_tls, "session_scope", None)


def bind_worker_session_scope(scope) -> None:
    _tls.session_scope = scope


def build_runtime_context(**kwargs):
    return None


def apply_runtime_context_to_agent(agent, ctx=None) -> None:
    if ctx is None:
        ctx = get_bound_runtime_context()
    if ctx and getattr(ctx, "member_id", None):
        agent.runtime_context = ctx


def push_member_runtime_env(ctx=None) -> dict[str, Optional[str]]:
    old: dict[str, Optional[str]] = {
        "INTELLECT_MEMBER_ID": os.environ.get("INTELLECT_MEMBER_ID"),
        "INTELLECT_TEAM": os.environ.get("INTELLECT_TEAM"),
        "INTELLECT_PROJECT": os.environ.get("INTELLECT_PROJECT"),
    }
    try:
        from agent.runtime_context import snapshot_wiki_runtime_env

        old.update(snapshot_wiki_runtime_env())
    except Exception:
        pass
    return old


def pop_member_runtime_env(snapshot: dict[str, Optional[str]]) -> None:
    for key in ("INTELLECT_MEMBER_ID", "INTELLECT_TEAM", "INTELLECT_PROJECT"):
        val = snapshot.get(key)
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val
    try:
        from agent.runtime_context import restore_wiki_runtime_env

        restore_wiki_runtime_env(snapshot)
    except Exception:
        pass


def check_member_access(handler, parsed) -> bool:
    """Always allow in single-user mode."""
    return True


def maybe_redirect_oauth_canonical_host(handler, parsed) -> bool:
    return False


def get_status(handler, parsed) -> dict[str, Any]:
    from agent.membership import (
        is_members_enabled,
        is_projects_enabled,
        is_teams_enabled,
        members_mode,
    )
    from api.auth import is_auth_enabled

    config = _load_config()
    return {
        "enabled": is_members_enabled(config),
        "teams_enabled": is_teams_enabled(config),
        "projects_enabled": is_projects_enabled(config),
        "mode": members_mode(config),
        "actor_member_id": None,
        "actor_display_name": None,
        "actor_has_avatar": False,
        "actor_avatar_url": None,
        "active_team_id": None,
        "active_project_id": None,
        "agent_available": agent_membership_available(),
        "webui_auth_enabled": is_auth_enabled(),
        "bootstrap_complete": False,
    }


def _members_api_prefix(path: str) -> bool:
    return (
        path.startswith("/api/members")
        or path.startswith("/api/teams")
        or path.startswith("/api/member-projects")
    )


def handle_get(handler, parsed) -> bool:
    if not _members_api_prefix(parsed.path):
        return False
    if not agent_membership_available():
        if parsed.path == "/api/members/status":
            from api.auth import is_auth_enabled

            json_response(
                handler,
                {
                    "enabled": False,
                    "teams_enabled": False,
                    "projects_enabled": False,
                    "mode": "legacy",
                    "actor_member_id": None,
                    "active_team_id": None,
                    "agent_available": False,
                    "bootstrap_complete": False,
                    "webui_auth_enabled": is_auth_enabled(),
                },
            )
            return True
        return bad(handler, "Members feature requires intellect-agent", status=503)

    if parsed.path == "/api/members/status":
        json_response(handler, get_status(handler, parsed))
        return True
    return bad(handler, "Members feature is disabled", status=404)


def handle_post(handler, parsed) -> bool:
    if not _members_api_prefix(parsed.path):
        return False
    if not agent_membership_available():
        return bad(handler, "Members feature requires intellect-agent", status=503)
    return bad(handler, "Members feature is disabled", status=404)


def handle_delete(handler, parsed) -> bool:
    if not parsed.path.startswith("/api/members"):
        return False
    if not agent_membership_available():
        return bad(handler, "Members feature requires intellect-agent", status=503)
    return bad(handler, "Members feature is disabled", status=404)
