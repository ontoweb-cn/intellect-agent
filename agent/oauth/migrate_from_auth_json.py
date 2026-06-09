"""Migrate legacy ``auth.json`` OAuth data into ``oauth_tokens`` (PR-A9)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MIGRATION_MARKER_NAME = ".oauth_auth_json_migrated"


def migration_marker_path() -> Path:
    from intellect_constants import get_intellect_home

    return get_intellect_home() / MIGRATION_MARKER_NAME


def migration_marker_exists() -> bool:
    return migration_marker_path().exists()


def write_migration_marker(entry_count: int) -> None:
    path = migration_marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{int(time.time())}\n{entry_count}\n", encoding="utf-8")


def _load_auth_json() -> dict[str, Any]:
    from intellect_cli.auth import _load_auth_store

    try:
        return _load_auth_store()
    except Exception:
        return {}


def summarize_auth_json_oauth(auth: dict[str, Any] | None = None) -> dict[str, Any]:
    """Count migratable OAuth material in auth.json (for dry-run)."""
    auth = auth or _load_auth_json()
    providers = auth.get("providers", {}) if isinstance(auth.get("providers"), dict) else {}
    pool = auth.get("credential_pool", {}) if isinstance(auth.get("credential_pool"), dict) else {}
    provider_ids = set(providers.keys()) | set(pool.keys())
    pool_entry_count = 0
    if isinstance(pool, dict):
        for entries in pool.values():
            if isinstance(entries, list):
                pool_entry_count += sum(
                    1
                    for e in entries
                    if isinstance(e, dict)
                    and (e.get("access_token") or e.get("runtime_api_key"))
                )
    return {
        "provider_count": len(provider_ids),
        "singleton_providers": len(providers),
        "pool_providers": len(pool),
        "pool_entry_count": pool_entry_count,
        "active_provider": auth.get("active_provider"),
    }


def migrate_auth_json_to_db(
    db: Any,
    *,
    dry_run: bool = False,
    prune_auth_json: bool = False,
) -> dict[str, Any]:
    """Migrate auth.json OAuth tokens into oauth_tokens (reuses A4 migrator)."""
    from agent.oauth.auth_json_migration import migrate_auth_tokens

    stats: dict[str, Any] = {
        "dry_run": dry_run,
        "summary": summarize_auth_json_oauth(),
        "migrated": 0,
        "pruned": False,
        "errors": [],
    }
    if dry_run:
        return stats

    try:
        stats["migrated"] = migrate_auth_tokens(db)
        from agent.oauth.pool_storage import migrate_auth_json_pool_to_db

        stats["pool_migrated"] = migrate_auth_json_pool_to_db(db)
    except Exception as exc:
        stats["errors"].append(str(exc))
        return stats

    if not stats["errors"]:
        total = int(stats["summary"].get("pool_entry_count", 0))
        write_migration_marker(max(total, int(stats.get("pool_migrated", 0))))

    if prune_auth_json and not stats["errors"]:
        stats["pruned"] = prune_oauth_sections_in_auth_json()

    return stats


def prune_oauth_sections_in_auth_json() -> bool:
    """Clear OAuth ``providers`` and ``credential_pool``; keep shell metadata."""
    from intellect_cli.auth import _auth_store_lock, _load_auth_store, _save_auth_store

    try:
        with _auth_store_lock():
            auth = _load_auth_store()
            changed = False
            if auth.get("providers"):
                auth["providers"] = {}
                changed = True
            if auth.get("credential_pool"):
                auth["credential_pool"] = {}
                changed = True
            if auth.get("active_provider"):
                auth["active_provider"] = None
                changed = True
            if changed:
                _save_auth_store(auth)
            return changed
    except Exception as exc:
        logger.warning("prune auth.json oauth sections failed: %s", exc)
        return False
