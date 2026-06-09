"""Built-in OAuth provider catalog — load JSON + seed ``oauth_providers``."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSION = 1
CATALOG_MARKER_NAME = ".oauth_builtin_catalog_seeded"


def catalog_dir() -> Path:
    return Path(__file__).resolve().parent / "catalog"


def catalog_path() -> Path:
    return catalog_dir() / "builtin_providers.json"


_CATALOG_FIELDS_CACHE: dict[str, list[dict[str, Any]]] | None = None


def credential_fields_index(*, path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Map provider id (and aliases) → ``credential_fields`` from the builtin catalog."""
    global _CATALOG_FIELDS_CACHE
    if _CATALOG_FIELDS_CACHE is not None:
        return _CATALOG_FIELDS_CACHE

    data = load_builtin_catalog(path=path)
    index: dict[str, list[dict[str, Any]]] = {}
    for record in data.get("providers") or []:
        if not isinstance(record, dict):
            continue
        pid = str(record.get("id") or "").strip()
        if not pid:
            continue
        raw = record.get("credential_fields")
        fields = list(raw) if isinstance(raw, list) else []
        index[pid] = fields
        for alias in record.get("aliases") or []:
            aid = str(alias).strip()
            if aid:
                index[aid] = fields
    _CATALOG_FIELDS_CACHE = index
    return index


def credential_fields_for(provider_id: str, *, path: Path | None = None) -> list[dict[str, Any]]:
    """Return catalog ``credential_fields`` for *provider_id* (empty when unknown)."""
    pid = str(provider_id or "").strip()
    if not pid:
        return []
    return list(credential_fields_index(path=path).get(pid) or [])


def load_builtin_catalog(*, path: Path | None = None) -> dict[str, Any]:
    """Load and validate ``builtin_providers.json``."""
    catalog_file = path or catalog_path()
    raw = json.loads(catalog_file.read_text(encoding="utf-8"))
    version = int(raw.get("schema_version", 0))
    if version != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported oauth catalog schema_version={version} "
            f"(expected {SUPPORTED_SCHEMA_VERSION})"
        )
    providers = raw.get("providers")
    if not isinstance(providers, list) or not providers:
        raise ValueError("oauth catalog must contain a non-empty providers array")
    for entry in providers:
        if not isinstance(entry, dict) or not entry.get("id"):
            raise ValueError("each catalog provider must be an object with id")
    return raw


def _read_icon_svg(icon: dict[str, Any], base: Path) -> tuple[str, str, str]:
    """Return (logo_svg, logo_path, logo_type)."""
    icon_type = str(icon.get("type") or "svg").strip().lower()
    inline = str(icon.get("inline_svg") or "").strip()
    if inline:
        return inline, "", "svg"

    rel = str(icon.get("path") or "").strip()
    if not rel:
        return "", "", icon_type or "svg"

    full = base / rel
    if not full.is_file():
        logger.warning("oauth catalog icon missing: %s", full)
        return "", rel, icon_type

    if icon_type == "svg" or full.suffix.lower() == ".svg":
        try:
            return full.read_text(encoding="utf-8").strip(), rel, "svg"
        except OSError as exc:
            logger.warning("oauth catalog icon read failed %s: %s", full, exc)
            return "", rel, "svg"

    return "", rel, icon_type if icon_type in ("png", "path") else "png"


def _row_from_record(record: dict[str, Any], *, now: float, base: Path) -> tuple[Any, ...]:
    endpoints = record.get("endpoints") or {}
    icon = record.get("icon") or {}
    logo_svg, logo_path, logo_type = _read_icon_svg(icon, base)

    tenant_defaults = record.get("tenant_config_defaults") or {}
    if not isinstance(tenant_defaults, dict):
        tenant_defaults = {}

    tenant_specific = bool(record.get("tenant_specific"))
    if not tenant_specific and tenant_defaults:
        tenant_specific = True

    scopes = record.get("scopes") or []
    if not isinstance(scopes, list):
        scopes = []

    enabled = 1 if record.get("enabled_default") else 0
    pkce = 1 if record.get("pkce", True) else 0

    return (
        record["id"],
        record.get("name") or record["id"],
        record.get("usage") or "login",
        record.get("auth_flow") or "pkce_loopback",
        enabled,
        logo_svg,
        logo_path,
        logo_type,
        "",
        "",
        str(endpoints.get("authorize_url") or ""),
        str(endpoints.get("token_url") or ""),
        str(endpoints.get("userinfo_url") or ""),
        str(endpoints.get("device_code_url") or ""),
        str(endpoints.get("revoke_url") or ""),
        json.dumps(scopes),
        pkce,
        1 if tenant_specific else 0,
        json.dumps(tenant_defaults),
        str(record.get("claim_sub") or "sub"),
        str(record.get("claim_email") or "email"),
        str(record.get("claim_name") or "name"),
        str(endpoints.get("oidc_discovery_url") or ""),
        str(record.get("token_storage") or "identities"),
        1 if record.get("platform_bindable") else 0,
        int(record.get("platform_bind_ttl") or 3600),
        int(record.get("display_order") or 0),
        1 if record.get("is_builtin", True) else 0,
        str(record.get("description") or ""),
        now,
        now,
    )


_INSERT_SQL = """
INSERT OR IGNORE INTO oauth_providers (
    id, name, usage, auth_flow, enabled,
    logo_svg, logo_path, logo_type,
    client_id, client_secret_encrypted,
    authorize_url, token_url, userinfo_url,
    device_code_url, revoke_url,
    scopes, pkce, tenant_specific, tenant_config,
    claim_sub, claim_email, claim_name,
    oidc_discovery_url,
    token_storage, platform_bindable, platform_bind_ttl,
    display_order, is_builtin, description,
    created_at, updated_at
) VALUES (
    ?, ?, ?, ?, ?,
    ?, ?, ?,
    ?, ?,
    ?, ?, ?,
    ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?,
    ?,
    ?, ?, ?,
    ?, ?, ?,
    ?, ?
)
"""

_UPDATE_METADATA_SQL = """
UPDATE oauth_providers SET
    name = ?,
    usage = ?,
    auth_flow = ?,
    logo_svg = ?,
    logo_path = ?,
    logo_type = ?,
    authorize_url = ?,
    token_url = ?,
    userinfo_url = ?,
    device_code_url = ?,
    revoke_url = ?,
    scopes = ?,
    pkce = ?,
    tenant_specific = ?,
    tenant_config = ?,
    claim_sub = ?,
    claim_email = ?,
    claim_name = ?,
    oidc_discovery_url = ?,
    token_storage = ?,
    platform_bindable = ?,
    platform_bind_ttl = ?,
    display_order = ?,
    description = ?,
    updated_at = ?
WHERE id = ? AND is_builtin = 1
"""


def seed_builtin_oauth_providers(
    cursor,
    *,
    force_metadata: bool = False,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Seed built-in OAuth providers from the catalog file (idempotent).

    New rows are inserted with ``INSERT OR IGNORE``. Existing rows keep
    ``enabled``, ``client_id``, and ``client_secret_encrypted`` unchanged.

    When *force_metadata* is True, built-in rows get endpoint/scope/logo
    metadata refreshed (still without touching secrets or enabled).
    """
    data = catalog or load_builtin_catalog()
    base = catalog_dir()
    now = time.time()
    inserted = 0
    metadata_updated = 0

    for record in data["providers"]:
        row = _row_from_record(record, now=now, base=base)
        cursor.execute(_INSERT_SQL, row)
        if cursor.rowcount > 0:
            inserted += 1
            continue

        if not force_metadata:
            continue

        pid = record["id"]
        endpoints = record.get("endpoints") or {}
        icon = record.get("icon") or {}
        logo_svg, logo_path, logo_type = _read_icon_svg(icon, base)
        tenant_defaults = record.get("tenant_config_defaults") or {}
        if not isinstance(tenant_defaults, dict):
            tenant_defaults = {}
        tenant_specific = bool(record.get("tenant_specific")) or bool(tenant_defaults)
        scopes = record.get("scopes") or []
        if not isinstance(scopes, list):
            scopes = []

        cursor.execute(
            _UPDATE_METADATA_SQL,
            (
                record.get("name") or pid,
                record.get("usage") or "login",
                record.get("auth_flow") or "pkce_loopback",
                logo_svg,
                logo_path,
                logo_type,
                str(endpoints.get("authorize_url") or ""),
                str(endpoints.get("token_url") or ""),
                str(endpoints.get("userinfo_url") or ""),
                str(endpoints.get("device_code_url") or ""),
                str(endpoints.get("revoke_url") or ""),
                json.dumps(scopes),
                1 if record.get("pkce", True) else 0,
                1 if tenant_specific else 0,
                json.dumps(tenant_defaults),
                str(record.get("claim_sub") or "sub"),
                str(record.get("claim_email") or "email"),
                str(record.get("claim_name") or "name"),
                str(endpoints.get("oidc_discovery_url") or ""),
                str(record.get("token_storage") or "identities"),
                1 if record.get("platform_bindable") else 0,
                int(record.get("platform_bind_ttl") or 3600),
                int(record.get("display_order") or 0),
                str(record.get("description") or ""),
                now,
                pid,
            ),
        )
        if cursor.rowcount > 0:
            metadata_updated += 1

    return {
        "catalog_id": str(data.get("catalog_id") or ""),
        "schema_version": int(data.get("schema_version") or 0),
        "total": len(data["providers"]),
        "inserted": inserted,
        "metadata_updated": metadata_updated,
    }


def write_catalog_marker(catalog_id: str) -> None:
    """Record successful catalog seed under INTELLECT_HOME."""
    from intellect_constants import get_intellect_home

    path = get_intellect_home() / CATALOG_MARKER_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{catalog_id}\n{time.time()}\n", encoding="utf-8")


def seed_builtin_oauth_providers_db(db, *, force_metadata: bool = False) -> dict[str, Any]:
    """Seed using a SessionDB / MembershipDB instance."""
    stats: dict[str, Any] = {}

    def _run(cursor):
        nonlocal stats
        stats = seed_builtin_oauth_providers(cursor, force_metadata=force_metadata)

    db._execute_write(_run)
    if stats.get("catalog_id"):
        write_catalog_marker(stats["catalog_id"])
    return stats
