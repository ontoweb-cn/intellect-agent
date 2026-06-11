"""OAuth provider management API — CRUD for login OAuth providers.

Uses the unified OAuthEngine (agent/oauth) for provider resolution and
falls back to config.yaml + builtins when the engine is not available.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from api.helpers import bad, j as json_response, read_body
from api.members import _load_config, agent_membership_available, resolve_member_id


def _store():
    """Get a MembershipStore instance."""
    from agent.membership import MembershipStore
    return MembershipStore(config=_load_config())


def _require_admin(handler, parsed) -> str | None:
    """Resolve the actor and require Action.ADMIN. Returns actor id or sends error."""
    from agent.membership import Action, authorize
    actor = resolve_member_id(handler, parsed)
    if not actor:
        bad(handler, "Member login required", status=401)
        return None
    store = _store()
    try:
        row = store.get_member(actor) if actor else None
        role = str(row.get("role") or "member") if row else "member"
        if not authorize(role, Action.ADMIN):
            bad(handler, "Only the profile owner can manage OAuth providers", status=403)
            return None
    finally:
        store.close()
    return actor

logger = logging.getLogger(__name__)


def yaml_oauth_migration_notice(config: dict[str, Any] | None) -> dict[str, Any]:
    """Surface legacy ``members.oauth.providers`` YAML for Settings migration UI."""
    empty = {
        "deprecated": False,
        "yaml_provider_count": 0,
        "migration_marker": False,
        "cli_command": "",
    }
    if not isinstance(config, dict):
        return empty
    try:
        from agent.oauth.migrate_from_config import (  # type: ignore[import-not-found]
            migration_marker_exists,
            yaml_providers_from_config,
        )
    except ImportError:
        return empty

    entries = yaml_providers_from_config(config)
    if not entries:
        oauth = config.get("members", {}).get("oauth", {})
        if isinstance(oauth, dict):
            raw = oauth.get("providers")
            if isinstance(raw, dict):
                entries = []
                for pid, entry in raw.items():
                    if isinstance(entry, dict):
                        row = dict(entry)
                        row.setdefault("id", str(pid))
                        if row.get("id"):
                            entries.append(row)
    if not entries:
        return empty

    migrated = migration_marker_exists()
    cli = "intellect oauth migrate-from-config --write-config"
    if migrated:
        cli = (
            "intellect oauth migrate-from-config --write-config "
            "(or remove members.oauth.providers from config.yaml)"
        )
    return {
        "deprecated": True,
        "yaml_provider_count": len(entries),
        "migration_marker": migrated,
        "cli_command": cli,
    }


# ── Engine helpers ────────────────────────────────────────────────────────────

def _get_engine_with_store():
    """Return (OAuthEngine, store) tuple, or (None, None) if unavailable.
    Caller MUST close the store after use."""
    if not agent_membership_available():
        return None, None
    try:
        from agent.oauth import OAuthEngine  # type: ignore[import-not-found]
        config = _load_config()
        store = _store()
        return OAuthEngine(config=config, db=store), store
    except Exception:
        return None, None


# ── Provider listing ─────────────────────────────────────────────────────────

def _catalog_credential_fields(provider_id: str) -> list[dict[str, Any]]:
    try:
        from agent.oauth.builtin_catalog import credential_fields_for  # type: ignore[import-not-found]

        return credential_fields_for(provider_id)
    except Exception:
        return []


def _provider_to_public_dict(p, *, has_token: bool = False) -> dict:
    """Convert an OAuthProviderConfig to the public API dict shape."""
    tenant = p.tenant_config if isinstance(getattr(p, "tenant_config", None), dict) else {}
    cred_fields = _catalog_credential_fields(p.id)
    return {
        "id": p.id,
        "name": p.name,
        "usage": p.usage,
        "auth_flow": p.auth_flow,
        "enabled": p.enabled,
        "is_builtin": p.is_builtin,
        "logo_svg": p.logo_svg,
        "logo_type": p.logo_type,
        "client_id": p.client_id[:8] + "…" if p.client_id else "",
        "has_client_id": bool(p.client_id),
        "has_client_secret": bool(getattr(p, "client_secret_encrypted", "") or ""),
        "has_token": has_token,
        "scopes": p.scopes,
        "authorize_url": p.authorize_url,
        "token_url": p.token_url,
        "userinfo_url": p.userinfo_url,
        "device_code_url": p.device_code_url,
        "oidc_discovery_url": p.oidc_discovery_url,
        "token_storage": p.token_storage,
        "display_order": p.display_order,
        "description": p.description,
        "claim_sub": p.claim_sub,
        "claim_email": p.claim_email,
        "claim_name": p.claim_name,
        "tenant_config": dict(tenant),
        "credential_fields": cred_fields,
    }


def _oauth_providers_payload(providers: list[dict[str, Any]]) -> dict[str, Any]:
    config = _load_config()
    return {
        "providers": providers,
        "yaml_migration": yaml_oauth_migration_notice(config),
    }


def _list_oauth_providers(handler) -> None:
    """GET /api/oauth/providers — list all login OAuth providers."""
    engine, store = _get_engine_with_store()

    if engine:
        try:
            providers = engine.list_providers(usage="", enabled_only=False)
            result = [_provider_to_public_dict(p) for p in providers]
            if store:
                try:
                    from agent.oauth.model_tokens import enrich_providers_with_token_status  # type: ignore[import-not-found]

                    enrich_providers_with_token_status(result, store)
                except Exception:
                    logger.debug('non-critical operation failed', exc_info=True)
            json_response(handler, _oauth_providers_payload(result))
            return
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)
        finally:
            if store:
                store.close()

    # Fallback: use old members_oauth API
    try:
        from agent.members_oauth import list_enabled_providers
        config = _load_config()
        providers = list_enabled_providers(config)
        result = [{"id": p.get("id", ""), "name": p.get("display_name", ""),
                    "enabled": True, "is_builtin": True} for p in providers]
        json_response(handler, _oauth_providers_payload(result))
    except Exception:
        json_response(handler, _oauth_providers_payload([]))


# ── Provider CRUD ────────────────────────────────────────────────────────────

def _persist_client_secret(raw: str) -> str:
    """Encrypt client secret for oauth_providers.client_secret_encrypted."""
    secret = str(raw or "").strip()
    if not secret:
        return ""
    try:
        from agent.oauth.storage import encrypt_token

        return encrypt_token(secret)
    except Exception:
        logger.exception("Failed to encrypt OAuth client secret")
        return secret


def _add_oauth_provider(handler, parsed, body: dict[str, Any]) -> None:
    """POST /api/oauth/providers — add a custom OAuth provider."""
    if not _require_admin(handler, parsed):
        return
    provider_id = str(body.get("id") or "").strip()
    if not provider_id:
        bad(handler, "Provider id is required")
        return

    store = _store()
    try:
        # Check for duplicates
        existing = store._conn.execute(
            "SELECT id FROM oauth_providers WHERE id = ?", (provider_id,)
        ).fetchone()
        if existing:
            bad(handler, f"Provider {provider_id} already exists", status=409)
            return

        name = str(body.get("name") or provider_id).strip()
        auth_flow = str(body.get("auth_flow") or "pkce_loopback").strip()
        client_id = str(body.get("client_id") or "").strip()
        client_secret = str(body.get("client_secret") or "").strip()
        scopes = json.dumps(body.get("scopes") or ["openid", "profile", "email"])
        authorize_url = str(body.get("authorize_url") or "").strip()
        token_url = str(body.get("token_url") or "").strip()
        userinfo_url = str(body.get("userinfo_url") or "").strip()
        oidc_discovery_url = str(body.get("oidc_discovery_url") or "").strip()
        enabled = 1 if body.get("enabled", True) else 0
        now = time.time()

        store._conn.execute(
            """INSERT INTO oauth_providers (id, name, usage, auth_flow, enabled,
               client_id, client_secret_encrypted, authorize_url, token_url,
               userinfo_url, scopes, oidc_discovery_url, is_builtin,
               created_at, updated_at)
               VALUES (?, ?, 'login', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (provider_id, name, auth_flow, enabled, client_id, _persist_client_secret(client_secret),
             authorize_url, token_url, userinfo_url, scopes, oidc_discovery_url,
             now, now),
        )
        store._conn.commit()
        json_response(handler, {"ok": True, "provider_id": provider_id})
    except Exception as exc:
        logger.exception("Failed to add OAuth provider")
        bad(handler, str(exc))
    finally:
        store.close()


def _update_oauth_provider(handler, parsed, provider_id: str, body: dict[str, Any]) -> None:
    """PUT /api/oauth/providers/{id} — update an OAuth provider."""
    if not _require_admin(handler, parsed):
        return
    store = _store()
    try:
        row = store._conn.execute(
            "SELECT * FROM oauth_providers WHERE id = ?", (provider_id,)
        ).fetchone()
        if not row:
            bad(handler, f"Provider {provider_id} not found", status=404)
            return

        updates = {}
        for field in ("name", "client_id", "client_secret_encrypted", "authorize_url",
                       "token_url", "userinfo_url", "oidc_discovery_url", "auth_flow"):
            if field in body:
                updates[field] = str(body[field] or "").strip()
        # Accept "client_secret" as an alias for "client_secret_encrypted"
        if "client_secret" in body and "client_secret_encrypted" not in updates:
            secret_raw = str(body["client_secret"] or "").strip()
            if secret_raw:
                updates["client_secret_encrypted"] = _persist_client_secret(secret_raw)

        if "scopes" in body:
            updates["scopes"] = json.dumps(body["scopes"])
        if "enabled" in body:
            updates["enabled"] = 1 if body["enabled"] else 0
        if "display_order" in body:
            updates["display_order"] = int(body["display_order"])

        if "tenant_config" in body and isinstance(body["tenant_config"], dict):
            existing_raw = row["tenant_config"] if row["tenant_config"] else "{}"
            try:
                existing = (
                    json.loads(existing_raw)
                    if isinstance(existing_raw, str)
                    else dict(existing_raw or {})
                )
            except json.JSONDecodeError:
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            merged = dict(existing)
            for key, value in body["tenant_config"].items():
                if value is None:
                    merged.pop(str(key), None)
                else:
                    merged[str(key)] = str(value).strip() if value != "" else ""
            updates["tenant_config"] = json.dumps(merged)

        now = time.time()
        if updates:
            updates["updated_at"] = now
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [provider_id]
            store._conn.execute(
                f"UPDATE oauth_providers SET {set_clause} WHERE id = ?", values
            )
            store._conn.commit()

        json_response(handler, {"ok": True, "provider_id": provider_id})
    except Exception as exc:
        logger.exception("Failed to update OAuth provider")
        bad(handler, str(exc))
    finally:
        store.close()


def _delete_oauth_provider(handler, parsed, provider_id: str) -> None:
    """DELETE /api/oauth/providers/{id} — delete a custom OAuth provider."""
    if not _require_admin(handler, parsed):
        return
    store = _store()
    try:
        row = store._conn.execute(
            "SELECT is_builtin FROM oauth_providers WHERE id = ?", (provider_id,)
        ).fetchone()
        if not row:
            bad(handler, f"Provider {provider_id} not found", status=404)
            return
        if row["is_builtin"]:
            bad(handler, "Cannot delete a built-in provider", status=403)
            return

        store._conn.execute("DELETE FROM oauth_providers WHERE id = ?", (provider_id,))
        store._conn.commit()
        json_response(handler, {"ok": True})
    except Exception as exc:
        logger.exception("Failed to delete OAuth provider")
        bad(handler, str(exc))
    finally:
        store.close()


# ── Logo upload ──────────────────────────────────────────────────────────────

def _upload_logo(handler, parsed, provider_id: str, body: dict[str, Any]) -> None:
    """PUT /api/oauth/providers/{id}/logo — upload a logo for a provider."""
    if not _require_admin(handler, parsed):
        return
    logo_svg = str(body.get("logo_svg") or "").strip()
    logo_data = str(body.get("logo_data") or "").strip()  # base64 PNG
    if not logo_svg and not logo_data:
        bad(handler, "logo_svg or logo_data is required")
        return
    store = _store()
    try:
        row = store._conn.execute(
            "SELECT id FROM oauth_providers WHERE id = ?", (provider_id,)
        ).fetchone()
        if not row:
            bad(handler, f"Provider {provider_id} not found", status=404)
            return
        if logo_svg:
            store._conn.execute(
                "UPDATE oauth_providers SET logo_svg = ?, logo_type = 'svg', updated_at = ? WHERE id = ?",
                (logo_svg, time.time(), provider_id),
            )
        elif logo_data:
            store._conn.execute(
                "UPDATE oauth_providers SET logo_svg = ?, logo_type = 'png', updated_at = ? WHERE id = ?",
                (logo_data, time.time(), provider_id),
            )
        store._conn.commit()
        json_response(handler, {"ok": True, "provider_id": provider_id})
    except Exception as exc:
        logger.exception("Failed to upload logo")
        bad(handler, str(exc))
    finally:
        store.close()


# ── Route handlers for api/routes.py ──────────────────────────────────────────

def handle_get(handler, parsed) -> bool:
    """Handle GET /api/oauth/providers"""
    if parsed.path == "/api/oauth/providers":
        _list_oauth_providers(handler)
        return True
    return False


def handle_post(handler, parsed, body) -> bool:
    """Handle POST /api/oauth/providers"""
    if parsed.path == "/api/oauth/providers":
        _add_oauth_provider(handler, parsed, body)
        return True
    return False


def handle_put(handler, parsed, body) -> bool:
    """Handle PUT /api/oauth/providers/{id} and /api/oauth/providers/{id}/logo"""
    path = parsed.path
    if path.startswith("/api/oauth/providers/"):
        rest = path[len("/api/oauth/providers/"):].strip("/")
        if rest.endswith("/logo"):
            provider_id = rest[:-len("/logo")].strip("/")
            if provider_id:
                _upload_logo(handler, parsed, provider_id, body)
                return True
        elif rest:
            _update_oauth_provider(handler, parsed, rest, body)
            return True
    return False


def handle_delete(handler, parsed) -> bool:
    """Handle DELETE /api/oauth/providers/{id}"""
    path = parsed.path
    if path.startswith("/api/oauth/providers/"):
        provider_id = path[len("/api/oauth/providers/"):].strip("/")
        if provider_id:
            _delete_oauth_provider(handler, parsed, provider_id)
            return True
    return False
