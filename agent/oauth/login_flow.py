"""Member login OAuth flows — route by ``auth_flow`` using DB-backed config."""

from __future__ import annotations

import logging
from typing import Any

from agent.oauth import OAuthProviderConfig

# ── Stage 5: Rust crypto ───────────────────────────────────────────────────
try:
    from intellect_core import pkce_challenge as _rust_pkce_challenge  # type: ignore[import-not-found]
    from intellect_core import secure_token_hex as _rust_secure_hex
    _HAS_RUST_CRYPTO = True
except (ImportError, AttributeError):
    _HAS_RUST_CRYPTO = False

logger = logging.getLogger(__name__)

_ENTERPRISE_AUTH_FLOWS = frozenset({
    "oauth2_wecom",
    "oauth2_dingtalk",
    "oauth2_feishu",
})

_LARK_AUTH_URL = "https://accounts.larksuite.com/open-apis/authen/v1/authorize"
_LARK_TOKEN_URL = "https://open.larksuite.com/open-apis/authen/v2/oauth/token"
_LARK_USERINFO_URL = "https://open.larksuite.com/open-apis/authen/v1/user_info"


def _members_provider_dict(cfg: OAuthProviderConfig) -> dict[str, Any]:
    from agent.oauth.provider_resolution import config_to_members_dict

    return config_to_members_dict(cfg)


def _apply_feishu_domain_endpoints(provider: dict[str, Any]) -> dict[str, Any]:
    """Override Feishu/Lark endpoints when tenant domain is ``lark``."""
    domain = str(provider.get("domain") or "").strip().lower()
    if domain != "lark":
        return provider
    merged = dict(provider)
    merged["authorization_endpoint"] = _LARK_AUTH_URL
    merged["token_endpoint"] = _LARK_TOKEN_URL
    merged["userinfo_endpoint"] = _LARK_USERINFO_URL
    return merged


def provider_login_ready(cfg: OAuthProviderConfig) -> bool:
    """Return True when *cfg* has enough credentials to start login."""
    from agent.oauth._stubs import provider_oauth_login_ready

    return provider_oauth_login_ready(_members_provider_dict(cfg))


def build_authorization_url(
    cfg: OAuthProviderConfig,
    redirect_uri: str,
    state: str,
    code_challenge: str | None = None,
) -> str | None:
    """Build IdP authorization URL for a DB-backed login provider."""
    from agent.oauth._stubs import build_authorization_url as _build_url

    provider = _apply_feishu_domain_endpoints(_members_provider_dict(cfg))
    return _build_url(provider, redirect_uri, state, code_challenge)


def exchange_authorization_code(
    cfg: OAuthProviderConfig,
    code: str,
    redirect_uri: str,
    code_verifier: str | None = None,
) -> dict[str, Any] | None:
    """Exchange authorization code for tokens."""
    from agent.oauth._stubs import exchange_code_for_tokens

    provider = _apply_feishu_domain_endpoints(_members_provider_dict(cfg))
    if cfg.auth_flow == "oauth2_feishu":
        token_endpoint = provider.get("token_endpoint") or cfg.token_url
        provider = {**provider, "token_endpoint": token_endpoint}
    return exchange_code_for_tokens(
        provider,
        code=code,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
    )


def extract_login_claims(
    cfg: OAuthProviderConfig,
    token_response: dict[str, Any],
) -> dict[str, str] | None:
    """Extract normalized login claims ``{sub, email, name}``."""
    from agent.oauth._stubs import extract_claims

    provider = _apply_feishu_domain_endpoints(_members_provider_dict(cfg))
    return extract_claims(provider, token_response)


def uses_members_oauth_adapter(auth_flow: str) -> bool:
    """True when authorize/token exchange should use ``members_oauth`` adapters."""
    return (
        auth_flow in _ENTERPRISE_AUTH_FLOWS
        or auth_flow == "oidc_discovery"
        or auth_flow == "pkce_loopback"
    )


def start_login_session(
    engine: Any,
    provider_id: str,
    *,
    redirect_uri: str,
    state: str | None = None,
    code_verifier: str | None = None,
    usage: str = "login",
) -> tuple[Any | None, str | None]:
    """Start login authorize; returns (OAuthSession, auth_url) or (None, error).

    Uses file-backed *state* when provided (WebUI); otherwise generates state
    for CLI loopback flows.
    """
    from agent.oauth import OAuthSession
    from agent.oauth._stubs import generate_pkce_pair

    cfg = engine.get_provider(provider_id)
    if not cfg:
        return None, f"Unknown provider: {provider_id}"
    if not cfg.enabled:
        return None, f"Provider {provider_id} is not enabled"
    if not provider_login_ready(cfg):
        return None, f"Provider {provider_id} is missing OAuth credentials"

    if cfg.auth_flow == "device_code":
        return engine._start_device_code(cfg, usage)

    # P3-2: MSAL Native adapter for Azure AD (F2 — wiring)
    if cfg.id == "azure_ad" and _msal_enabled_for_config(cfg):
        return _start_login_via_msal(cfg, redirect_uri, state, usage)

    verifier = code_verifier or ""
    challenge = None
    if cfg.pkce and not verifier:
        if _HAS_RUST_CRYPTO:
            verifier, challenge = _rust_pkce_challenge()
        else:
            verifier, challenge = generate_pkce_pair()
    elif cfg.pkce and verifier:
        if _HAS_RUST_CRYPTO:
            from intellect_core import pkce_challenge_from_verifier as _rust_pkce_from
            challenge = _rust_pkce_from(verifier)
        else:
            import base64, hashlib
            digest = hashlib.sha256(verifier.encode()).digest()
            challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    flow_state = state
    if not flow_state:
        if _HAS_RUST_CRYPTO:
            flow_state = _rust_secure_hex(16)
        else:
            import secrets
            flow_state = secrets.token_hex(16)

    auth_url = build_authorization_url(cfg, redirect_uri, flow_state, challenge)
    if not auth_url:
        return None, f"Could not build authorization URL for {provider_id}"

    session = OAuthSession(
        provider_id=cfg.id,
        state=flow_state,
        code_verifier=verifier,
        redirect_uri=redirect_uri,
        usage=usage,
        created_at=__import__("time").time(),
    )
    return session, auth_url


def complete_login(
    engine: Any,
    provider_id: str,
    code: str,
    redirect_uri: str,
    code_verifier: str | None = None,
) -> Any | None:
    """Complete login and return ``OAuthResult``."""
    from agent.oauth import OAuthResult

    cfg = engine.get_provider(provider_id)
    if not cfg:
        return None

    token_resp = exchange_authorization_code(
        cfg, code, redirect_uri, code_verifier=code_verifier
    )
    if not token_resp:
        return None

    claims = extract_login_claims(cfg, token_resp) or {}
    return OAuthResult(
        provider_id=cfg.id,
        usage="login",
        access_token=str(token_resp.get("access_token") or ""),
        refresh_token=token_resp.get("refresh_token"),
        expires_in=int(token_resp.get("expires_in") or 0),
        claims=claims,
    )


def _msal_enabled_for_config(cfg) -> bool:
    """Check if MSAL Native should be used for this provider (F2)."""
    if not cfg or not hasattr(cfg, "metadata") or not cfg.metadata:
        return False
    meta = cfg.metadata if isinstance(cfg.metadata, dict) else {}
    use_msal = meta.get("use_msal") if isinstance(meta, dict) else meta
    return bool(use_msal)


def _start_login_via_msal(
    cfg, redirect_uri: str, state: str | None, usage: str
) -> tuple[Any | None, str | None]:
    """Start an Azure AD login via the MSAL Native adapter (F2)."""
    try:
        from agent.oauth.flows.msal_adapter import (
            build_msal_client,
            initiate_auth_code_flow as msal_auth_flow,
        )
        from agent.oauth._stubs import get_provider_secret
    except ImportError as exc:
        return None, f"MSAL adapter unavailable: {exc}"

    tenant_id = (cfg.metadata or {}).get("tenant_id", "organizations") if isinstance(cfg.metadata, dict) else "organizations"
    client_id = cfg.client_id
    client_secret = get_provider_secret(cfg)

    app = build_msal_client(client_id, client_secret, tenant_id=tenant_id)
    flow = msal_auth_flow(
        app,
        scopes=cfg.scopes or ["User.Read"],
        redirect_uri=redirect_uri,
        state=state,
    )

    session = type("OAuthSession", (), {
        "provider_id": cfg.id,
        "state": state or "",
        "code_verifier": "",
        "redirect_uri": redirect_uri,
        "usage": usage,
        "created_at": __import__("time").time(),
        "_msal_app": app,
        "_msal_flow": flow["flow_session"],
    })()
    return session, flow["auth_uri"]
