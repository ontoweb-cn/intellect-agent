"""Shared helpers for reading Intellect Agent sessions from the session store."""
from __future__ import annotations

import logging
import sqlite3
import warnings
from contextlib import closing
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _warn_deprecated_db_path_api(api_name: str) -> None:
    warnings.warn(
        f"{api_name}(db_path) is deprecated; use the *_for_profile() helper "
        "via api.storage_bridge",
        DeprecationWarning,
        stacklevel=3,
    )


class _LegacyPathDbHandle:
    """Closes a read-only legacy SQLite connection opened by :func:`_conn_from_db_path`."""

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _conn_from_db_path(db_path: Path) -> tuple[_LegacyPathDbHandle, sqlite3.Connection]:
    """Read-only legacy opener for hand-built SQLite files (tests).

    Prefer :func:`api.storage_bridge.get_session_db` in production — it respects
    ``storage.backend`` and avoids re-running schema init on minimal fixtures.
    """
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return _LegacyPathDbHandle(conn), conn


MESSAGING_SOURCES = {
    'discord',
    'email',
    'slack',
    'telegram',
    'weixin',
}

CLI_MIN_UNTITLED_MESSAGE_COUNT = 6
CLI_MIN_UNTITLED_USER_MESSAGE_COUNT = 2

SOURCE_LABELS = {
    'api_server': 'API',
    'cli': 'CLI',
    'cron': 'Cron',
    'discord': 'Discord',
    'email': 'Email',
    'slack': 'Slack',
    'telegram': 'Telegram',
    'tool': 'Tool',
    'webui': 'WebUI',
    'weixin': 'Weixin',
}


def normalize_agent_session_source(raw_source: str | None) -> dict:
    """Return stable source metadata for Intellect Agent session rows.

    ``sessions.source`` is an Agent-level raw value. WebUI needs a smaller,
    durable contract so routes, SSE snapshots, and future sidebar policies do
    not each reimplement raw-source checks.
    """
    raw = str(raw_source or '').strip().lower() or 'unknown'

    if raw == 'webui':
        session_source = 'webui'
    elif raw == 'cli':
        session_source = 'cli'
    elif raw in MESSAGING_SOURCES:
        session_source = 'messaging'
    elif raw == 'cron':
        session_source = 'cron'
    elif raw == 'tool':
        session_source = 'tool'
    elif raw == 'api_server':
        session_source = 'api'
    else:
        session_source = 'other'

    label = SOURCE_LABELS.get(raw)
    if not label:
        label = raw.replace('_', ' ').title() if raw != 'unknown' else 'Agent'

    return {
        'raw_source': None if raw == 'unknown' else raw,
        'session_source': session_source,
        'source_label': label,
    }


def _with_normalized_source(row: dict) -> dict:
    normalized = normalize_agent_session_source(row.get('source'))
    return {**row, **normalized}


def _optional_col(name: str, columns: set[str], fallback: str = "NULL") -> str:
    return f"s.{name}" if name in columns else f"{fallback} AS {name}"


def _safe_lower(value) -> str:
    return str(value or "").strip().lower()


def _normalize_source_name(value: object) -> str:
    source = _safe_lower(value)
    if not source:
        return ""
    if source.endswith(" session"):
        source = source[:-len(" session")].strip()
    return source


def _looks_like_default_cli_title(row: dict) -> bool:
    """Return True when a CLI row looks like framework-generated metadata."""
    title = _safe_lower(row.get("title"))
    if not title or title == "untitled":
        return True
    if title in {"cli", "cli session"}:
        return True

    source_candidates = {
        _normalize_source_name(row.get("source")),
        _normalize_source_name(row.get("session_source")),
        _normalize_source_name(row.get("source_tag")),
        _normalize_source_name(row.get("raw_source")),
        _normalize_source_name(row.get("source_label")),
    }
    source_candidates.discard("")
    source_candidates.add("cli")
    return any(title == f"{candidate} session" for candidate in source_candidates)


def _as_positive_int(value) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _count_user_turns(row: dict) -> int:
    user_turns = row.get("actual_user_message_count")
    if user_turns is None:
        user_turns = row.get("user_message_count")
    if user_turns is None:
        messages = row.get("messages") or []
        if isinstance(messages, list):
            return sum(
                1
                for msg in messages
                if _safe_lower(msg.get("role") if isinstance(msg, dict) else msg) == "user"
            )
        return 0
    return _as_positive_int(user_turns)


def _has_cli_lineage(row: dict) -> bool:
    segment_count = _as_positive_int(row.get("_compression_segment_count"))
    return segment_count > 1 or bool(row.get("_lineage_root_id"))


def is_cli_session_row(row: dict) -> bool:
    """Return True for rows that should be treated as CLI-imported sessions."""
    if not isinstance(row, dict):
        return False
    source = _safe_lower(row.get("session_source"))
    if source == "messaging":
        return False
    if source == "cli":
        return True
    source_tag = _safe_lower(row.get("source_tag"))
    raw_source = _safe_lower(row.get("raw_source"))
    source_name = _safe_lower(row.get("source"))
    source_label = _safe_lower(row.get("source_label"))
    if source_tag == "cli" or raw_source == "cli" or source_name == "cli" or source_label == "cli":
        return True

    # Legacy imported CLI rows may only be marked as CLI in sidebar metadata.
    # Keep this conservative to avoid treating messaging sessions as CLI.
    return bool(
        row.get("is_cli_session")
        and source not in MESSAGING_SOURCES
        and source_tag not in MESSAGING_SOURCES
        and raw_source not in MESSAGING_SOURCES
        and source_name not in MESSAGING_SOURCES
        and _looks_like_default_cli_title(row)
    )


def is_cli_session_row_visible(row: dict) -> bool:
    """Return whether a CLI-related row should remain visible in the sidebar."""
    if not isinstance(row, dict):
        return False
    if not is_cli_session_row(row):
        return True

    message_count = _as_positive_int(row.get("actual_message_count") or row.get("message_count"))
    if message_count <= 0:
        return False

    if _has_cli_lineage(row):
        return True

    if not _looks_like_default_cli_title(row):
        return True

    return _count_user_turns(row) >= CLI_MIN_UNTITLED_USER_MESSAGE_COUNT


def _is_continuation_session(parent: dict | None, child: dict | None) -> bool:
    """Return True when ``child`` is the next segment of the same conversation.

    Compression rotates session ids automatically. A manual CLI close followed
    by ``intellect -c`` also records a new child session; for sidebar projection it
    should continue the same visible conversation rather than becoming a
    separate child-session row. Plain parent/child links that started before the
    parent's ended boundary remain child sessions.

    Do not collapse lineage across raw sources. A WebUI session that continues
    from a Telegram/CLI/etc. parent must remain visible as its own surface-owned
    conversation; otherwise the tip inherits the root's title/source metadata and
    can disappear under messaging/sidebar policies.
    """
    if not parent or not child:
        return False
    if str(child.get('session_source') or '').strip().lower() == 'fork':
        return False
    parent_source = str(parent.get('source') or '').strip().lower()
    child_source = str(child.get('source') or '').strip().lower()
    if parent_source and child_source and parent_source != child_source:
        return False
    if parent.get('end_reason') not in {'compression', 'cli_close'}:
        return False
    ended_at = parent.get('ended_at')
    if ended_at is None:
        # Older state.db rows/tests may not have ended_at populated. Preserve
        # the historical contract that compression/cli_close parent links are
        # continuations when no boundary timestamp is available.
        return True
    try:
        return float(child.get('started_at') or 0) >= float(ended_at)
    except (TypeError, ValueError):
        return False


def _continuation_root_id(rows_by_id: dict[str, dict], session_id: str | None) -> str | None:
    """Return the visible lineage root for ``session_id`` by walking continuations."""
    if not session_id:
        return None
    root_id = str(session_id)
    current_id = root_id
    seen = {current_id}
    for _ in range(len(rows_by_id) + 1):
        current = rows_by_id.get(current_id)
        parent_id = current.get('parent_session_id') if current else None
        parent = rows_by_id.get(parent_id) if parent_id else None
        if not parent or not _is_continuation_session(parent, current):
            return root_id
        if parent_id in seen:
            return root_id
        root_id = str(parent_id)
        current_id = str(parent_id)
        seen.add(current_id)
    return root_id


def _project_agent_session_rows(rows: list[dict]) -> list[dict]:
    """Collapse compression chains into one logical sidebar row.

    The visible conversation should still look like the original chain head
    (title and timestamps), while importing should use the latest importable
    segment so the user continues from the current compressed state.
    """
    rows_by_id = {row['id']: row for row in rows}
    children_by_parent: dict[str, list[dict]] = {}
    continuation_child_ids = set()

    for row in rows:
        parent_id = row.get('parent_session_id')
        if not parent_id:
            continue
        children_by_parent.setdefault(parent_id, []).append(row)
        parent = rows_by_id.get(parent_id)
        if _is_continuation_session(parent, row):
            continuation_child_ids.add(row['id'])
        else:
            row['relationship_type'] = 'child_session'
            row['parent_title'] = parent.get('title') if parent else None
            row['parent_source'] = parent.get('source') if parent else None
            parent_root = _continuation_root_id(rows_by_id, parent_id)
            if parent_root:
                row['_parent_lineage_root_id'] = parent_root

    for children in children_by_parent.values():
        children.sort(key=lambda row: row.get('started_at') or 0, reverse=True)

    def compression_tip(row: dict) -> tuple[dict | None, int]:
        current = row
        seen = {row['id']}
        latest_importable = row if (row.get('actual_message_count') or 0) > 0 else None
        segment_count = 1
        for _ in range(len(rows_by_id) + 1):
            candidates = [
                child for child in children_by_parent.get(current['id'], [])
                if child['id'] not in seen and _is_continuation_session(current, child)
            ]
            if not candidates:
                return latest_importable, segment_count
            current = candidates[0]
            seen.add(current['id'])
            segment_count += 1
            if (current.get('actual_message_count') or 0) > 0:
                latest_importable = current
        return latest_importable, segment_count

    projected = []
    for row in rows:
        if row['id'] in continuation_child_ids:
            continue

        segment_count = 1
        tip = row
        if row.get('end_reason') in {'compression', 'cli_close'}:
            tip, segment_count = compression_tip(row)
        if not tip or (tip.get('actual_message_count') or 0) <= 0:
            continue

        if tip is row:
            projected.append(dict(row))
            continue

        merged = dict(row)
        # Keep the chain head's visible identity (title, started_at), but
        # point the row at the latest importable segment for navigation AND
        # surface the tip's recency so an actively-used chain bubbles to the
        # top of the sidebar by its true last activity. Without overriding
        # last_activity, a long-lived chain whose tip is being edited NOW
        # would sort by the root's old timestamp and fall below recently
        # touched standalone sessions — exactly the inverse of what a user
        # expects from "Show agent sessions" sorted by activity.
        for key in (
            'id', 'model', 'message_count', 'actual_message_count', 'actual_user_message_count',
            'ended_at', 'end_reason', 'last_activity',
        ):
            if key in tip:
                merged[key] = tip[key]
        if not merged.get('title'):
            merged['title'] = tip.get('title')
        if not merged.get('source'):
            merged['source'] = tip.get('source')
        merged['_lineage_root_id'] = row['id']
        merged['_lineage_tip_id'] = tip['id']
        merged['_compression_segment_count'] = segment_count
        projected.append(merged)

    projected.sort(
        key=lambda row: row.get('last_activity') or row.get('started_at') or 0,
        reverse=True,
    )
    return projected


def read_importable_agent_session_rows(
    db_path: Path,
    limit: int = 200,
    log=None,
    exclude_sources: tuple[str, ...] | None = ("cron", "webui"),
) -> list[dict]:
    """Return agent sessions projected as importable conversations.

    Intellect Agent can create rows in ``state.db.sessions`` before a session has
    any messages, and long conversations can be split into compression-linked
    rows. WebUI cannot import empty rows and should not show compression
    segments as separate conversations, so both the regular ``/api/sessions``
    path and the gateway SSE watcher use this shared projection.

    By default, omit background/internal sources such as ``cron`` from the WebUI
    sidebar. This mirrors Intellect Agent CLI's session-list behaviour: interactive
    views should stay focused on user-facing conversations, while callers that
    need a source-specific diagnostic view can opt out by passing
    ``exclude_sources=None``.
    """
    _warn_deprecated_db_path_api("read_importable_agent_session_rows")
    db_path = Path(db_path)
    if not db_path.exists():
        return []

    log = log or logger
    db, conn = _conn_from_db_path(db_path)
    try:
        cur = conn.cursor()

        # Older Intellect Agent versions may not have source tracking. Without a
        # source column we cannot safely distinguish WebUI rows from agent rows.
        cur.execute("PRAGMA table_info(sessions)")
        session_cols = {row[1] for row in cur.fetchall()}
        cur.execute("PRAGMA table_info(messages)")
        message_cols = {row[1] for row in cur.fetchall()}
        if 'source' not in session_cols:
            log.warning(
                "agent session listing skipped: state.db at %s has no 'source' column "
                "(older intellect-agent?). Agent sessions unavailable. "
                "Upgrade intellect-agent to fix this.",
                db_path,
            )
            return []

        parent_expr = _optional_col('parent_session_id', session_cols)
        session_source_expr = _optional_col('session_source', session_cols)
        ended_expr = _optional_col('ended_at', session_cols)
        end_reason_expr = _optional_col('end_reason', session_cols)
        user_id_expr = _optional_col('user_id', session_cols)
        chat_id_expr = _optional_col('chat_id', session_cols)
        chat_type_expr = _optional_col('chat_type', session_cols)
        thread_id_expr = _optional_col('thread_id', session_cols)
        session_key_expr = _optional_col('session_key', session_cols)
        origin_chat_id_expr = _optional_col('origin_chat_id', session_cols)
        origin_user_id_expr = _optional_col('origin_user_id', session_cols)
        platform_expr = _optional_col('platform', session_cols)
        member_id_expr = _optional_col('member_id', session_cols)
        user_message_count_expr = (
            "COUNT(CASE WHEN LOWER(m.role) = 'user' THEN 1 END)"
            if 'role' in message_cols
            else "COUNT(m.id)"
        )

        where_clauses = ["s.source IS NOT NULL"]
        params: list[object] = []
        if exclude_sources:
            excluded = tuple(str(source) for source in exclude_sources if source)
            if excluded:
                placeholders = ", ".join("?" for _ in excluded)
                where_clauses.append(f"s.source NOT IN ({placeholders})")
                params.extend(excluded)

        select_sql = f"""
            SELECT s.id, s.title, s.model, s.message_count,
                   s.started_at, s.source,
                   {session_source_expr},
                   {user_id_expr},
                   {chat_id_expr},
                   {chat_type_expr},
                   {thread_id_expr},
                   {session_key_expr},
                   {origin_chat_id_expr},
                   {origin_user_id_expr},
                   {platform_expr},
                   {member_id_expr},
                   {parent_expr},
                   {ended_expr},
                   {end_reason_expr},
                   COUNT(m.id) AS actual_message_count,
                   {user_message_count_expr} AS actual_user_message_count,
                   MAX(m.timestamp) AS last_activity
        """
        if limit is not None:
            result_limit = max(0, int(limit))
            if result_limit == 0:
                return []
            # The sidebar only needs a small visible window. Bound the expensive
            # messages join to a recent-activity candidate set instead of
            # aggregating every historical Intellect state.db session before
            # slicing in Python. The candidate ordering must include the latest
            # message timestamp, not only ``started_at``: long-lived CLI sessions
            # can be resumed days later and should still surface at the top.
            # Oversampling preserves room for hidden compression segments or
            # other rows filtered after projection.
            candidate_limit = max(result_limit * 8, result_limit)
            cur.execute(
                f"""
                WITH candidates AS (
                    SELECT s.id
                    FROM sessions s
                    WHERE {' AND '.join(where_clauses)}
                    ORDER BY COALESCE(
                        (SELECT MAX(mx.timestamp) FROM messages mx WHERE mx.session_id = s.id),
                        s.started_at
                    ) DESC,
                    s.started_at DESC
                    LIMIT ?
                )
                {select_sql}
                FROM sessions s
                JOIN candidates c ON c.id = s.id
                LEFT JOIN messages m ON m.session_id = s.id
                GROUP BY s.id
                ORDER BY COALESCE(MAX(m.timestamp), s.started_at) DESC
                """,
                [*params, candidate_limit],
            )
        else:
            cur.execute(
                f"""
                {select_sql}
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                WHERE {' AND '.join(where_clauses)}
                GROUP BY s.id
                ORDER BY COALESCE(MAX(m.timestamp), s.started_at) DESC
                """,
                params,
            )
        projected = _project_agent_session_rows([dict(row) for row in cur.fetchall()])
        projected = [_with_normalized_source(row) for row in projected]
        projected = [row for row in projected if is_cli_session_row_visible(row)]
        if limit is None:
            return projected
        return projected[:max(0, int(limit))]
    finally:
        db.close()


def _map_rich_row_to_importable(row: dict) -> dict:
    """Normalize :meth:`SessionDB.list_sessions_rich` rows for WebUI projection."""
    mapped = dict(row)
    mapped["last_activity"] = row.get("last_active") or row.get("started_at")
    msg_count = row.get("message_count") or 0
    mapped["actual_message_count"] = msg_count
    mapped["actual_user_message_count"] = row.get("actual_user_message_count")
    return mapped


def read_importable_agent_session_rows_for_profile(
    profile: str | None = None,
    limit: int = 200,
    log=None,
    exclude_sources: tuple[str, ...] | None = ("cron", "webui"),
    actor_member_id: str | None = None,
) -> list[dict]:
    """Like :func:`read_importable_agent_session_rows` but resolves storage per profile."""
    try:
        from api.storage_bridge import (
            get_session_db,
            resolve_storage_backend_name,
            resolve_state_db_path,
        )
    except ImportError:
        return []

    db = get_session_db(profile)
    if db is None:
        return []

    log = log or logger
    try:
        exclude = list(exclude_sources) if exclude_sources else None
        if limit is not None:
            result_limit = max(0, int(limit))
            if result_limit == 0:
                return []
            candidate_limit = max(result_limit * 8, result_limit)
        else:
            result_limit = None
            candidate_limit = 2000

        # Include compression continuations so Python projection can collapse
        # chains the same way as the legacy sqlite3 sidebar query.
        rows = db.list_sessions_rich(
            exclude_sources=exclude,
            limit=candidate_limit,
            order_by_last_active=True,
            project_compression_tips=False,
            include_children=True,
            member_id=actor_member_id,
        )
        mapped = [_map_rich_row_to_importable(r) for r in rows]
        projected = _project_agent_session_rows(mapped)
        projected = [_with_normalized_source(row) for row in projected]
        projected = [row for row in projected if is_cli_session_row_visible(row)]
        if result_limit is None:
            return projected
        return projected[:result_limit]
    except Exception:
        log.debug("PG importable session listing failed", exc_info=True)
        return []
    finally:
        try:
            db.close()
        except Exception:
            pass  # intentionally silent — cleanup/teardown path


def _lineage_report_row(row: dict, role: str) -> dict:
    updated_at = row.get('ended_at') if row.get('ended_at') is not None else row.get('started_at')
    return {
        'session_id': row.get('id'),
        'role': role,
        'title': row.get('title'),
        'source': row.get('source'),
        'started_at': row.get('started_at'),
        'updated_at': updated_at,
        'end_reason': row.get('end_reason'),
        'active': row.get('ended_at') is None,
        'archived': False,
    }


def _empty_lineage_report(session_id: str, *, found: bool = False) -> dict:
    return {
        'mutation': False,
        'found': found,
        'session_id': session_id,
        'lineage_key': session_id,
        'tip_session_id': session_id,
        'total_segments': 0,
        'materialized_segments': 0,
        'segments': [],
        'children': [],
        'manual_review': False,
    }


def _lineage_report_row_dict(row) -> dict | None:
    if row is None:
        return None
    if hasattr(row, "keys"):
        return dict(row)
    return {
        "id": row[0],
        "source": row[1] if len(row) > 1 else None,
        "session_source": row[2] if len(row) > 2 else None,
        "title": row[3] if len(row) > 3 else None,
        "started_at": row[4] if len(row) > 4 else None,
        "parent_session_id": row[5] if len(row) > 5 else None,
        "ended_at": row[6] if len(row) > 6 else None,
        "end_reason": row[7] if len(row) > 7 else None,
    }


def _read_session_lineage_report_with_conn(
    conn,
    session_id: str | None,
    max_hops: int = 20,
) -> dict:
    sid = str(session_id or '').strip()
    if not sid:
        return _empty_lineage_report('')

    try:
        session_cols = _session_columns(conn)
        required = {'id', 'parent_session_id', 'end_reason'}
        if not required.issubset(session_cols):
            return _empty_lineage_report(sid)

        source_expr = _optional_col('source', session_cols)
        session_source_expr = _optional_col('session_source', session_cols)
        title_expr = _optional_col('title', session_cols)
        started_expr = _optional_col('started_at', session_cols, '0')
        ended_expr = _optional_col('ended_at', session_cols)
        end_reason_expr = _optional_col('end_reason', session_cols)
        parent_expr = _optional_col('parent_session_id', session_cols)

        def fetch_one(row_id: str | None) -> dict | None:
            if not row_id:
                return None
            cur = _conn_execute(
                conn,
                f"""
                SELECT s.id,
                       {source_expr},
                       {session_source_expr},
                       {title_expr},
                       {started_expr},
                       {parent_expr},
                       {ended_expr},
                       {end_reason_expr}
                FROM sessions s
                WHERE s.id = ?
                """,
                (row_id,),
            )
            return _lineage_report_row_dict(cur.fetchone())

        target = fetch_one(sid)
        if not target:
            return _empty_lineage_report(sid)

        segments = [target]
        current = target
        seen = {sid}
        manual_review = False
        for _hop in range(max(0, int(max_hops))):
            parent_id = current.get('parent_session_id')
            parent = fetch_one(parent_id)
            if not parent or parent_id in seen:
                manual_review = bool(parent_id and parent_id in seen)
                break
            if not _is_continuation_session(parent, current):
                break
            segments.append(parent)
            seen.add(parent_id)
            current = parent
        else:
            manual_review = True

        segment_ids = {row['id'] for row in segments}
        child_rows: list[dict] = []
        for parent in segments:
            cur = _conn_execute(
                conn,
                f"""
                SELECT s.id,
                       {source_expr},
                       {session_source_expr},
                       {title_expr},
                       {started_expr},
                       {parent_expr},
                       {ended_expr},
                       {end_reason_expr}
                FROM sessions s
                WHERE s.parent_session_id = ?
                ORDER BY s.started_at DESC
                """,
                (parent['id'],),
            )
            for child_row in cur.fetchall():
                child = _lineage_report_row_dict(child_row)
                if not child or child['id'] in segment_ids:
                    continue
                if _is_continuation_session(parent, child):
                    manual_review = True
                    continue
                child_rows.append(child)
    except Exception:
        return _empty_lineage_report(sid)

    root_id = segments[-1]['id'] if segments else sid
    tip_id = segments[0]['id'] if segments else sid
    return {
        'mutation': False,
        'found': True,
        'session_id': sid,
        'lineage_key': root_id,
        'tip_session_id': tip_id,
        'total_segments': len(segments),
        'materialized_segments': len(segments),
        'segments': [
            _lineage_report_row(row, 'tip' if idx == 0 else 'hidden_segment')
            for idx, row in enumerate(segments)
        ],
        'children': [_lineage_report_row(row, 'child_session') for row in child_rows],
        'manual_review': manual_review,
    }


def read_session_lineage_report(db_path: Path, session_id: str | None, max_hops: int = 20) -> dict:
    """Return a bounded, read-only lifecycle report for a session lineage.

    This helper intentionally reports only facts that can be derived from
    ``state.db.sessions`` without mutating WebUI JSON, archiving rows, or
    deleting historical segments. It mirrors the sidebar continuation rules so
    a future UI/PR can explain which rows are hidden compression/cli-close
    segments and which child-session branches remain distinct.
    """
    _warn_deprecated_db_path_api("read_session_lineage_report")
    sid = str(session_id or '').strip()
    if not sid:
        return _empty_lineage_report('')
    db_path = Path(db_path)
    if not db_path.exists():
        return _empty_lineage_report(sid)

    try:
        db, conn = _conn_from_db_path(db_path)
        try:
            return _read_session_lineage_report_with_conn(conn, sid, max_hops)
        finally:
            db.close()
    except Exception:
        return _empty_lineage_report(sid)


def read_session_lineage_report_for_profile(
    profile: str | None,
    session_id: str | None,
    max_hops: int = 20,
) -> dict:
    """Profile-aware lineage report via :func:`api.storage_bridge.get_session_db`."""
    sid = str(session_id or '').strip()
    try:
        from api.storage_bridge import get_session_db
    except ImportError:
        return _empty_lineage_report(sid)
    db = get_session_db(profile)
    if db is None:
        return _empty_lineage_report(sid)
    try:
        conn = getattr(db, "_conn", None)
        if conn is None:
            return _empty_lineage_report(sid)
        return _read_session_lineage_report_with_conn(conn, sid, max_hops)
    except Exception:
        return _empty_lineage_report(sid)
    finally:
        try:
            db.close()
        except Exception:
            pass  # intentionally silent — cleanup/teardown path


def read_session_lineage_metadata(
    db_path: Path | Any,
    session_ids: list[str] | set[str],
    *,
    _conn=None,
) -> dict[str, dict]:
    """Return compression-lineage metadata for known WebUI sidebar sessions.

    WebUI sessions are persisted as JSON files, but Intellect Agent also mirrors
    them into ``state.db.sessions`` for insights/session history. Compression
    and cross-surface continuation create parent chains there. ``/api/sessions``
    needs to surface that lineage to the sidebar so client-side collapse can
    group logical continuations without mutating or deleting any session files.

    Missing DBs, old schemas, or incomplete rows degrade to an empty mapping.
    """
    wanted = {str(sid) for sid in (session_ids or []) if sid}
    if not wanted:
        return {}

    if _conn is not None:
        return _read_session_lineage_metadata_with_conn(_conn, wanted)

    _warn_deprecated_db_path_api("read_session_lineage_metadata")
    db_path = Path(db_path)
    if not db_path.exists():
        return {}

    try:
        db, conn = _conn_from_db_path(db_path)
        try:
            return _read_session_lineage_metadata_with_conn(conn, wanted)
        finally:
            db.close()
    except Exception:
        return {}


def _conn_execute(conn, sql: str, params: tuple = ()):
    """Run SQL on sqlite3, PG adapter, or test tracking connections."""
    execute = getattr(conn, "execute", None)
    if execute is not None:
        return execute(sql, params)
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur


def _session_columns(conn) -> set[str]:
    try:
        from api.storage_bridge import table_column_names_from_conn

        return table_column_names_from_conn(conn, "sessions")
    except Exception:
        rows = _conn_execute(conn, "PRAGMA table_info(sessions)").fetchall()
        out: set[str] = set()
        for row in rows:
            if hasattr(row, "keys"):
                out.add(row["name"])
            else:
                out.add(row[1])
        return out


def _read_session_lineage_metadata_with_conn(conn, wanted: set[str]) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    try:
        session_cols = _session_columns(conn)
        if 'parent_session_id' not in session_cols or 'end_reason' not in session_cols:
            return {}
        session_source_expr = _optional_col('session_source', session_cols)
        # Scoped fetch via PRIMARY KEY + idx_sessions_parent rather than a
        # full table scan. IN-clause is chunked to 500 for SQLITE_MAX_VARIABLE_NUMBER.
        IN_CHUNK = 500
        to_fetch = set(wanted)
        for _hop in range(20):
            if not to_fetch:
                break
            fetch_list = list(to_fetch)
            to_fetch = set()
            for i in range(0, len(fetch_list), IN_CHUNK):
                chunk = fetch_list[i:i + IN_CHUNK]
                placeholders = ','.join('?' * len(chunk))
                result = _conn_execute(
                    conn,
                    f"""
                    SELECT s.id, s.source, {session_source_expr}, s.title, s.started_at, s.parent_session_id, s.ended_at, s.end_reason
                    FROM sessions s
                    WHERE s.id IN ({placeholders})
                    """,
                    tuple(chunk),
                )
                for row in result.fetchall():
                    if hasattr(row, "keys"):
                        item = dict(row)
                    else:
                        item = {
                            "id": row[0],
                            "source": row[1],
                            "title": row[3] if len(row) > 3 else None,
                            "started_at": row[4] if len(row) > 4 else None,
                            "parent_session_id": row[5] if len(row) > 5 else None,
                            "ended_at": row[6] if len(row) > 6 else None,
                            "end_reason": row[7] if len(row) > 7 else None,
                        }
                    rows[item["id"]] = item
            for sid in fetch_list:
                parent_id = rows.get(sid, {}).get('parent_session_id')
                if parent_id and parent_id not in rows and parent_id not in to_fetch:
                    to_fetch.add(parent_id)
    except Exception:
        return {}

    metadata: dict[str, dict] = {}
    for sid in wanted:
        row = rows.get(sid)
        if not row:
            continue

        state_title = str(row.get('title') or '').strip()
        if state_title:
            metadata.setdefault(sid, {})['_state_db_title'] = state_title

        parent_id = row.get('parent_session_id')
        parent_row = rows.get(parent_id) if parent_id else None
        if parent_id and parent_row:
            entry = metadata.setdefault(sid, {})
            entry['parent_session_id'] = parent_id
            if not _is_continuation_session(parent_row, row):
                entry['relationship_type'] = 'child_session'
                entry['parent_title'] = parent_row.get('title')
                entry['parent_source'] = parent_row.get('source')
                parent_source = str(parent_row.get('source') or '').strip().lower()
                child_source = str(row.get('source') or '').strip().lower()
                if parent_source and child_source and parent_source != child_source:
                    entry['_cross_surface_child_session'] = True
                parent_root = _continuation_root_id(rows, parent_id)
                if parent_root:
                    entry['_parent_lineage_root_id'] = parent_root
                continue

        root_id = sid
        current_id = sid
        segment_count = 1
        seen = {sid}
        while True:
            current = rows.get(current_id)
            parent_id = current.get('parent_session_id') if current else None
            parent = rows.get(parent_id) if parent_id else None
            if not parent or parent_id in seen:
                break
            if not _is_continuation_session(parent, current):
                break
            root_id = parent_id
            current_id = parent_id
            seen.add(parent_id)
            segment_count += 1

        if root_id != sid:
            entry = metadata.setdefault(sid, {})
            entry['_lineage_root_id'] = root_id
            entry['_compression_segment_count'] = segment_count

    return metadata


def read_session_lineage_metadata_for_profile(
    profile: str | None,
    session_ids: list[str] | set[str],
) -> dict[str, dict]:
    """Profile-aware lineage metadata via :func:`api.storage_bridge.get_session_db`."""

    def _hook_state_db_path() -> Path | None:
        try:
            from api import models as _models

            hook_path = _models._active_state_db_path()
        except Exception:
            hook_path = None
        if hook_path is not None and Path(hook_path).is_file():
            return Path(hook_path)
        return None

    hook_path = _hook_state_db_path()
    if hook_path is not None:
        return read_session_lineage_metadata(hook_path, session_ids)

    try:
        from api.storage_bridge import get_session_db, resolve_state_db_path
    except ImportError:
        return {}

    db_path = resolve_state_db_path(profile)
    if db_path is not None and Path(db_path).is_file():
        return read_session_lineage_metadata(Path(db_path), session_ids)

    db = get_session_db(profile)
    if db is None:
        return {}
    try:
        conn = getattr(db, "_conn", None)
        if conn is None:
            return {}
        return read_session_lineage_metadata(Path("."), session_ids, _conn=conn)
    except Exception:
        return {}
    finally:
        try:
            db.close()
        except Exception:
            pass  # intentionally silent — cleanup/teardown path
