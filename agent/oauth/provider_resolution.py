"""OAuth provider resolution — DB primary, legacy YAML overlay."""

from __future__ import annotations

import logging
from typing import Any

from agent.oauth import OAuthEngine, OAuthProviderConfig

logger = logging.getLogger(__name__)

_YAML_DEPRECATION_LOGGED = False

# Lazily built from builtin catalog
_ALIAS_TO_ID: dict[str, str] | None = None

_ENTERPRISE_LOGIN_IDS = frozenset({"wecom", "dingtalk", "feishu"})


def _alias_map() -> dict[str, str]:
    global _ALIAS_TO_ID
    if _ALIAS_TO_ID is not None:
        return _ALIAS_TO_ID
    mapping: dict[str, str] = {}
    try:
        from agent.oauth.builtin_catalog import load_builtin_catalog

        for record in load_builtin_catalog()["providers"]:
            pid = str(record.get("id") or "").strip().lower()
            if not pid:
                continue
            for alias in record.get("aliases") or []:
                mapping[str(alias).strip().lower()] = pid
    except Exception as exc:
        logger.debug("oauth alias map unavailable: %s", exc)
    _ALIAS_TO_ID = mapping
    return mapping


def normalize_provider_id(provider_id: str) -> str:
    """Map aliases (e.g. lark → feishu) to canonical provider ids."""
    key = str(provider_id or "").strip().lower()
    return _alias_map().get(key, key)


def effective_login_pkce(provider_id: str, pkce_from_row: bool) -> bool:
    """Return whether login OAuth should use PKCE for *provider_id*.

    Gitee/GitHub use classic client_secret flows (no PKCE). DB rows seeded before
    catalog fixes may still have ``pkce=1``; preset/catalog values win for builtins.
    """
    pid = normalize_provider_id(provider_id)
    try:
        from agent.oauth._stubs import OAUTH_PROVIDER_PRESETS

        if pid in OAUTH_PROVIDER_PRESETS and "pkce" in OAUTH_PROVIDER_PRESETS[pid]:
            return bool(OAUTH_PROVIDER_PRESETS[pid]["pkce"])
    except Exception:
        logger.debug('non-critical operation failed', exc_info=True)
    try:
        from agent.oauth.builtin_catalog import load_builtin_catalog

        for record in load_builtin_catalog().get("providers") or []:
            if str(record.get("id") or "").strip().lower() == pid:
                return bool(record.get("pkce", pkce_from_row))
    except Exception:
        logger.debug('non-critical operation failed', exc_info=True)
    return bool(pkce_from_row)


def open_members_db(config: dict[str, Any] | None, db: Any = None) -> Any | None:
    """Return MembershipDB (or pass-through *db*) when members OAuth is enabled."""
    if db is not None:
        return db
    from agent.oauth._stubs import is_oauth_enabled

    if not is_oauth_enabled(config):
        return None
    try:
        # (single-user: MembershipDB removed)
def _noop_db(*a, **kw): return None
MembershipDB = _noop_db

        return MembershipDB(config=config or {})
    except Exception as exc:
        logger.debug("MembershipDB unavailable for OAuth resolution: %s", exc)
        return None


def _warn_yaml_providers_deprecated() -> None:
    global _YAML_DEPRECATION_LOGGED
    if _YAML_DEPRECATION_LOGGED:
        return
    _YAML_DEPRECATION_LOGGED = True
    logger.warning(
        "members.oauth.providers in config.yaml is deprecated; "
        "configure providers in Intellect WebUI Settings or via "
        "'intellect oauth enable <id>'."
    )


def _auth_flow_to_type(auth_flow: str) -> str:
    if auth_flow.startswith("oauth2_"):
        return "oauth2"
    if auth_flow == "oidc_discovery":
        return "oidc"
    return "oidc"


def _decrypt_client_secret(encrypted: str) -> str:
    return resolve_client_secret_stored(encrypted)


def resolve_client_secret_stored(stored: str) -> str:
    """Return plaintext client secret from a DB ``client_secret_encrypted`` value.

    Supports Fernet ciphertext (canonical), legacy plaintext rows written before
    WebUI encrypt-on-save, and rejects 64-char hex digests that cannot be used
    as OAuth client secrets.
    """
    raw = str(stored or "").strip()
    if not raw:
        return ""
    try:
        from agent.oauth.storage import decrypt_token

        return decrypt_token(raw)
    except Exception:
        logger.debug('non-critical operation failed', exc_info=True)
    if raw.startswith("gAAAA"):
        return ""
    if len(raw) == 64 and all(c in "0123456789abcdefABCDEF" for c in raw):
        return ""
    return raw


def config_to_members_dict(cfg: OAuthProviderConfig) -> dict[str, Any]:
    """Convert ``OAuthProviderConfig`` to ``members_oauth`` provider dict shape."""
    tenant = cfg.tenant_config if isinstance(cfg.tenant_config, dict) else {}
    out: dict[str, Any] = {
        "id": cfg.id,
        "display_name": cfg.name,
        "enabled": cfg.enabled,
        "type": _auth_flow_to_type(cfg.auth_flow),
        "auth_flow": cfg.auth_flow,
        "usage": cfg.usage,
        "authorization_endpoint": cfg.authorize_url,
        "token_endpoint": cfg.token_url,
        "userinfo_endpoint": cfg.userinfo_url,
        "scopes": list(cfg.scopes),
        "pkce": cfg.pkce,
        "claim_sub": cfg.claim_sub,
        "claim_email": cfg.claim_email,
        "claim_name": cfg.claim_name,
        "client_id": cfg.client_id or "",
        "logo_svg": cfg.logo_svg,
        "logo_path": cfg.logo_path,
        "description": cfg.description,
        "is_builtin": cfg.is_builtin,
        "oidc_discovery_url": cfg.oidc_discovery_url,
    }
    secret = resolve_client_secret_stored(cfg.client_secret_encrypted)
    if not secret:
        from agent.oauth._stubs import get_provider_secret

        secret = get_provider_secret({"id": cfg.id})
    if secret:
        out["client_secret"] = secret

    if cfg.id == "azure_ad":
        out["tenant"] = tenant.get("tenant", "common")
        out["issuer"] = tenant.get(
            "issuer",
            f"https://login.microsoftonline.com/{out['tenant']}/v2.0",
        )
    elif cfg.id == "wecom":
        out["corp_id"] = tenant.get("corp_id", "")
        out["agent_id"] = tenant.get("agent_id", "")
    elif cfg.id == "dingtalk":
        out["app_key"] = out["client_id"]
    elif cfg.id == "feishu":
        out["domain"] = tenant.get("domain", "feishu")
    elif cfg.id in ("gitlab", "gitea"):
        if tenant.get("base_url"):
            out["base_url"] = tenant["base_url"]

    return out


def config_to_public_dict(cfg: OAuthProviderConfig) -> dict[str, Any]:
    """Public provider fields for login/register UI."""
    return {
        "id": cfg.id,
        "display_name": cfg.name,
        "type": _auth_flow_to_type(cfg.auth_flow),
        "auth_flow": cfg.auth_flow,
    }


def resolve_login_provider(
    config: dict[str, Any] | None,
    provider_id: str,
    *,
    db: Any = None,
    usage: str = "login",
) -> dict[str, Any] | None:
    """Resolve a login provider by id (DB primary)."""
    from agent.oauth._stubs import is_oauth_enabled

    if not is_oauth_enabled(config):
        return None

    canonical = normalize_provider_id(provider_id)
    membership_db = open_members_db(config, db)
    if membership_db is None:
        return _resolve_login_provider_legacy_yaml(config, canonical)

    try:
        engine = OAuthEngine(config=config, db=membership_db)
        cfg = engine.get_provider(canonical)
        if cfg is None or not cfg.enabled:
            if cfg is None:
                return _resolve_login_provider_legacy_yaml(config, canonical)
            return None
        return config_to_members_dict(cfg)
    finally:
        if db is None and membership_db is not None:
            membership_db.close()


def list_enabled_login_providers(
    config: dict[str, Any] | None,
    *,
    db: Any = None,
) -> list[dict[str, Any]]:
    """List enabled login providers from DB (YAML legacy fallback when DB empty)."""
    from agent.oauth._stubs import is_oauth_enabled

    if not is_oauth_enabled(config):
        return []

    membership_db = open_members_db(config, db)
    if membership_db is None:
        return _list_enabled_legacy_yaml(config)

    try:
        engine = OAuthEngine(config=config, db=membership_db)
        providers = engine.list_providers(usage="login", enabled_only=True)
        if providers:
            return [config_to_public_dict(p) for p in providers]
        return _list_enabled_legacy_yaml(config)
    finally:
        if db is None and membership_db is not None:
            membership_db.close()


def _resolve_login_provider_legacy_yaml(
    config: dict[str, Any] | None,
    provider_id: str,
) -> dict[str, Any] | None:
    from agent.oauth._stubs import OAUTH_PROVIDER_PRESETS, get_oauth_config

    preset = OAUTH_PROVIDER_PRESETS.get(provider_id)
    if not preset:
        return None

    oauth_cfg = get_oauth_config(config)
    yaml_list = oauth_cfg.get("providers", [])
    if yaml_list:
        _warn_yaml_providers_deprecated()

    user_entry: dict[str, Any] = {}
    for entry in yaml_list:
        if entry.get("id") == provider_id:
            user_entry = entry
            break

    if user_entry.get("enabled") is False:
        return None

    merged = dict(preset)
    merged.update(user_entry)
    merged.setdefault("enabled", True)
    return merged


def _list_enabled_legacy_yaml(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    from agent.oauth._stubs import get_oauth_config

    oauth_cfg = get_oauth_config(config)
    yaml_list = oauth_cfg.get("providers", [])
    if not yaml_list:
        return []
    _warn_yaml_providers_deprecated()

    result: list[dict[str, Any]] = []
    for entry in yaml_list:
        if entry.get("enabled") is False:
            continue
        merged = _resolve_login_provider_legacy_yaml(config, entry.get("id", ""))
        if merged:
            result.append({
                "id": merged["id"],
                "display_name": merged.get("display_name", merged["id"]),
                "type": merged.get("type", "oidc"),
            })
    return result


def enterprise_provider_config_hint(provider_id: str) -> str:
    """Doctor hint for incomplete enterprise OAuth credentials."""
    if provider_id == "wecom":
        return "Set corp_id, agent_id, and client secret (DB tenant_config or WECOM_OAUTH_CLIENT_SECRET)"
    if provider_id == "dingtalk":
        return "Set AppKey as client_id and AppSecret in Settings or DINGTALK_OAUTH_CLIENT_SECRET"
    if provider_id == "feishu":
        return "Set App ID / App Secret in Settings or FEISHU_OAUTH_CLIENT_SECRET"
    return "Complete OAuth credentials in Settings or ~/.intellect/.env"
