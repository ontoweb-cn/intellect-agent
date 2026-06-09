"""Model OAuth tokens — ``oauth_tokens`` table as primary store (PR-A4).

Runtime provider ids (``openai-codex``, ``xai-oauth``, …) map to ``oauth_providers``
row ids (``openai_codex``, ``xai``, …). Legacy ``auth.json`` / ``credential_pool``
remain readable during transition; new writes go to the DB first.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Runtime providers that store tokens in oauth_tokens but may predate catalog seed.
_MODEL_OAUTH_PROVIDER_SEEDS: dict[str, tuple[str, str]] = {
    "anthropic": ("Anthropic (Claude)", "pkce_loopback"),
    "claude": ("Claude Code", "external"),
}

# Runtime CLI / WebUI id → oauth_providers.id
RUNTIME_TO_DB_PROVIDER: dict[str, str] = {
    "openai-codex": "openai_codex",
    "xai-oauth": "xai",
    "google-gemini-cli": "gemini",
    "gemini-cli": "gemini",
    "qwen-oauth": "qwen",
    "minimax-oauth": "minimax_oauth",
    "minimax": "minimax_oauth",
    "ontoweb": "ontoweb",
    "copilot": "copilot",
    "anthropic": "anthropic",
    "claude-code": "claude",
    "claude": "claude",
    "spotify": "spotify",
    "zai": "zai",
}


def db_provider_id(provider_id: str) -> str:
    """Canonical ``oauth_providers`` / ``oauth_tokens`` provider id."""
    pid = (provider_id or "").strip().lower()
    if pid in RUNTIME_TO_DB_PROVIDER:
        return RUNTIME_TO_DB_PROVIDER[pid]
    return pid.replace("-", "_")


def runtime_provider_id(db_provider_id_value: str) -> str:
    """Best-effort runtime id for auth.json / credential_pool keys."""
    db_pid = (db_provider_id_value or "").strip().lower()
    for runtime, db_id in RUNTIME_TO_DB_PROVIDER.items():
        if db_id == db_pid:
            return runtime
    return db_pid.replace("_", "-")


def provider_has_db_token(
    db: Any,
    provider_id: str,
    *,
    member_id: str | None = None,
) -> bool:
    from agent.oauth.storage import get_oauth_token

    tok = get_oauth_token(db_provider_id(provider_id), db, member_id=member_id)
    return bool(tok and tok.get("access_token"))


def ensure_model_oauth_provider_row(db: Any, provider_id: str) -> None:
    """Insert a minimal ``oauth_providers`` row when missing (FK for model tokens)."""
    pid = db_provider_id(provider_id)
    name, auth_flow = _MODEL_OAUTH_PROVIDER_SEEDS.get(
        pid,
        (runtime_provider_id(pid).replace("-", " ").title(), "pkce_loopback"),
    )
    now = time.time()

    def _ensure(conn):
        conn.execute(
            "INSERT OR IGNORE INTO oauth_providers ("
            "id, name, usage, auth_flow, enabled, logo_svg, logo_path, logo_type, "
            "client_id, client_secret_encrypted, authorize_url, token_url, userinfo_url, "
            "device_code_url, revoke_url, scopes, pkce, tenant_specific, tenant_config, "
            "claim_sub, claim_email, claim_name, oidc_discovery_url, "
            "token_storage, platform_bindable, platform_bind_ttl, "
            "display_order, is_builtin, description, created_at, updated_at"
            ") VALUES (?, ?, 'model', ?, 0, '', '', 'svg', '', '', '', '', '', '', '', "
            "'[]', 1, 0, '{}', 'sub', 'email', 'name', '', 'credential_pool', 0, 3600, "
            "90, 1, ?, ?, ?)",
            (pid, name, auth_flow, f"Model OAuth provider ({pid})", now, now),
        )

    db._execute_write(_ensure)


def persist_model_token(
    db: Any,
    provider_id: str,
    *,
    access_token: str,
    refresh_token: str | None = None,
    expires_in: int = 0,
    scope: str = "",
    member_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Store encrypted model OAuth token in ``oauth_tokens``."""
    from agent.oauth.storage import store_oauth_token

    token = (access_token or "").strip()
    if not token:
        raise ValueError("access_token is required")
    ensure_model_oauth_provider_row(db, provider_id)
    return store_oauth_token(
        db_provider_id(provider_id),
        token,
        db,
        member_id=member_id,
        refresh_token=refresh_token,
        expires_in=expires_in,
        scope=scope,
        metadata=metadata,
    )


def try_load_model_token_row(provider_id: str) -> dict[str, Any] | None:
    """Load decrypted ``oauth_tokens`` row for a runtime provider id."""
    try:
        # (single-user: MembershipStore removed; using stub)
def _noop_store(*a, **kw): return None
MembershipStore = _noop_store
        from agent.oauth.storage import get_oauth_token

        store = MembershipStore()
        try:
            return get_oauth_token(db_provider_id(provider_id), store, member_id=None)
        finally:
            store.close()
    except Exception as exc:
        logger.debug("load model token row failed for %s: %s", provider_id, exc)
        return None


def try_persist_model_token(
    provider_id: str,
    *,
    access_token: str,
    refresh_token: str | None = None,
    expires_in: int = 0,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Best-effort persist to ``oauth_tokens`` (opens its own store)."""
    try:
        # (single-user: MembershipStore removed; using stub)
def _noop_store(*a, **kw): return None
MembershipStore = _noop_store

        store = MembershipStore()
        try:
            ensure_model_oauth_provider_row(store, provider_id)
            persist_model_token(
                store,
                provider_id,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=expires_in,
                metadata=metadata,
            )
        finally:
            store.close()
    except Exception as exc:
        logger.debug("persist model token failed for %s: %s", provider_id, exc)


def try_delete_model_token(provider_id: str) -> bool:
    """Best-effort delete of model OAuth token row(s)."""
    try:
        # (single-user: MembershipStore removed; using stub)
def _noop_store(*a, **kw): return None
MembershipStore = _noop_store

        store = MembershipStore()
        try:
            return delete_model_token(store, provider_id)
        finally:
            store.close()
    except Exception as exc:
        logger.debug("delete model token failed for %s: %s", provider_id, exc)
        return False


def delete_model_token(
    db: Any,
    provider_id: str,
    *,
    member_id: str | None = None,
) -> bool:
    """Remove model OAuth token row(s) for a provider."""
    pid = db_provider_id(provider_id)
    affected = [0]

    def _delete(conn):
        cur = conn.cursor()
        if member_id:
            cur.execute(
                "DELETE FROM oauth_tokens WHERE provider_id=? AND member_id=?",
                (pid, member_id),
            )
        else:
            cur.execute(
                "DELETE FROM oauth_tokens WHERE provider_id=? AND member_id IS NULL",
                (pid,),
            )
        affected[0] = cur.rowcount

    db._execute_write(_delete)
    return affected[0] > 0


def model_token_auth_status(
    db: Any,
    provider_id: str,
    *,
    member_id: str | None = None,
) -> dict[str, Any] | None:
    """Return a ``get_auth_status``-shaped dict when a DB token exists."""
    from agent.oauth.storage import get_oauth_token

    tok = get_oauth_token(db_provider_id(provider_id), db, member_id=member_id)
    if not tok or not tok.get("access_token"):
        return None
    runtime = runtime_provider_id(db_provider_id(provider_id))
    return {
        "logged_in": True,
        "provider": runtime,
        "key_source": "oauth_db",
        "source": "oauth_tokens",
        "auth_store": "state.db",
        "has_refresh_token": bool(tok.get("refresh_token")),
    }


def enrich_providers_with_token_status(
    providers: list[dict[str, Any]],
    db: Any,
    *,
    member_id: str | None = None,
) -> None:
    """Set ``has_token`` on public provider dicts (model/server usage)."""
    for row in providers:
        usage = row.get("usage") or ""
        if usage not in ("model", "server", "both"):
            row.setdefault("has_token", False)
            continue
        row["has_token"] = provider_has_db_token(
            db, str(row.get("id") or ""), member_id=member_id
        )


def oauth_token_row_to_pool_entry(
    provider_id: str,
    token_row: dict[str, Any],
    *,
    label: str | None = None,
) -> dict[str, Any]:
    """Build a ``credential_pool``-shaped dict from an ``oauth_tokens`` row."""
    runtime = runtime_provider_id(db_provider_id(provider_id))
    access = str(token_row.get("access_token") or "").strip()
    refresh = str(token_row.get("refresh_token") or "").strip()
    entry: dict[str, Any] = {
        "id": f"db-{token_row.get('id', 'oauth')}",
        "label": label or f"{runtime} (state.db)",
        "auth_type": "oauth",
        "priority": 0,
        "source": "oauth_tokens",
        "access_token": access,
        "refresh_token": refresh or None,
    }
    expires_at = token_row.get("expires_at")
    if expires_at:
        entry["expires_at_ms"] = int(float(expires_at) * 1000)
    if runtime == "openai-codex":
        entry["base_url"] = "https://chatgpt.com/backend-api/codex"
        entry["auth_mode"] = "chatgpt"
    return entry


def load_credential_pool_entries_from_db(provider_id: str) -> list[dict[str, Any]]:
    """Return pool-shaped entries from ``oauth_tokens``, or []."""
    try:
        # (single-user: MembershipStore removed; using stub)
def _noop_store(*a, **kw): return None
MembershipStore = _noop_store
        from agent.oauth.storage import get_oauth_token

        store = MembershipStore()
        try:
            row = get_oauth_token(db_provider_id(provider_id), store, member_id=None)
            if not row or not row.get("access_token"):
                return []
            return [oauth_token_row_to_pool_entry(provider_id, row)]
        finally:
            store.close()
    except Exception as exc:
        logger.debug("load pool from db failed for %s: %s", provider_id, exc)
        return []


def resolve_runtime_access_token(provider_id: str) -> str | None:
    """Return decrypted access token from DB for a runtime provider id."""
    try:
        # (single-user: MembershipStore removed; using stub)
def _noop_store(*a, **kw): return None
MembershipStore = _noop_store
        from agent.oauth.storage import get_oauth_token

        store = MembershipStore()
        try:
            row = get_oauth_token(db_provider_id(provider_id), store, member_id=None)
            if row and row.get("access_token"):
                return str(row["access_token"])
        finally:
            store.close()
    except Exception:
        return None
    return None


def try_model_token_auth_status(provider_id: str) -> dict[str, Any] | None:
    """Open a store and return DB token status, or None if unavailable."""
    try:
        # (single-user: MembershipStore removed; using stub)
def _noop_store(*a, **kw): return None
MembershipStore = _noop_store

        store = MembershipStore()
        try:
            return model_token_auth_status(store, provider_id)
        finally:
            store.close()
    except Exception as exc:
        logger.debug("model token status check failed for %s: %s", provider_id, exc)
        return None


# ---------------------------------------------------------------------------
# extra_metadata: provider-level runtime cache in oauth_providers table
# ---------------------------------------------------------------------------

def read_provider_extra_metadata(provider_id: str) -> dict[str, Any]:
    """Read extra_metadata JSON from ``oauth_providers`` table."""
    try:
        # (single-user: MembershipStore removed; using stub)
def _noop_store(*a, **kw): return None
MembershipStore = _noop_store

        store = MembershipStore()
        try:
            pid = db_provider_id(provider_id)
            row = store._conn.execute(
                "SELECT extra_metadata FROM oauth_providers WHERE id=?",
                (pid,),
            ).fetchone()
            if row and row["extra_metadata"]:
                try:
                    return json.loads(row["extra_metadata"])
                except (json.JSONDecodeError, TypeError):
                    pass
        finally:
            store.close()
    except Exception:
        pass
    return {}


def write_provider_extra_metadata(provider_id: str, data: dict[str, Any]) -> None:
    """Write extra_metadata JSON to ``oauth_providers`` table."""
    try:
        # (single-user: MembershipStore removed; using stub)
def _noop_store(*a, **kw): return None
MembershipStore = _noop_store

        store = MembershipStore()
        try:
            pid = db_provider_id(provider_id)
            store._execute_write(lambda cur: cur.execute(
                "UPDATE oauth_providers SET extra_metadata=?, updated_at=? WHERE id=?",
                (json.dumps(data), time.time(), pid),
            ))
        finally:
            store.close()
    except Exception as exc:
        logger.debug("write extra_metadata for %s failed: %s", provider_id, exc)
