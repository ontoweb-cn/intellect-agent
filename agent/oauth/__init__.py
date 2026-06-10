"""OAuth unified platform — single entry point for login, model, and server OAuth.

Schema: ``oauth_providers`` + ``oauth_tokens`` tables (v19).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote as _url_quote, urlencode as _url_encode

# ── Stage 5: Rust crypto acceleration ──────────────────────────────────────
try:
    from intellect_core import (  # type: ignore[import-not-found]
        pkce_challenge as _rust_pkce_challenge,
        secure_token_hex as _rust_secure_hex,
    )
    _HAS_RUST_CRYPTO = True
except (ImportError, AttributeError):
    _HAS_RUST_CRYPTO = False

logger = logging.getLogger(__name__)

# ── Core types ──────────────────────────────────────────────────────────────


@dataclass
class OAuthProviderConfig:
    """Normalized provider configuration loaded from DB/config/builtins."""

    id: str
    name: str
    usage: str = "login"             # 'login' | 'model' | 'server' | 'both'
    auth_flow: str = "pkce_loopback"  # 'pkce_loopback' | 'device_code' | 'oidc_discovery' | 'trusted_header'
    enabled: bool = False

    # LOGO
    logo_svg: str = ""
    logo_path: str = ""
    logo_type: str = "svg"

    # Endpoints
    client_id: str = ""
    client_secret_encrypted: str = ""
    authorize_url: str = ""
    token_url: str = ""
    userinfo_url: str = ""
    device_code_url: str = ""
    revoke_url: str = ""

    # Params
    scopes: list[str] = field(default_factory=list)
    pkce: bool = True
    tenant_specific: bool = False
    tenant_config: dict = field(default_factory=dict)

    # Claims
    claim_sub: str = "sub"
    claim_email: str = "email"
    claim_name: str = "name"

    # OIDC
    oidc_discovery_url: str = ""

    # Storage
    token_storage: str = "identities"

    # Gateway
    platform_bindable: bool = False
    platform_bind_ttl: int = 3600

    # Display
    display_order: int = 0
    is_builtin: bool = False
    description: str = ""


@dataclass
class OAuthSession:
    """Active OAuth authorization session."""

    provider_id: str
    state: str
    code_verifier: str
    redirect_uri: str
    usage: str = "login"
    created_at: float = 0.0


@dataclass
class OAuthResult:
    """Completed OAuth authorization result."""

    provider_id: str
    usage: str
    access_token: str
    refresh_token: str | None = None
    expires_in: int = 0
    claims: dict = field(default_factory=dict)


# ── OAuthEngine ─────────────────────────────────────────────────────────────


class OAuthEngine:
    """Single entry point for all OAuth flows.

    Provider resolution: DB (oauth_providers table) is the primary source;
    legacy ``members.oauth.providers`` in config.yaml only supplements missing
    DB rows or empty credentials; hardcoded builtins apply when no DB is open.

    Usage:
        engine = OAuthEngine(config, db)
        providers = engine.list_providers(usage='login')
        session, auth_url = engine.start_authorize('github', usage='login')
        result = engine.complete_authorize(session, {'code': '...', 'state': '...'})
        member_id = engine.resolve_login(result)
    """

    # In-memory session store: state → OAuthSession (lives for the CLI process lifetime)
    _sessions: dict[str, OAuthSession] = {}

    def __init__(self, config: dict | None = None, db: Any = None):
        self._config = config or {}
        self._db = db
        # One-time migration of legacy auth.json tokens to oauth_tokens table
        if db:
            try:
                from agent.oauth.auth_json_migration import migrate_auth_tokens
                migrate_auth_tokens(db)
            except Exception:
                pass

    # ── Provider management ───────────────────────────────────────────────

    def list_providers(self, usage: str = "", enabled_only: bool = True) -> list[OAuthProviderConfig]:
        """List providers: DB primary, legacy YAML overlay, builtins fallback."""
        result: dict[str, OAuthProviderConfig] = {}

        if self._db:
            try:
                rows = self._db._conn.execute(
                    "SELECT * FROM oauth_providers ORDER BY display_order"
                ).fetchall()
                for row in rows:
                    p = self._row_to_config(dict(row))
                    result[p.id] = p
            except Exception as exc:
                logger.debug("oauth_providers DB read failed: %s", exc)

        if result:
            self._apply_legacy_yaml_overlay(result)
        else:
            for bp in self._builtin_providers("", enabled_only=False):
                result[bp.id] = bp
            self._apply_legacy_yaml_overlay(result)

        filtered: list[OAuthProviderConfig] = []
        for p in result.values():
            if usage and p.usage not in (usage, "both"):
                continue
            if enabled_only and not p.enabled:
                continue
            filtered.append(p)
        filtered.sort(key=lambda p: p.display_order)
        return filtered

    def _apply_legacy_yaml_overlay(self, result: dict[str, OAuthProviderConfig]) -> None:
        """Apply deprecated config.yaml provider entries without overriding DB policy fields."""
        yaml_list = self._config.get("members", {}).get("oauth", {}).get("providers", [])
        if not yaml_list:
            return

        from agent.oauth.provider_resolution import _warn_yaml_providers_deprecated

        _warn_yaml_providers_deprecated()

        for entry in yaml_list:
            if not isinstance(entry, dict):
                continue
            pid = str(entry.get("id") or "").strip()
            if not pid:
                continue

            if entry.get("enabled") is False:
                # DB Settings enable wins over deprecated YAML disable flags.
                existing = result.get(pid)
                if existing is not None and existing.enabled:
                    continue
                result.pop(pid, None)
                continue

            yaml_cfg = self._yaml_to_config(entry)
            existing = result.get(pid)
            if existing:
                result[pid] = self._merge_legacy_credentials(existing, yaml_cfg)
            else:
                yaml_cfg.enabled = True
                result[pid] = yaml_cfg

    @staticmethod
    def _merge_legacy_credentials(
        base: OAuthProviderConfig,
        yaml_cfg: OAuthProviderConfig,
    ) -> OAuthProviderConfig:
        """Fill empty DB credential/endpoint fields from legacy YAML only."""
        merged = OAuthProviderConfig(**base.__dict__.copy())
        if not merged.client_id and yaml_cfg.client_id:
            merged.client_id = yaml_cfg.client_id
        if not merged.client_secret_encrypted and yaml_cfg.client_secret_encrypted:
            merged.client_secret_encrypted = yaml_cfg.client_secret_encrypted
        if not merged.scopes and yaml_cfg.scopes:
            merged.scopes = list(yaml_cfg.scopes)
        for field in (
            "authorize_url",
            "token_url",
            "userinfo_url",
            "oidc_discovery_url",
        ):
            if not getattr(merged, field) and getattr(yaml_cfg, field):
                setattr(merged, field, getattr(yaml_cfg, field))
        return merged

    @staticmethod
    def _merge_config(base: OAuthProviderConfig, override: OAuthProviderConfig) -> OAuthProviderConfig:
        """Merge override into base, keeping non-empty fields from override."""
        base_dict = base.__dict__.copy()
        for key, val in override.__dict__.items():
            if val:  # non-empty overrides
                base_dict[key] = val
        return OAuthProviderConfig(**base_dict)

    @staticmethod
    def _yaml_to_config(entry: dict) -> OAuthProviderConfig:
        """Convert a legacy config.yaml provider entry to OAuthProviderConfig."""
        from agent.oauth._stubs import OAUTH_PROVIDER_PRESETS

        scopes = entry.get("scopes", [])
        if isinstance(scopes, str):
            scopes = [s.strip() for s in scopes.split(",") if s.strip()]

        pid = entry.get("id", "")
        preset = OAUTH_PROVIDER_PRESETS.get(pid, {})
        auth_flow = entry.get("auth_flow") or preset.get("auth_flow") or "pkce_loopback"
        if pid in ("wecom", "dingtalk", "feishu") and auth_flow == "pkce_loopback":
            auth_flow = f"oauth2_{pid}"

        name = entry.get("display_name") or preset.get("display_name") or pid
        if not scopes and preset.get("scopes"):
            scopes = list(preset["scopes"])

        tenant: dict[str, Any] = {}
        if isinstance(entry.get("tenant_config"), dict):
            tenant.update(entry["tenant_config"])
        for key in ("corp_id", "agent_id", "tenant", "base_url", "domain"):
            if entry.get(key):
                tenant[key] = entry[key]

        return OAuthProviderConfig(
            id=pid,
            name=name,
            usage=entry.get("usage", "login"),
            auth_flow=auth_flow,
            enabled=bool(entry.get("enabled", True)),
            client_id=entry.get("client_id", "") or entry.get("app_key", ""),
            scopes=scopes,
            authorize_url=(
                entry.get("authorize_url")
                or preset.get("authorization_endpoint", "")
            ),
            token_url=entry.get("token_url") or preset.get("token_endpoint", ""),
            userinfo_url=(
                entry.get("userinfo_url") or preset.get("userinfo_endpoint", "")
            ),
            pkce=entry.get("pkce", preset.get("pkce", True)),
            tenant_config=tenant,
            claim_sub=entry.get("claim_sub", preset.get("claim_sub", "sub")),
            claim_email=entry.get("claim_email", preset.get("claim_email", "email")),
            claim_name=entry.get("claim_name", preset.get("claim_name", "name")),
            oidc_discovery_url=entry.get("oidc_discovery_url", "")
            or preset.get("discovery_url", ""),
        )

    def get_provider(self, provider_id: str) -> OAuthProviderConfig | None:
        from agent.oauth.provider_resolution import normalize_provider_id

        canonical = normalize_provider_id(provider_id)
        for p in self.list_providers(enabled_only=False):
            if p.id == canonical:
                return p
        return None

    # ── Authorization flows ────────────────────────────────────────────────

    def start_authorize(
        self, provider_id: str, usage: str = "login", **kwargs
    ) -> tuple[OAuthSession | None, str | None]:
        """Start an OAuth flow. Returns (session, auth_url) or (None, error)."""
        from agent.oauth.login_flow import start_login_session

        redirect_uri = kwargs.get("redirect_uri", "http://127.0.0.1:18923/callback")
        session, result = start_login_session(
            self,
            provider_id,
            redirect_uri=redirect_uri,
            state=kwargs.get("state"),
            code_verifier=kwargs.get("code_verifier"),
            usage=usage,
        )
        if session is None:
            return None, result

        if session.state:
            OAuthEngine._sessions[session.state] = session
            exp_thresh = time.time() - 600
            stale = [s for s, v in OAuthEngine._sessions.items() if v.created_at < exp_thresh]
            for s in stale:
                del OAuthEngine._sessions[s]

        return session, result

    def complete_authorize(
        self, session: OAuthSession, callback_params: dict
    ) -> OAuthResult | None:
        """Complete an OAuth flow. Returns OAuthResult or None."""
        code = callback_params.get("code", "")
        if not code:
            return None

        if session.usage == "login":
            from agent.oauth.login_flow import complete_login

            return complete_login(
                self,
                session.provider_id,
                code,
                session.redirect_uri,
                code_verifier=session.code_verifier or None,
            )

        provider = self.get_provider(session.provider_id)
        if not provider:
            return None

        token_resp = self._exchange_code(
            provider, code, session.redirect_uri, session.code_verifier
        )
        if not token_resp:
            return None

        claims = self._extract_claims(provider, token_resp)
        return OAuthResult(
            provider_id=provider.id,
            usage=session.usage,
            access_token=token_resp.get("access_token", ""),
            refresh_token=token_resp.get("refresh_token"),
            expires_in=token_resp.get("expires_in", 0),
            claims=claims,
        )

    # ── Resolution ─────────────────────────────────────────────────────────

    def resolve_login(self, result: OAuthResult) -> str | None:
        """Resolve an OAuth login result to a member_id."""
        if self._db:
            try:
                row = self._db._conn.execute(
                    "SELECT member_id FROM identities WHERE provider = ? AND provider_id = ?",
                    (f"oauth:{result.provider_id}", result.claims.get("sub", "")),
                ).fetchone()
                if row:
                    return row["member_id"]
            except Exception:
                pass
        return None

    # ── Model OAuth tokens (PR-A4) ─────────────────────────────────────────

    def has_model_token(self, provider_id: str, *, member_id: str | None = None) -> bool:
        if not self._db:
            return False
        from agent.oauth.model_tokens import provider_has_db_token

        return provider_has_db_token(self._db, provider_id, member_id=member_id)

    def store_model_token(
        self,
        provider_id: str,
        *,
        access_token: str,
        refresh_token: str | None = None,
        expires_in: int = 0,
        scope: str = "",
        member_id: str | None = None,
    ) -> str:
        if not self._db:
            raise RuntimeError("OAuthEngine requires a database connection")
        from agent.oauth.model_tokens import persist_model_token

        return persist_model_token(
            self._db,
            provider_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
            scope=scope,
            member_id=member_id,
        )

    def get_model_token(
        self, provider_id: str, *, member_id: str | None = None
    ) -> dict | None:
        if not self._db:
            return None
        from agent.oauth.model_tokens import db_provider_id
        from agent.oauth.storage import get_oauth_token

        return get_oauth_token(db_provider_id(provider_id), self._db, member_id=member_id)

    def revoke_model_token(
        self, provider_id: str, *, member_id: str | None = None
    ) -> bool:
        if not self._db:
            return False
        from agent.oauth.model_tokens import delete_model_token

        return delete_model_token(self._db, provider_id, member_id=member_id)

    # ── Internal helpers ───────────────────────────────────────────────────

    def _row_to_config(self, row: dict) -> OAuthProviderConfig:
        scopes_raw = row.get("scopes", "[]")
        try:
            scopes = json.loads(scopes_raw) if isinstance(scopes_raw, str) else scopes_raw
        except json.JSONDecodeError:
            scopes = []
        tenant_raw = row.get("tenant_config", "{}")
        try:
            tenant = json.loads(tenant_raw) if isinstance(tenant_raw, str) else tenant_raw
        except json.JSONDecodeError:
            tenant = {}

        from agent.oauth.provider_resolution import effective_login_pkce

        provider_id = str(row.get("id") or "")
        return OAuthProviderConfig(
            id=provider_id,
            name=row.get("name", ""),
            usage=row.get("usage", "login"),
            auth_flow=row.get("auth_flow", "pkce_loopback"),
            enabled=bool(row.get("enabled", False)),
            logo_svg=row.get("logo_svg", ""),
            logo_path=row.get("logo_path", ""),
            logo_type=row.get("logo_type", "svg"),
            client_id=row.get("client_id", ""),
            client_secret_encrypted=row.get("client_secret_encrypted", ""),
            authorize_url=row.get("authorize_url", ""),
            token_url=row.get("token_url", ""),
            userinfo_url=row.get("userinfo_url", ""),
            device_code_url=row.get("device_code_url", ""),
            revoke_url=row.get("revoke_url", ""),
            scopes=scopes,
            pkce=effective_login_pkce(provider_id, bool(row.get("pkce", True))),
            tenant_specific=bool(row.get("tenant_specific", False)),
            tenant_config=tenant,
            claim_sub=row.get("claim_sub", "sub"),
            claim_email=row.get("claim_email", "email"),
            claim_name=row.get("claim_name", "name"),
            oidc_discovery_url=row.get("oidc_discovery_url", ""),
            token_storage=row.get("token_storage", "identities"),
            platform_bindable=bool(row.get("platform_bindable", False)),
            platform_bind_ttl=row.get("platform_bind_ttl", 3600),
            display_order=row.get("display_order", 0),
            is_builtin=bool(row.get("is_builtin", False)),
            description=row.get("description", ""),
        )

    def _builtin_providers(
        self, usage: str = "", enabled_only: bool = True
    ) -> list[OAuthProviderConfig]:
        """Return hardcoded builtin providers as fallback."""
        builtins = [
            # Login OAuth
            OAuthProviderConfig(id="github", name="GitHub", usage="login",
                auth_flow="pkce_loopback", enabled=True, is_builtin=True, display_order=0,
                authorize_url="https://github.com/login/oauth/authorize",
                token_url="https://github.com/login/oauth/access_token",
                userinfo_url="https://api.github.com/user",
                scopes=["read:user","user:email"], claim_sub="id",
                claim_email="email", claim_name="login", pkce=False),
            OAuthProviderConfig(id="google", name="Google", usage="login",
                auth_flow="pkce_loopback", enabled=True, is_builtin=True, display_order=1,
                authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
                token_url="https://oauth2.googleapis.com/token",
                userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
                scopes=["openid","profile","email"], claim_sub="sub"),
            OAuthProviderConfig(id="gitee", name="Gitee", usage="login",
                auth_flow="pkce_loopback", enabled=True, is_builtin=True, display_order=2,
                authorize_url="https://gitee.com/oauth/authorize",
                token_url="https://gitee.com/oauth/token",
                userinfo_url="https://gitee.com/api/v5/user",
                scopes=["user_info"], claim_sub="id", claim_name="login", pkce=False),
            OAuthProviderConfig(id="azure_ad", name="Azure AD", usage="login",
                auth_flow="oidc_discovery", enabled=False, is_builtin=True, display_order=3,
                authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
                userinfo_url="https://graph.microsoft.com/oidc/userinfo",
                scopes=["openid","profile","email"], claim_sub="sub"),
            # Model OAuth
            OAuthProviderConfig(id="ontoweb", name="ONTOWEB Portal", usage="model",
                auth_flow="device_code", enabled=False, is_builtin=True, display_order=4,
                token_storage="credential_pool",
                scopes=["inference:invoke"], claim_sub="sub"),
            OAuthProviderConfig(id="openai_codex", name="OpenAI Codex", usage="model",
                auth_flow="pkce_loopback", enabled=False, is_builtin=True, display_order=5,
                token_storage="credential_pool",
                scopes=["read:user"], claim_sub="id"),
            OAuthProviderConfig(id="xai", name="xAI Grok", usage="model",
                auth_flow="oidc_discovery", enabled=False, is_builtin=True, display_order=6,
                token_storage="credential_pool",
                scopes=["openid","profile"], claim_sub="sub"),
            OAuthProviderConfig(id="gemini", name="Gemini Code Assist", usage="model",
                auth_flow="pkce_loopback", enabled=False, is_builtin=True, display_order=7,
                token_storage="credential_pool",
                scopes=["openid","profile","email"], claim_sub="sub"),
            OAuthProviderConfig(id="qwen", name="Qwen OAuth", usage="model",
                auth_flow="pkce_loopback", enabled=False, is_builtin=True, display_order=8,
                token_storage="credential_pool",
                scopes=["openid"], claim_sub="sub"),
            OAuthProviderConfig(id="anthropic", name="Anthropic (Claude)", usage="model",
                auth_flow="pkce_loopback", enabled=False, is_builtin=True, display_order=9,
                token_storage="credential_pool",
                scopes=["user:inference"], claim_sub="sub"),
        ]
        result = []
        for p in builtins:
            if usage and p.usage not in (usage, "both"):
                continue
            if enabled_only and not p.enabled:
                continue
            result.append(p)
        return result

    import secrets as _secrets

    def _start_pkce_loopback(
        self, provider: OAuthProviderConfig, usage: str, **kwargs
    ) -> tuple[OAuthSession | None, str | None]:
        if provider.pkce:
            if _HAS_RUST_CRYPTO:
                verifier, challenge = _rust_pkce_challenge()
            else:
                import hashlib, base64
                verifier = self._secrets.token_urlsafe(64)[:64]
                digest = hashlib.sha256(verifier.encode()).digest()
                challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        else:
            verifier = ""
            challenge = ""
        state = (
            _rust_secure_hex(16) if _HAS_RUST_CRYPTO
            else self._secrets.token_hex(16)
        )
        redirect_uri = kwargs.get("redirect_uri", "http://127.0.0.1:18923/callback")

        params = {
            "client_id": provider.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "scope": " ".join(provider.scopes),
        }
        if challenge:
            params["code_challenge"] = challenge
            params["code_challenge_method"] = "S256"

        qs = "&".join(f"{k}={_url_quote(str(v))}" for k, v in params.items())
        auth_url = f"{provider.authorize_url}?{qs}"

        session = OAuthSession(
            provider_id=provider.id,
            state=state,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            usage=usage,
            created_at=time.time(),
        )
        return session, auth_url

    def _start_device_code(
        self, provider: OAuthProviderConfig, usage: str
    ) -> tuple[OAuthSession | None, str | None]:
        import urllib.request, json as _json
        data = _json.dumps({
            "client_id": provider.client_id,
            "scope": " ".join(provider.scopes),
        }).encode()
        req = urllib.request.Request(
            provider.device_code_url or provider.token_url,
            data=data, headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = _json.loads(resp.read())
        except Exception:
            return None, "Device code request failed"

        session = OAuthSession(
            provider_id=provider.id,
            state="",
            code_verifier="",
            redirect_uri="",
            usage=usage,
            created_at=time.time(),
        )
        # Store device_code in session metadata for polling
        session.state = result.get("device_code", "")
        return session, result.get("verification_uri_complete", result.get("verification_uri", ""))

    def _exchange_code(
        self, provider: OAuthProviderConfig, code: str, redirect_uri: str, verifier: str
    ) -> dict | None:
        import urllib.request, json as _json
        data = {
            "client_id": provider.client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        if verifier:
            data["code_verifier"] = verifier
        if provider.client_secret_encrypted:
            try:
                from agent.oauth.storage import decrypt_token
                data["client_secret"] = decrypt_token(provider.client_secret_encrypted)
            except Exception:
                pass  # Fall through without secret — public clients may still work

        try:
            req = urllib.request.Request(
                provider.token_url,
                data=_url_encode(data).encode(),
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return _json.loads(resp.read())
        except Exception:
            return None

    def _extract_claims(self, provider: OAuthProviderConfig, token_resp: dict) -> dict:
        """Extract standardized claims from token response."""
        claims = {"sub": token_resp.get("sub", ""),
                  "email": token_resp.get("email", ""),
                  "name": token_resp.get("name", "")}
        # Try userinfo endpoint
        access_token = token_resp.get("access_token", "")
        if access_token and provider.userinfo_url:
            try:
                import urllib.request, json as _json
                req = urllib.request.Request(
                    provider.userinfo_url,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    info = _json.loads(resp.read())
                    claims["sub"] = info.get(provider.claim_sub, claims["sub"])
                    claims["email"] = info.get(provider.claim_email, claims["email"])
                    claims["name"] = info.get(provider.claim_name, claims["name"])
            except Exception:
                pass
        return claims
