"""Single-user session visibility — member isolation removed."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SessionAccessDenied(Exception):
    """Session exists but the current actor may not read it (unused in single-user mode)."""

    def __init__(self, session_id: str = "session"):
        self.session_id = session_id
        super().__init__(session_id)


def agent_session_visibility_available() -> bool:
    return True


def get_request_session_scope():
    return None


def capture_worker_session_scope(**kwargs):
    return None


def resolve_effective_session_scope(scope=None):
    return scope


def cli_sessions_scope_cache_token() -> str:
    return "__unrestricted__"


def resolve_actor_member_id_for_state_db_reads(
    explicit: str | None = None,
) -> str | None:
    return None


def resolve_scope_for_handler(handler, parsed):
    return None


def session_row_visible_for_request(session_or_row: Any, scope=None) -> bool:
    return True


def filter_session_rows(rows: list[dict], scope=None) -> list[dict]:
    return list(rows) if rows else []


def check_session_access(session_or_row: Any, scope=None) -> bool:
    return True


def get_session_for_mutation(sid: str, *, metadata_only: bool = False):
    from api.models import get_session

    return get_session(sid, metadata_only=metadata_only)


def enforce_session_access(session_or_row: Any, sid: Optional[str] = None) -> None:
    return None


def stamp_session_member_context(session) -> None:
    return None


def ensure_session_owner(
    session,
    *,
    parent_session=None,
    parent_session_id: str | None = None,
) -> None:
    return None


def members_require_member_id_on_save() -> bool:
    return False


def members_enabled_without_scope() -> bool:
    return False


def hydrate_session_rows_from_disk(
    rows: list[dict],
    *,
    max_disk_reads: int | None = None,
) -> list[dict]:
    return list(rows) if rows else []


def apply_member_scope_to_session_rows(rows: list[dict], scope=None) -> list[dict]:
    return list(rows) if rows else []
