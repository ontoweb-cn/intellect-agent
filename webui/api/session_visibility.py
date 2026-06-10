"""Member/team session list visibility for WebUI (agent session_visibility parity)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


class SessionAccessDenied(Exception):
    """Session exists but the current actor may not read it (multi-user scope)."""


def agent_session_visibility_available() -> bool:
    try:
        from agent.session_visibility import SessionListScope  # noqa: F401

        return True
    except ImportError:
        return False


def get_request_session_scope():
    """Return thread-local SessionListScope for the active HTTP request, if any."""
    try:
        from api.members import get_request_session_scope as _get

        return _get()
    except ImportError:
        return None


def capture_worker_session_scope(
    *,
    member_id: str | None = None,
    team_id: str | None = None,
):
    """Build a SessionListScope snapshot for background worker threads."""
    try:
        from agent.session_visibility import (
            SessionListScope,
            resolve_session_list_scope,
        )
        from api.members import _store, load_members_config
    except ImportError:
        return None

    config = load_members_config()
    mid = (member_id or os.environ.get("INTELLECT_MEMBER_ID") or "").strip() or None
    tid = (team_id or os.environ.get("INTELLECT_TEAM") or "").strip() or None
    if not mid:
        try:
            from agent.membership import is_members_enabled

            if is_members_enabled(config):
                return SessionListScope(deny_all=True)
        except ImportError:
            pass
        return SessionListScope(unrestricted=True)

    store = _store()
    try:
        return resolve_session_list_scope(
            config=config,
            store=store,
            actor_member_id=mid,
            active_team_id=tid,
        )
    finally:
        store.close()


def resolve_effective_session_scope(scope=None):
    """Request TLS scope, else env-based scope for worker threads."""
    if scope is not None:
        return scope
    scope = get_request_session_scope()
    if scope is not None:
        return scope
    return capture_worker_session_scope()


def cli_sessions_scope_cache_token() -> str:
    """Cache-key suffix so CLI session lists are not shared across members."""
    scope = resolve_effective_session_scope()
    if scope is None:
        return "__no_scope__"
    if scope.unrestricted:
        return "__unrestricted__"
    if scope.deny_all:
        return "__deny_all__"
    mid = str(scope.actor_member_id or "")
    tid = str(scope.active_team_id or "")
    config, store, close_store = _visibility_config_and_store()
    try:
        from agent.session_visibility import actor_sees_all_member_sessions

        sees_all = bool(
            mid and actor_sees_all_member_sessions(store, mid, config)
        )
    except ImportError:
        sees_all = False
    finally:
        if close_store and store is not None:
            store.close()
    return f"{mid}:{tid}:{'all' if sees_all else 'own'}"


def resolve_actor_member_id_for_state_db_reads(
    explicit: str | None = None,
) -> str | None:
    """Member id for state.db reads when the caller did not pass one explicitly.

    Returns ``None`` when members are disabled or scope is unrestricted.
    Returns ``""`` when scope is deny-all (caller should return empty).
    """
    if explicit:
        return str(explicit).strip() or None
    scope = resolve_effective_session_scope()
    if scope is None or scope.unrestricted:
        return None
    if scope.deny_all:
        return ""
    return str(scope.actor_member_id or "").strip() or None


def resolve_scope_for_handler(handler, parsed):
    """Build SessionListScope from the current WebUI request."""
    try:
        from agent.session_visibility import SessionListScope, resolve_session_list_scope
    except ImportError:
        return None

    from api.members import (
        _store,
        agent_membership_available,
        load_members_config,
        resolve_member_id,
        resolve_team_id,
    )

    if not agent_membership_available():
        return SessionListScope(unrestricted=True)

    from agent.membership import is_members_enabled, is_teams_enabled
    from agent.members_team import TeamRequiredError, resolve_member_team_id

    config = load_members_config()
    if not is_members_enabled(config):
        return SessionListScope(unrestricted=True)

    mid = resolve_member_id(handler, parsed)
    if not mid:
        return SessionListScope(deny_all=True)

    tid = resolve_team_id(handler, parsed, member_id=mid)
    if tid is None and is_teams_enabled(config):
        store = _store()
        try:
            try:
                tid = resolve_member_team_id(mid, config, store=store, for_dashboard=True)
            except TeamRequiredError:
                tid = None
        finally:
            store.close()

    store = _store()
    try:
        return resolve_session_list_scope(
            config=config,
            store=store,
            actor_member_id=mid,
            active_team_id=tid,
        )
    finally:
        store.close()


def _session_row_from_obj(session_or_row: Any) -> Mapping[str, Any]:
    if isinstance(session_or_row, dict):
        return session_or_row
    return {
        "member_id": getattr(session_or_row, "member_id", None),
        "team_id": getattr(session_or_row, "team_id", None),
    }


def members_enabled_without_scope() -> bool:
    """True when members are enabled but no SessionListScope is bound (deny-by-default)."""
    try:
        from agent.membership import is_members_enabled
        from api.members import load_members_config

        return is_members_enabled(load_members_config())
    except ImportError:
        return False


def _session_row_visible_with_scope(session_or_row: Any, scope) -> bool:
    """Apply agent session_row_visible for a resolved non-None scope."""
    from agent.session_visibility import session_row_visible

    config, store, close_store = _visibility_config_and_store()
    try:
        return session_row_visible(
            _session_row_from_obj(session_or_row),
            scope,
            config=config,
            store=store,
        )
    finally:
        if close_store and store is not None:
            store.close()


def session_row_visible_for_request(session_or_row: Any, scope=None) -> bool:
    scope = resolve_effective_session_scope(scope)
    if scope is None:
        return not members_enabled_without_scope()
    return _session_row_visible_with_scope(session_or_row, scope)


def filter_session_rows(rows: list[dict], scope=None) -> list[dict]:
    scope = resolve_effective_session_scope(scope)
    if scope is None:
        return [] if members_enabled_without_scope() else rows
    return [
        row
        for row in rows
        if _session_row_visible_with_scope(row, scope)
    ]


def _visibility_config_and_store():
    """Load members config + store for session_row_visible role checks."""
    try:
        from api.members import _store, load_members_config

        return load_members_config(), _store(), False
    except Exception:
        return {}, None, False


def check_session_access(session_or_row: Any, scope=None) -> bool:
    """Whether *session_or_row* is visible under *scope* (or the active request scope)."""
    scope = resolve_effective_session_scope(scope)
    if scope is None:
        return not members_enabled_without_scope()
    return _session_row_visible_with_scope(session_or_row, scope)


def get_session_for_mutation(sid: str, *, metadata_only: bool = False):
    """Load a session for a mutating route; missing or denied → KeyError(sid).

    Wraps :func:`api.models.get_session` so route handlers can ``except KeyError``
    and return HTTP 404. Denied access raises :class:`SessionAccessDenied` (also
    mapped to 404 in ``server.py``).
    """
    from api.models import get_session

    return get_session(sid, metadata_only=metadata_only)


def enforce_session_access(session_or_row: Any, sid: Optional[str] = None) -> None:
    """Raise :class:`SessionAccessDenied` when the session is outside request scope."""
    session_id = sid or getattr(session_or_row, "session_id", None) or (
        session_or_row.get("session_id") if isinstance(session_or_row, dict) else None
    )
    if not check_session_access(session_or_row):
        scope = resolve_effective_session_scope()
        actor = getattr(scope, "actor_member_id", None) if scope is not None else None
        owner = getattr(session_or_row, "member_id", None) or (
            session_or_row.get("member_id") if isinstance(session_or_row, dict) else None
        )
        logger.warning(
            "session access denied: session_id=%s actor_member_id=%s session_member_id=%s",
            session_id,
            actor,
            owner,
        )
        raise SessionAccessDenied(session_id or "session")


def stamp_session_member_context(session) -> None:
    """Tag a session with the bound member/team runtime context (TLS)."""
    try:
        from api.members import (
            agent_membership_available,
            get_bound_runtime_context,
            load_members_config,
        )
    except ImportError:
        return
    if not agent_membership_available():
        return
    from agent.membership import is_members_enabled

    if not is_members_enabled(load_members_config()):
        return
    ctx = get_bound_runtime_context()
    if not ctx or not ctx.member_id:
        return
    session.member_id = str(ctx.member_id)
    session.team_id = str(ctx.team_id) if ctx.team_id else None


def _load_parent_session_metadata(parent_session_id: str | None):
    """Best-effort metadata load for parent-based member_id inheritance."""
    sid = (parent_session_id or "").strip()
    if not sid:
        return None
    try:
        from api.models import get_session

        return get_session(sid, metadata_only=True)
    except Exception:
        logger.debug(
            "parent session metadata unavailable for stamp: %s",
            sid,
            exc_info=True,
        )
        return None


def ensure_session_owner(
    session,
    *,
    parent_session=None,
    parent_session_id: str | None = None,
) -> None:
    """Ensure *session* has member_id/team_id before persisting (multi-user).

    Order: request TLS context → *parent_session* / *parent_session_id*
    inheritance → ``INTELLECT_MEMBER_ID`` / ``INTELLECT_TEAM`` env (workers).

    ``Session.save()`` calls this automatically; routes only need an explicit
    call when persisting without ``save()`` (e.g. ``/api/session/new``).
    """
    stamp_session_member_context(session)
    if getattr(session, "member_id", None):
        return
    try:
        from api.members import get_bound_runtime_context

        ctx = get_bound_runtime_context()
        if ctx and ctx.member_id:
            session.member_id = str(ctx.member_id)
            if ctx.team_id and not getattr(session, "team_id", None):
                session.team_id = str(ctx.team_id)
            return
    except ImportError:
        logger.debug("ensure_session_owner: members context unavailable", exc_info=True)
    try:
        from api.members import _tls

        mid = getattr(_tls, "member_id", None)
        if mid:
            session.member_id = str(mid)
            tid = getattr(_tls, "team_id", None)
            if tid and not getattr(session, "team_id", None):
                session.team_id = str(tid)
            return
    except ImportError:
        pass
    if getattr(session, "member_id", None):
        return
    if parent_session is None and parent_session_id:
        parent_session = _load_parent_session_metadata(parent_session_id)
    elif parent_session is None:
        inherited_sid = getattr(session, "parent_session_id", None)
        if inherited_sid:
            parent_session = _load_parent_session_metadata(inherited_sid)
    if parent_session is not None:
        parent_mid = getattr(parent_session, "member_id", None)
        if parent_mid:
            session.member_id = str(parent_mid)
            parent_tid = getattr(parent_session, "team_id", None)
            session.team_id = str(parent_tid) if parent_tid else None
            return
    env_mid = (os.environ.get("INTELLECT_MEMBER_ID") or "").strip()
    if env_mid:
        session.member_id = env_mid
        env_tid = (os.environ.get("INTELLECT_TEAM") or "").strip()
        session.team_id = env_tid or None


def members_require_member_id_on_save() -> bool:
    """True when WebUI must refuse Session.save() without member_id."""
    try:
        from agent.membership import is_members_enabled
        from api.members import agent_membership_available, load_members_config
    except ImportError:
        return False
    if not agent_membership_available():
        return False
    config = load_members_config()
    if not is_members_enabled(config):
        return False
    members = config.get("members") if isinstance(config.get("members"), dict) else {}
    si = members.get("session_isolation") if isinstance(members.get("session_isolation"), dict) else {}
    return bool(si.get("require_member_id_on_save", True))


def _session_row_needs_disk_hydrate(row: dict, session_path: Path) -> bool:
    """True when index row may be stale relative to on-disk session JSON."""
    if not str(row.get("member_id") or "").strip():
        return True
    if not session_path.is_file():
        return False
    try:
        index_ts = float(
            row.get("updated_at")
            or row.get("last_message_at")
            or row.get("created_at")
            or 0
        )
        if index_ts <= 0:
            return True
        return session_path.stat().st_mtime > index_ts + 1.0
    except (OSError, TypeError, ValueError):
        return True


# Cap disk reads per sidebar list request so first login with many legacy
# sessions (missing index member_id) does not block GET /api/sessions.
_LIST_HYDRATE_DISK_READ_CAP = 48


def hydrate_session_rows_from_disk(
    rows: list[dict],
    *,
    max_disk_reads: int | None = _LIST_HYDRATE_DISK_READ_CAP,
) -> list[dict]:
    """Refresh member_id/team_id on list rows from on-disk session JSON when needed.

    The sidebar index (_index.json) can lag behind the canonical session file
    (e.g. member_id re-stamped after a handoff). Listing used stale index
    member_id while GET /api/session reads the file → false positives in the
    sidebar and 404 on click. Disk JSON is the source of truth for ownership.

    Rows with a current index member_id and file mtime not newer than the index
    timestamp are left as-is to avoid O(n) disk reads on every sidebar poll.
    """
    try:
        from api.models import SESSION_DIR, Session, _write_session_index
    except ImportError:
        return rows

    hydrated: list[dict] = []
    index_repairs: list = []
    disk_reads = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = row.get("session_id")
        if not sid:
            hydrated.append(row)
            continue
        session_path = SESSION_DIR / f"{sid}.json"
        if not _session_row_needs_disk_hydrate(row, session_path):
            hydrated.append(row)
            continue
        if (
            max_disk_reads is not None
            and max_disk_reads >= 0
            and disk_reads >= max_disk_reads
        ):
            hydrated.append(row)
            continue
        disk_reads += 1
        meta = Session.load_metadata_only(sid)
        if meta is None:
            hydrated.append(row)
            continue
        new_row = dict(row)
        file_mid = getattr(meta, "member_id", None)
        file_tid = getattr(meta, "team_id", None)
        if file_mid is not None:
            new_row["member_id"] = file_mid
        if file_tid is not None:
            new_row["team_id"] = file_tid
        if (
            str(row.get("member_id") or "") != str(file_mid or "")
            or str(row.get("team_id") or "") != str(file_tid or "")
        ):
            index_repairs.append(meta)
        hydrated.append(new_row)
    if index_repairs:
        try:
            _write_session_index(updates=index_repairs)
        except Exception:
            logger.debug(
                "Failed to repair session index member_id drift for %s",
                [getattr(s, "session_id", None) for s in index_repairs],
                exc_info=True,
            )
    return hydrated


def apply_member_scope_to_session_rows(rows: list[dict], scope=None) -> list[dict]:
    """Filter session list rows for the active request or worker scope."""
    return filter_session_rows(hydrate_session_rows_from_disk(rows), scope)
