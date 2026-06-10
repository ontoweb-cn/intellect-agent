"""
W1 storage bridge — WebUI state.db access via agent storage factory.

Resolves the active or named profile's INTELLECT_HOME, loads merged
``storage.*`` config, and opens ``SessionDB`` on the same path the agent
uses (``storage.sqlite.path`` or default ``state.db``).

Callers should use :func:`get_session_db` / :func:`resolve_state_db_path`
instead of constructing ``SessionDB`` or ``sqlite3.connect`` directly.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


def resolve_intellect_home(profile: str | None = None) -> Path:
    """Return INTELLECT_HOME for *profile* (name) or the active WebUI profile.

    When *profile* is explicit, invalid names or resolution failures raise
    :class:`LookupError` so callers do not fall back to the active profile's
    DB (#2762).
    """
    if profile is None:
        try:
            from api.profiles import get_active_intellect_home

            return Path(get_active_intellect_home()).expanduser().resolve()
        except Exception:
            logger.debug("active profile home resolution failed, using env")
            return Path(
                os.getenv("INTELLECT_HOME", str(Path.home() / ".intellect"))
            ).expanduser().resolve()

    from api.profiles import (
        _PROFILE_ID_RE,
        _is_root_profile,
        _resolve_profile_home_for_name,
    )

    if profile == "":
        raise LookupError("empty profile name")
    if not _is_root_profile(profile) and not _PROFILE_ID_RE.fullmatch(profile):
        raise LookupError(f"invalid profile name: {profile!r}")

    return Path(_resolve_profile_home_for_name(profile)).expanduser().resolve()


@contextmanager
def _scoped_intellect_home(home: Path) -> Iterator[None]:
    """Scope INTELLECT_HOME to *home* without mutating ``os.environ``."""
    from intellect_constants import reset_intellect_home_override, set_intellect_home_override

    token = set_intellect_home_override(home)
    try:
        yield
    finally:
        reset_intellect_home_override(token)


def infer_connection_dialect(conn) -> str:
    """Best-effort SQL dialect for SessionDB._conn or sqlite3.Connection."""
    conn_type = type(conn).__name__
    if conn_type in ("PGConnectionAdapter", "PGCursorAdapter"):
        return "postgresql"
    inner = getattr(conn, "_connection", conn)
    module = type(inner).__module__
    if "psycopg2" in module or "pg8000" in module or "asyncpg" in module:
        return "postgresql"
    return "sqlite"


def _column_name_from_row(row) -> str:
    if hasattr(row, "keys"):
        mapping = dict(row) if not isinstance(row, dict) else row
        if "column_name" in mapping:
            return str(mapping["column_name"])
        if "name" in mapping:
            return str(mapping["name"])
    if isinstance(row, (list, tuple)) and len(row) > 1:
        return str(row[1])
    return str(row[0])


def table_column_names_from_conn(conn, table: str) -> set[str]:
    """Return column names for *table* on *conn* (sqlite3 or SessionDB adapter)."""
    safe_table = str(table).strip().replace('"', "")
    dialect = infer_connection_dialect(conn)
    if dialect == "postgresql":
        execute = getattr(conn, "execute", None)
        if execute is None:
            cur = conn.cursor()
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = %s",
                (safe_table.lower(),),
            )
            rows = cur.fetchall()
        else:
            rows = execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = ?",
                (safe_table.lower(),),
            ).fetchall()
        names: set[str] = set()
        for row in rows:
            names.add(_column_name_from_row(row))
        return names
    execute = getattr(conn, "execute", None)
    if execute is None:
        cur = conn.cursor()
        cur.execute(f'PRAGMA table_info("{safe_table}")')
        rows = cur.fetchall()
    else:
        rows = execute(f'PRAGMA table_info("{safe_table}")').fetchall()
    return {_column_name_from_row(row) for row in rows}


def sessions_order_by_started_desc_sql(*, dialect: str) -> str:
    if dialect == "postgresql":
        return "ORDER BY started_at DESC NULLS LAST"
    return "ORDER BY COALESCE(started_at, 0) DESC"


def messages_order_by_sql(message_cols: set[str], *, dialect: str) -> str:
    if "timestamp" in message_cols and "id" in message_cols:
        return "timestamp, id"
    if "id" in message_cols:
        return "id"
    if dialect == "sqlite":
        return "rowid"
    return "id"


def optional_column_sql(
    name: str,
    columns: set[str],
    fallback: str | None = None,
    *,
    dialect: str = "sqlite",
) -> str:
    if name in columns:
        return name
    if fallback is None:
        return f"NULL AS {name}"
    if name == "started_at" and dialect == "postgresql" and fallback == "0":
        return f"NULL::timestamptz AS {name}"
    return f"{fallback} AS {name}"


def get_storage_manager_for_profile(profile: str | None = None) -> Any | None:
    """Return agent :class:`~agent.storage.StorageManager` for *profile*, or None."""
    try:
        from agent.webui_storage import get_webui_storage_manager
    except ImportError:
        return None

    home = resolve_intellect_home(profile)
    try:
        with _scoped_intellect_home(home):
            return get_webui_storage_manager()
    except Exception:
        logger.debug("get_storage_manager_for_profile failed", exc_info=True)
        return None


def resolve_storage_backend_name(profile: str | None = None) -> str:
    """Active storage backend for *profile* (``sqlite`` or ``postgresql``)."""
    try:
        from agent.storage.factory import get_storage_backend_name
        from intellect_cli.config import load_config
    except ImportError:
        return "sqlite"

    try:
        home = resolve_intellect_home(profile)
    except LookupError:
        return "sqlite"

    try:
        with _scoped_intellect_home(home):
            return get_storage_backend_name(load_config())
    except Exception:
        logger.debug("resolve_storage_backend_name failed", exc_info=True)
        return "sqlite"


def resolve_state_db_path(profile: str | None = None) -> Path | None:
    """Return the on-disk SQLite ``state.db`` path when backend is sqlite.

    When ``storage.backend`` is ``postgresql``, returns ``None`` — the active store
    is PostgreSQL; do not use this for existence checks or ``sqlite3.connect``.
    """
    if resolve_storage_backend_name(profile) == "postgresql":
        return None
    home = resolve_intellect_home(profile)
    with _scoped_intellect_home(home):
        manager = get_storage_manager_for_profile(profile)
        if manager is not None:
            db_path = getattr(manager.db, "db_path", None)
            if db_path:
                try:
                    return Path(db_path).expanduser().resolve()
                except Exception:
                    logger.debug("storage manager db path failed", exc_info=True)
        from intellect_cli.config import load_config

        cfg = load_config()
        storage = cfg.get("storage") if isinstance(cfg.get("storage"), dict) else {}
        sqlite = storage.get("sqlite") if isinstance(storage.get("sqlite"), dict) else {}
        custom = str(sqlite.get("path") or "").strip()
        if custom:
            return Path(custom).expanduser().resolve()
    return (home / "state.db").resolve()


def state_storage_available(profile: str | None = None) -> bool:
    """True when the profile has a readable session store (SQLite file or PG backend)."""
    if resolve_storage_backend_name(profile) == "postgresql":
        try:
            from intellect_cli.config import load_config
        except ImportError:
            return False
        try:
            home = resolve_intellect_home(profile)
        except LookupError:
            return False
        try:
            with _scoped_intellect_home(home):
                cfg = load_config()
        except Exception:
            logger.debug("state_storage_available config load failed", exc_info=True)
            return False
        storage = cfg.get("storage") if isinstance(cfg.get("storage"), dict) else {}
        pg = storage.get("postgresql") if isinstance(storage.get("postgresql"), dict) else {}
        dsn = str(pg.get("dsn") or os.getenv("INTELLECT_PG_DSN") or "").strip()
        return bool(dsn)
    db_path = resolve_state_db_path(profile)
    return bool(db_path and db_path.is_file())


def table_column_names(db: Any, table: str) -> set[str]:
    """Return column names for *table* on the active SessionDB connection."""
    return table_column_names_from_conn(db._conn, table)


@contextmanager
def open_session_db(profile: str | None = None) -> Iterator[Any]:
    """Context manager around :func:`get_session_db` (always closes)."""
    db = get_session_db(profile)
    if db is None:
        yield None
        return
    try:
        yield db
    finally:
        try:
            db.close()
        except Exception:
            logger.debug("open_session_db close failed", exc_info=True)


def get_session_db(profile: str | None = None):
    """Open :class:`intellect_state.SessionDB` for *profile* (caller must ``close()``).

    Uses the agent storage factory when ``storage.backend`` is ``postgresql`` (W2).
    For SQLite, opens the profile's ``state.db`` file when it exists.
    """
    try:
        from intellect_state import SessionDB
    except ImportError:
        return None

    try:
        home = resolve_intellect_home(profile)
    except LookupError:
        logger.debug("refusing state.db open for invalid profile %r", profile)
        return None

    try:
        with _scoped_intellect_home(home):
            if resolve_storage_backend_name(profile) == "postgresql":
                return SessionDB()
            db_path = resolve_state_db_path(profile)
            if db_path is None or not db_path.is_file():
                return None
            return SessionDB(db_path=db_path)
    except Exception:
        logger.debug("Failed to open SessionDB for profile %r", profile, exc_info=True)
        return None
