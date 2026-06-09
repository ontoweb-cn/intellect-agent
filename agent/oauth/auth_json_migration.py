"""One-time migration: auth.json OAuth tokens → oauth_tokens table.

Called on first startup after schema v19.  Reads existing OAuth credentials
from the legacy auth.json store and migrates them to the encrypted
oauth_tokens table.  The original entries in auth.json are preserved
so existing code paths continue to work during the transition period.

Migration is idempotent — existing oauth_tokens rows are not overwritten.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MIGRATION_MARKER = ".oauth_tokens_migrated"


def _marker_path() -> Path:
    from intellect_constants import get_intellect_home

    return get_intellect_home() / _MIGRATION_MARKER


def _has_run() -> bool:
    return _marker_path().exists()


def _mark_done() -> None:
    _marker_path().write_text(str(int(time.time())))


def _load_auth_json() -> dict:
    from intellect_cli.auth import _load_auth_store

    try:
        return _load_auth_store()
    except Exception:
        return {}


def _token_already_migrated(db: Any, provider_id: str) -> bool:
    try:
        row = db._conn.execute(
            "SELECT id FROM oauth_tokens WHERE provider_id=? AND member_id IS NULL",
            (provider_id,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _insert_token(
    db: Any,
    provider_id: str,
    access_token: str,
    refresh_token: str,
    *,
    now: float,
    expires_in: int = 0,
    metadata: dict[str, Any] | None = None,
) -> bool:
    from agent.oauth.model_tokens import (
        db_provider_id,
        ensure_model_oauth_provider_row,
        persist_model_token,
    )

    pid = db_provider_id(provider_id)
    if _token_already_migrated(db, pid):
        return False

    try:
        ensure_model_oauth_provider_row(db, provider_id)
        persist_model_token(
            db,
            provider_id,
            access_token=access_token,
            refresh_token=refresh_token or None,
            expires_in=expires_in,
            metadata=metadata,
        )
        logger.info("oauth migrate: %s → oauth_tokens (%s)", provider_id, pid)
        return True
    except Exception as exc:
        logger.warning("oauth migrate: failed %s: %s", provider_id, exc)
        return False


def migrate_auth_tokens(db) -> int:
    """Migrate OAuth tokens from auth.json to oauth_tokens table."""
    if _has_run():
        return 0

    auth = _load_auth_json()
    count = 0
    now = time.time()
    had_work = False

    providers = auth.get("providers", {})
    if isinstance(providers, dict):
        for provider_id, state in providers.items():
            if not isinstance(state, dict):
                continue
            access_token = state.get("access_token") or state.get("agent_key") or ""
            refresh_token = state.get("refresh_token") or ""
            if not access_token:
                continue
            had_work = True
            expires_in = int(state.get("expires_in") or 0)
            meta = {
                k: state[k]
                for k in ("expires_at", "expires_at_ms", "source")
                if state.get(k) is not None
            }
            if _insert_token(
                db,
                str(provider_id),
                access_token,
                refresh_token,
                now=now,
                expires_in=expires_in,
                metadata=meta or None,
            ):
                count += 1

    pool = auth.get("credential_pool", {})
    if isinstance(pool, dict):
        for pool_provider, entries in pool.items():
            if not isinstance(entries, list):
                continue
            best_access = ""
            best_refresh = ""
            best_meta: dict[str, Any] = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                access = (
                    entry.get("access_token")
                    or entry.get("runtime_api_key")
                    or ""
                )
                if access and len(str(access)) > len(best_access):
                    best_access = str(access)
                    best_refresh = str(entry.get("refresh_token") or "")
                    best_meta = {
                        k: entry[k]
                        for k in ("expires_at", "expires_at_ms", "source")
                        if entry.get(k) is not None
                    }
            if best_access:
                had_work = True
                if _insert_token(
                    db,
                    str(pool_provider),
                    best_access,
                    best_refresh,
                    now=now,
                    metadata=best_meta or None,
                ):
                    count += 1

    # Migrate Z.AI endpoint cache from auth.json to oauth_providers.extra_metadata
    _migrate_zai_endpoint_cache(auth, db)

    if count > 0 or not had_work:
        _mark_done()
    if count:
        logger.info("oauth migrate: %d token(s) migrated from auth.json", count)
    return count


def _migrate_zai_endpoint_cache(auth: dict, db: Any) -> None:
    """Migrate Z.AI detected_endpoint from auth.json to oauth_providers.extra_metadata."""
    try:
        from agent.oauth.model_tokens import db_provider_id

        providers = auth.get("providers", {})
        if not isinstance(providers, dict):
            return
        zai_state = providers.get("zai")
        if not isinstance(zai_state, dict):
            return
        endpoint = zai_state.get("detected_endpoint")
        if not isinstance(endpoint, dict) or not endpoint.get("base_url"):
            return

        pid = db_provider_id("zai")
        row = db._conn.execute(
            "SELECT extra_metadata FROM oauth_providers WHERE id=?",
            (pid,),
        ).fetchone()
        if not row:
            return
        existing = row["extra_metadata"] or "{}"
        if "detected_endpoint" in str(existing):
            return  # already migrated

        import json
        db._execute_write(lambda cur: cur.execute(
            "UPDATE oauth_providers SET extra_metadata=?, updated_at=? WHERE id=?",
            (json.dumps({"detected_endpoint": endpoint}), time.time(), pid),
        ))
        logger.info("oauth migrate: zai endpoint cache → oauth_providers.extra_metadata")
    except Exception as exc:
        logger.debug("zai endpoint cache migration failed: %s", exc)
