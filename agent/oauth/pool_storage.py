"""OAuth credential pool persistence in ``oauth_pool_entries`` (PR-A7)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

GLOBAL_PROFILE_SCOPE = "__global__"


def current_profile_scope() -> str:
    """Scope key for the active ``INTELLECT_HOME`` (empty = default root)."""
    try:
        from intellect_constants import get_default_intellect_root, get_intellect_home

        home = get_intellect_home().resolve()
        try:
            root = get_default_intellect_root().resolve()
            if home == root:
                return ""
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)
        return hashlib.sha256(str(home).encode("utf-8")).hexdigest()[:16]
    except Exception:
        return ""


def _db_provider_id(runtime_provider_id: str) -> str:
    from agent.oauth.model_tokens import db_provider_id

    return db_provider_id(runtime_provider_id)


def _extra_metadata_from_entry(entry: dict[str, Any]) -> dict[str, Any]:
    known = {
        "id", "label", "auth_type", "priority", "source", "access_token",
        "refresh_token", "last_status", "last_status_at", "last_error_code",
        "last_error_reason", "last_error_message", "last_error_reset_at",
        "base_url", "expires_at", "expires_at_ms", "last_refresh",
        "inference_base_url", "agent_key", "agent_key_expires_at", "request_count",
    }
    return {k: v for k, v in entry.items() if k not in known and v is not None}


def pool_entry_to_credential_dict(row: dict[str, Any]) -> dict[str, Any]:
    from agent.oauth.storage import decrypt_token

    access_enc = row.get("access_token_encrypted") or ""
    refresh_enc = row.get("refresh_token_encrypted") or ""
    access = decrypt_token(access_enc) if access_enc else ""
    refresh = decrypt_token(refresh_enc) if refresh_enc else ""

    try:
        meta = json.loads(row.get("metadata") or "{}")
    except json.JSONDecodeError:
        meta = {}

    out: dict[str, Any] = {
        "id": row.get("id") or "",
        "label": row.get("label") or "",
        "auth_type": meta.get("auth_type") or "oauth",
        "priority": int(row.get("priority") or 0),
        "source": row.get("source") or "",
        "access_token": access,
        "base_url": row.get("base_url") or "",
    }
    if refresh:
        out["refresh_token"] = refresh
    status = row.get("status")
    if status and status != "ok":
        out["last_status"] = status
    for key in (
        "last_status_at", "last_error_code", "last_error_reason",
        "last_error_message", "last_error_reset_at", "expires_at",
        "expires_at_ms", "last_refresh", "inference_base_url",
        "agent_key", "agent_key_expires_at", "request_count",
    ):
        if key in meta and meta[key] is not None:
            out[key] = meta[key]
    for key, val in meta.get("extra", {}).items():
        if val is not None:
            out[key] = val
    return out


def credential_dict_to_pool_row(
    runtime_provider_id: str,
    entry: dict[str, Any],
    *,
    profile_scope: str,
) -> dict[str, Any]:
    from agent.oauth.storage import encrypt_token

    access = str(entry.get("access_token") or entry.get("runtime_api_key") or "").strip()
    is_secret_free_link_marker = (
        str(entry.get("auth_type") or "") == "oauth"
        and str(entry.get("source") or "") == "claude_code_linked"
    )
    if not access and not is_secret_free_link_marker:
        raise ValueError("pool entry missing access_token")

    refresh = str(entry.get("refresh_token") or "").strip()
    entry_id = str(entry.get("id") or "").strip() or f"pool-{uuid.uuid4().hex[:12]}"
    status = str(entry.get("last_status") or "ok").strip() or "ok"
    if status not in ("ok", "exhausted", "dead"):
        status = "ok"

    meta = _extra_metadata_from_entry(entry)
    meta["auth_type"] = entry.get("auth_type") or meta.get("auth_type") or "oauth"
    for key in (
        "last_status_at", "last_error_code", "last_error_reason",
        "last_error_message", "last_error_reset_at", "expires_at",
        "expires_at_ms", "last_refresh", "inference_base_url",
        "agent_key", "agent_key_expires_at", "request_count",
    ):
        if key in entry and entry[key] is not None:
            meta[key] = entry[key]

    now = time.time()
    return {
        "id": entry_id,
        "provider_id": _db_provider_id(runtime_provider_id),
        "profile_scope": profile_scope,
        "label": str(entry.get("label") or entry.get("source") or runtime_provider_id),
        "source": str(entry.get("source") or ""),
        "priority": int(entry.get("priority") or 0),
        "status": status,
        "access_token_encrypted": encrypt_token(access),
        "refresh_token_encrypted": encrypt_token(refresh) if refresh else None,
        "base_url": str(entry.get("base_url") or ""),
        "metadata": json.dumps(meta),
        "issued_at": now,
        "updated_at": now,
        "last_used_at": now,
    }


def _fetch_pool_rows(db: Any, runtime_provider_id: str, profile_scope: str) -> list[dict[str, Any]]:
    pid = _db_provider_id(runtime_provider_id)
    rows = db._conn.execute(
        "SELECT * FROM oauth_pool_entries WHERE provider_id=? AND profile_scope=? "
        "ORDER BY priority ASC, updated_at DESC",
        (pid, profile_scope),
    ).fetchall()
    return [dict(r) for r in rows]


def read_pool_entries(
    db: Any,
    runtime_provider_id: str,
    *,
    include_global_fallback: bool = True,
) -> list[dict[str, Any]]:
    scope = current_profile_scope()
    rows = _fetch_pool_rows(db, runtime_provider_id, scope)
    if not rows and include_global_fallback and scope:
        rows = _fetch_pool_rows(db, runtime_provider_id, GLOBAL_PROFILE_SCOPE)
    return [pool_entry_to_credential_dict(r) for r in rows]


def write_pool_entries(
    db: Any,
    runtime_provider_id: str,
    entries: list[dict[str, Any]],
    *,
    profile_scope: str | None = None,
) -> None:
    from agent.oauth.model_tokens import ensure_model_oauth_provider_row

    ensure_model_oauth_provider_row(db, runtime_provider_id)
    scope = current_profile_scope() if profile_scope is None else profile_scope
    pid = _db_provider_id(runtime_provider_id)

    def _write(conn):
        conn.execute(
            "DELETE FROM oauth_pool_entries WHERE provider_id=? AND profile_scope=?",
            (pid, scope),
        )
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                row = credential_dict_to_pool_row(runtime_provider_id, entry, profile_scope=scope)
            except ValueError:
                continue
            conn.execute(
                "INSERT INTO oauth_pool_entries ("
                "id, provider_id, profile_scope, label, source, priority, status, "
                "access_token_encrypted, refresh_token_encrypted, base_url, metadata, "
                "issued_at, updated_at, last_used_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["id"], row["provider_id"], row["profile_scope"], row["label"],
                    row["source"], row["priority"], row["status"],
                    row["access_token_encrypted"], row["refresh_token_encrypted"],
                    row["base_url"], row["metadata"], row["issued_at"],
                    row["updated_at"], row["last_used_at"],
                ),
            )

    db._execute_write(_write)


def delete_pool_entries(db: Any, runtime_provider_id: str, *, profile_scope: str | None = None) -> int:
    pid = _db_provider_id(runtime_provider_id)
    affected = [0]

    def _delete(conn):
        if profile_scope is None:
            cur = conn.execute("DELETE FROM oauth_pool_entries WHERE provider_id=?", (pid,))
        else:
            cur = conn.execute(
                "DELETE FROM oauth_pool_entries WHERE provider_id=? AND profile_scope=?",
                (pid, profile_scope),
            )
        affected[0] = cur.rowcount

    db._execute_write(_delete)
    return affected[0]


def read_all_pool_entries_grouped() -> dict[str, list[dict[str, Any]]]:
    """Return ``{runtime_provider_id: [pool dicts]}`` from ``oauth_pool_entries``."""
    try:
        # (single-user: MembershipStore removed; using stub)
def _noop_store(*a, **kw): return None
MembershipStore = _noop_store
        from agent.oauth.model_tokens import runtime_provider_id

        store = MembershipStore()
        try:
            rows = store._conn.execute(
                "SELECT * FROM oauth_pool_entries ORDER BY provider_id, priority ASC, updated_at DESC"
            ).fetchall()
            out: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                runtime = runtime_provider_id(str(dict(row).get("provider_id") or ""))
                out.setdefault(runtime, []).append(pool_entry_to_credential_dict(dict(row)))
            return out
        finally:
            store.close()
    except Exception:
        return {}


def try_read_pool_entries(runtime_provider_id: str) -> list[dict[str, Any]]:
    try:
        # (single-user: MembershipStore removed; using stub)
def _noop_store(*a, **kw): return None
MembershipStore = _noop_store

        store = MembershipStore()
        try:
            return read_pool_entries(store, runtime_provider_id)
        finally:
            store.close()
    except Exception:
        return []


def try_write_pool_entries(runtime_provider_id: str, entries: list[dict[str, Any]]) -> None:
    try:
        # (single-user: MembershipStore removed; using stub)
def _noop_store(*a, **kw): return None
MembershipStore = _noop_store

        store = MembershipStore()
        try:
            write_pool_entries(store, runtime_provider_id, entries)
        finally:
            store.close()
    except Exception as exc:
        logger.warning(
            "try_write_pool_entries failed for %s: %s",
            runtime_provider_id,
            exc,
            exc_info=True,
        )


def migrate_auth_json_pool_to_db(db: Any, auth: dict[str, Any] | None = None) -> int:
    if auth is None:
        from intellect_cli.auth import _load_auth_store

        auth = _load_auth_store()
    pool = auth.get("credential_pool")
    if not isinstance(pool, dict):
        return 0

    count = 0
    scope = current_profile_scope()
    for runtime_pid, entries in pool.items():
        if not isinstance(entries, list):
            continue
        valid = [
            e for e in entries
            if isinstance(e, dict) and (e.get("access_token") or e.get("runtime_api_key"))
        ]
        if not valid:
            continue
        write_pool_entries(db, str(runtime_pid), valid, profile_scope=scope)
        count += len(valid)
    return count
