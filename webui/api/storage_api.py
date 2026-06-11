"""WebUI API for P2 storage migration (W7) — loopback-only."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from api.helpers import bad, j
from intellect_constants import display_intellect_home, get_intellect_home

logger = logging.getLogger(__name__)


def agent_storage_available() -> bool:
    try:
        from api.config import _INTELLECT_FOUND

        if not _INTELLECT_FOUND:
            return False
        from agent.storage.factory import create_storage_backend  # noqa: F401

        return True
    except Exception:
        return False


def _require_loopback(handler) -> bool:
    from api.auth import is_loopback_client

    if not is_loopback_client(handler):
        bad(handler, "Storage API requires localhost access", 403)
        return False
    return True


def _pg_deps_available() -> bool:
    try:
        import sqlalchemy  # noqa: F401
        import psycopg2  # noqa: F401

        return True
    except ImportError:
        return False


def build_dsn_from_body(body: dict[str, Any]) -> str:
    """Resolve DSN from request body (dsn or host/port/user/password/database)."""
    dsn = str(body.get("dsn") or "").strip()
    if dsn:
        return dsn
    host = str(body.get("host") or "localhost").strip()
    port = int(body.get("port") or 5432)
    user = str(body.get("user") or "intellect").strip()
    password = str(body.get("password") or "")
    database = str(body.get("database") or "intellect").strip()
    ssl_mode = str(body.get("ssl_mode") or "prefer").strip()
    auth = quote_plus(user)
    if password:
        auth = f"{quote_plus(user)}:{quote_plus(password)}"
    base = f"postgresql://{auth}@{host}:{port}/{quote_plus(database)}"
    if ssl_mode and ssl_mode != "prefer":
        base = f"{base}?sslmode={quote_plus(ssl_mode)}"
    return base


def _dual_write_status() -> dict[str, Any]:
    try:
        from agent.storage.dual_write import probe_dual_write_risk
        from intellect_cli.config import load_config

        report = probe_dual_write_risk(get_intellect_home(), load_config())
        return {
            "dual_write_risk": report.risk,
            "dual_write_messages": list(report.messages),
            "active_sqlite_on_pg": report.active_sqlite_present,
            "sqlite_recently_modified": report.sqlite_recently_modified,
            "divergent_tables": {
                table: {"sqlite": pair[0], "postgresql": pair[1]}
                for table, pair in sorted(report.divergent_tables.items())
            },
        }
    except Exception:
        logger.debug("dual_write status probe failed", exc_info=True)
        return {
            "dual_write_risk": "unknown",
            "dual_write_messages": [],
            "active_sqlite_on_pg": False,
            "sqlite_recently_modified": False,
            "divergent_tables": {},
        }


def storage_status_payload() -> dict[str, Any]:
    from api.config import _INTELLECT_FOUND

    sqlite_path = get_intellect_home() / "state.db"
    backend = "sqlite"
    if _INTELLECT_FOUND:
        try:
            from intellect_cli.config import load_config
            from agent.storage.factory import get_storage_backend_name

            cfg = load_config()
            backend = get_storage_backend_name(cfg)
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)
    pg_dsn = ""
    if _INTELLECT_FOUND and backend == "postgresql":
        try:
            from agent.storage.postgres_backend import resolve_postgresql_dsn
            from intellect_cli.config import load_config

            pg_dsn = resolve_postgresql_dsn(load_config()) or ""
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)
    payload: dict[str, Any] = {
        "agent_available": agent_storage_available(),
        "pg_deps_available": _pg_deps_available(),
        "storage_backend": backend,
        "sqlite_path": str(sqlite_path),
        "sqlite_exists": sqlite_path.exists(),
        "pg_dsn_configured": bool(pg_dsn),
        "fresh_pg_install": backend != "postgresql" or not sqlite_path.exists(),
        "intellect_home": display_intellect_home(),
        "defaults": {
            "storage": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "intellect",
            "user": "intellect",
        },
    }
    payload.update(_dual_write_status())
    return payload


def handle_get(handler, parsed) -> bool:
    if parsed.path != "/api/storage/status":
        return False
    if not _require_loopback(handler):
        return True
    if not agent_storage_available():
        return bad(handler, "Storage API requires intellect-agent", 503)
    return j(handler, storage_status_payload())


def handle_post(handler, parsed, body: dict[str, Any] | None) -> bool:
    path = parsed.path
    if not path.startswith("/api/storage/"):
        return False
    if not _require_loopback(handler):
        return True
    if not agent_storage_available():
        return bad(handler, "Storage API requires intellect-agent", 503)

    payload = body if isinstance(body, dict) else {}

    if path == "/api/storage/test-pg":
        if not _pg_deps_available():
            return bad(
                handler,
                "PostgreSQL extras not installed (pip install 'intellect-agent[db-postgresql]')",
                503,
            )
        dsn = build_dsn_from_body(payload)
        if not dsn:
            return bad(handler, "dsn or host/user/database required", 400)
        try:
            from agent.storage.postgres_backend import PGStorageBackend
            from intellect_cli.config import load_config

            cfg = load_config()
            cfg = dict(cfg)
            storage = dict(cfg.get("storage") or {})
            pg = dict(storage.get("postgresql") or {})
            pg["dsn"] = dsn
            storage["backend"] = "postgresql"
            storage["postgresql"] = pg
            cfg["storage"] = storage
            backend = PGStorageBackend(cfg)
            backend.initialize()
            row = backend.fetchone("SELECT 1 AS ok")
            backend.close()
            return j(handler, {"ok": True, "message": "PostgreSQL connection OK", "probe": row})
        except Exception as exc:
            logger.debug("test-pg failed", exc_info=True)
            return bad(handler, f"PostgreSQL connection failed: {exc}", 400)

    if path == "/api/storage/init-pg-schema":
        if not _pg_deps_available():
            return bad(
                handler,
                "PostgreSQL extras not installed (pip install 'intellect-agent[db-postgresql]')",
                503,
            )
        dsn = build_dsn_from_body(payload)
        if not dsn:
            return bad(handler, "dsn or host/user/database required", 400)
        apply_config = bool(payload.get("apply_config"))
        enable_redis = bool(payload.get("enable_redis"))
        verify_only = bool(payload.get("verify_only"))
        try:
            from agent.storage.migrate_sqlite_to_pg import init_postgresql_schema
            from intellect_cli.config import load_config

            result = init_postgresql_schema(
                dsn=dsn,
                config=load_config(),
                update_config=apply_config,
                enable_redis=enable_redis,
                verify_only=verify_only,
            )
            return j(handler, result, status=200 if result.get("ok") else 500)
        except Exception as exc:
            logger.debug("init-pg-schema failed", exc_info=True)
            return bad(handler, f"PostgreSQL init failed: {exc}", 500)

    if path == "/api/storage/migrate-sqlite-to-pg":
        dry_run = bool(payload.get("dry_run"))
        if not dry_run and not _pg_deps_available():
            return bad(
                handler,
                "PostgreSQL extras not installed (pip install 'intellect-agent[db-postgresql]')",
                503,
            )
        dsn = build_dsn_from_body(payload)
        if not dsn:
            return bad(handler, "dsn or host/user/database required", 400)
        apply_config = bool(payload.get("apply_config"))
        enable_redis = bool(payload.get("enable_redis"))
        sqlite_raw = str(payload.get("sqlite_path") or "").strip()
        sqlite_path = (
            Path(sqlite_raw).expanduser()
            if sqlite_raw
            else get_intellect_home() / "state.db"
        )
        if not sqlite_path.exists():
            return bad(handler, f"SQLite database not found: {sqlite_path}", 404)
        try:
            from agent.storage.migrate_sqlite_to_pg import migrate_sqlite_to_postgresql
            from intellect_cli.config import load_config

            report = migrate_sqlite_to_postgresql(
                sqlite_path=sqlite_path,
                dsn=dsn,
                config=load_config(),
                dry_run=dry_run,
                backup=not dry_run,
                update_config=apply_config,
                enable_redis=enable_redis,
            )
            tables = [
                {
                    "name": t.name,
                    "source_rows": t.source_rows,
                    "copied_rows": t.copied_rows,
                    "skipped": t.skipped,
                    "error": t.error,
                }
                for t in report.tables
            ]
            errors = [t for t in report.tables if t.error]
            return j(
                handler,
                {
                    "ok": not errors,
                    "dry_run": report.dry_run,
                    "sqlite_path": str(report.sqlite_path),
                    "dsn": report.dsn,
                    "backup_path": str(report.backup_path) if report.backup_path else None,
                    "config_updated": report.config_updated,
                    "retired_sqlite_path": (
                        str(report.retired_sqlite_path) if report.retired_sqlite_path else None
                    ),
                    "checksum": report.checksum_summary(),
                    "tables": tables,
                },
                status=200 if not errors else 500,
            )
        except Exception as exc:
            logger.debug("migrate-sqlite-to-pg failed", exc_info=True)
            return bad(handler, f"Migration failed: {exc}", 500)

    return False
