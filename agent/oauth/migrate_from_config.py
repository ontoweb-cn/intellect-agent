"""Migrate legacy ``members.oauth.providers`` from config.yaml into state.db."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MIGRATION_MARKER_NAME = ".oauth_yaml_providers_migrated"


def migration_marker_path() -> Path:
    from intellect_constants import get_intellect_home

    return get_intellect_home() / MIGRATION_MARKER_NAME


def migration_marker_exists() -> bool:
    return migration_marker_path().exists()


def write_migration_marker(provider_count: int) -> None:
    path = migration_marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{int(time.time())}\n{provider_count}\n", encoding="utf-8")


def yaml_providers_from_config(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(config, dict):
        return []
    oauth = config.get("members", {}).get("oauth", {})
    if not isinstance(oauth, dict):
        return []
    providers = oauth.get("providers", [])
    if not isinstance(providers, list):
        return []
    return [p for p in providers if isinstance(p, dict) and p.get("id")]


def _secret_from_yaml(entry: dict[str, Any]) -> str:
    for key in ("client_secret", "app_secret", "secret"):
        val = entry.get(key)
        if val:
            return str(val)
    return ""


def _tenant_from_yaml(entry: dict[str, Any]) -> dict[str, Any]:
    tenant: dict[str, Any] = {}
    if isinstance(entry.get("tenant_config"), dict):
        tenant.update(entry["tenant_config"])
    for key in (
        "corp_id",
        "agent_id",
        "tenant",
        "base_url",
        "domain",
        "issuer",
    ):
        if entry.get(key) not in (None, ""):
            tenant[key] = entry[key]
    return tenant


def yaml_entry_to_provider_row(entry: dict[str, Any]) -> dict[str, Any]:
    """Map a config.yaml provider entry to oauth_providers column values."""
    from agent.oauth._stubs import OAUTH_PROVIDER_PRESETS
    from agent.oauth import OAuthEngine
    from agent.oauth.provider_resolution import normalize_provider_id

    pid = normalize_provider_id(str(entry.get("id") or "").strip())
    preset = OAUTH_PROVIDER_PRESETS.get(pid, {})
    cfg = OAuthEngine._yaml_to_config({**preset, **entry, "id": pid})

    tenant = _tenant_from_yaml(entry)
    if tenant:
        merged_tenant = dict(cfg.tenant_config or {})
        merged_tenant.update(tenant)
        cfg.tenant_config = merged_tenant

    secret_plain = _secret_from_yaml(entry)
    secret_enc = ""
    if secret_plain:
        try:
            from agent.oauth.storage import encrypt_token

            secret_enc = encrypt_token(secret_plain)
        except Exception as exc:
            logger.warning("encrypt client_secret for %s failed: %s", pid, exc)

    scopes = cfg.scopes or []
    enabled = 1 if entry.get("enabled", True) else 0

    return {
        "id": pid,
        "name": cfg.name,
        "usage": cfg.usage,
        "auth_flow": cfg.auth_flow,
        "enabled": enabled,
        "client_id": (entry.get("client_id") or entry.get("app_key") or cfg.client_id or ""),
        "client_secret_encrypted": secret_enc,
        "authorize_url": cfg.authorize_url,
        "token_url": cfg.token_url,
        "userinfo_url": cfg.userinfo_url,
        "device_code_url": cfg.device_code_url,
        "revoke_url": cfg.revoke_url,
        "scopes": json.dumps(scopes),
        "pkce": 1 if cfg.pkce else 0,
        "tenant_specific": 1 if cfg.tenant_config else 0,
        "tenant_config": json.dumps(cfg.tenant_config or {}),
        "claim_sub": cfg.claim_sub,
        "claim_email": cfg.claim_email,
        "claim_name": cfg.claim_name,
        "oidc_discovery_url": cfg.oidc_discovery_url,
        "token_storage": cfg.token_storage,
        "display_order": cfg.display_order,
        "description": cfg.description or "",
        "secret_plain_present": bool(secret_plain),
    }


def _existing_row(cursor, provider_id: str) -> dict[str, Any] | None:
    row = cursor.execute(
        "SELECT * FROM oauth_providers WHERE id=?",
        (provider_id,),
    ).fetchone()
    if not row:
        return None
    return dict(row)


def migrate_yaml_providers_to_db(
    config: dict[str, Any],
    db: Any,
    *,
    dry_run: bool = False,
    force_secrets: bool = False,
    force_client_id: bool = False,
) -> dict[str, Any]:
    """Upsert YAML ``members.oauth.providers`` entries into ``oauth_providers``."""
    entries = yaml_providers_from_config(config)
    stats: dict[str, Any] = {
        "yaml_count": len(entries),
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        "providers": [],
    }
    if not entries:
        return stats

    now = time.time()

    def _run(cursor):
        for entry in entries:
            try:
                row = yaml_entry_to_provider_row(entry)
            except Exception as exc:
                stats["errors"].append({"id": entry.get("id"), "error": str(exc)})
                continue

            secret_plain_present = row.pop("secret_plain_present", False)
            pid = row["id"]
            existing = _existing_row(cursor, pid)

            if existing is None:
                stats["providers"].append({"id": pid, "action": "insert"})
                if dry_run:
                    stats["inserted"] += 1
                    continue
                cursor.execute(
                    "INSERT INTO oauth_providers ("
                    "id, name, usage, auth_flow, enabled, "
                    "logo_svg, logo_path, logo_type, "
                    "client_id, client_secret_encrypted, "
                    "authorize_url, token_url, userinfo_url, "
                    "device_code_url, revoke_url, "
                    "scopes, pkce, tenant_specific, tenant_config, "
                    "claim_sub, claim_email, claim_name, "
                    "oidc_discovery_url, token_storage, "
                    "platform_bindable, platform_bind_ttl, "
                    "display_order, is_builtin, description, "
                    "created_at, updated_at"
                    ") VALUES ("
                    "?,?,?,?,?,"
                    "'','','svg',"
                    "?,?,?,?,?,?,"
                    "?,?,?,?,?,?,?,?,"
                    "0,3600,?,?,0,?,?,?"
                    ")",
                    (
                        row["id"],
                        row["name"],
                        row["usage"],
                        row["auth_flow"],
                        row["enabled"],
                        row["client_id"],
                        row["client_secret_encrypted"],
                        row["authorize_url"],
                        row["token_url"],
                        row["userinfo_url"],
                        row["device_code_url"],
                        row["revoke_url"],
                        row["scopes"],
                        row["pkce"],
                        row["tenant_specific"],
                        row["tenant_config"],
                        row["claim_sub"],
                        row["claim_email"],
                        row["claim_name"],
                        row["oidc_discovery_url"],
                        row["token_storage"],
                        row["display_order"],
                        row["description"],
                        now,
                        now,
                    ),
                )
                stats["inserted"] += 1
                continue

            updates: dict[str, Any] = {}
            if "enabled" in entry:
                updates["enabled"] = row["enabled"]

            yaml_client_id = row["client_id"]
            if yaml_client_id and (force_client_id or not existing.get("client_id")):
                updates["client_id"] = yaml_client_id

            yaml_secret = row["client_secret_encrypted"]
            if yaml_secret and secret_plain_present:
                if force_secrets or not existing.get("client_secret_encrypted"):
                    updates["client_secret_encrypted"] = yaml_secret

            for field in (
                "authorize_url",
                "token_url",
                "userinfo_url",
                "oidc_discovery_url",
                "scopes",
                "pkce",
                "tenant_specific",
                "tenant_config",
                "claim_sub",
                "claim_email",
                "claim_name",
            ):
                yaml_val = row.get(field)
                db_val = existing.get(field)
                if yaml_val in (None, "", "[]", "{}"):
                    continue
                if not force_client_id and db_val not in (None, "", "[]", "{}"):
                    if field == "scopes":
                        try:
                            if json.loads(str(db_val)):
                                continue
                        except json.JSONDecodeError:
                            pass
                    elif field == "tenant_config":
                        try:
                            if json.loads(str(db_val)):
                                continue
                        except json.JSONDecodeError:
                            pass
                    elif str(db_val).strip():
                        continue
                updates[field] = yaml_val

            if not updates:
                stats["skipped"] += 1
                stats["providers"].append({"id": pid, "action": "skipped"})
                continue

            stats["providers"].append({"id": pid, "action": "update", "fields": sorted(updates)})
            if dry_run:
                stats["updated"] += 1
                continue

            set_clause = ", ".join(f"{k}=?" for k in updates)
            values = list(updates.values()) + [now, pid]
            cursor.execute(
                f"UPDATE oauth_providers SET {set_clause}, updated_at=? WHERE id=?",
                values,
            )
            stats["updated"] += 1

    db._execute_write(_run)

    if not dry_run and entries and not stats["errors"]:
        write_migration_marker(len(entries))

    return stats


def clear_yaml_providers_in_config(config: dict[str, Any]) -> bool:
    """Remove ``members.oauth.providers`` list (replace with empty list)."""
    members = config.get("members")
    if not isinstance(members, dict):
        return False
    oauth = members.get("oauth")
    if not isinstance(oauth, dict):
        return False
    if "providers" not in oauth:
        return False
    oauth["providers"] = []
    return True
