"""WebUI-native members/teams API (embeds intellect-agent membership stack).

Routes mirror ``intellect_cli.dashboard_members_api`` but run on the WebUI
``http.server`` handler. OAuth callbacks and member sessions use the same
cookies and ``{INTELLECT_HOME}/.member-sessions`` files as the agent dashboard
— no HTTP proxy to port 9009.
"""

from __future__ import annotations

import http.cookies
import json
import logging
import os
import re
import threading
import time
from types import SimpleNamespace
from typing import Any, Optional

_MIN_PASSWORD_LENGTH = 8
_WEBUI_PLATFORM = "webui"
_ACTIVITY_TOUCH_INTERVAL = 300.0  # seconds between DB presence refreshes


def _webui_worker_count() -> int:
    try:
        from agent.webui_ha import parse_webui_worker_count

        return parse_webui_worker_count()
    except Exception:
        return 1


def _resolve_browser_session_token(session_id: str) -> dict | None:
    """Resolve browser session cookie — DB-first when ``INTELLECT_WEBUI_WORKERS>1`` (M4)."""
    if not session_id:
        return None
    if _webui_worker_count() > 1:
        store = _store()
        try:
            resolve = getattr(store, "resolve_member_id_by_external_id", None)
            if callable(resolve):
                member_id = resolve(_WEBUI_PLATFORM, session_id)
                if member_id:
                    return {"member_id": member_id, "session_id": session_id}
        finally:
            store.close()
        return None
    try:
        from agent.member_session import resolve_member_session

        return resolve_member_session(session_id)
    except ImportError:
        return None


# ── member_id cache (TTL 60s) ───────────────────────────────────────────────
_member_id_cache: dict[str, tuple[str, float]] = {}  # raw_cookie → (hex_id, expiry)
_activity_touch_at: dict[tuple[str, str], float] = {}  # (member_id, session_token) → monotonic


def _resolve_or_create_member(store, display_name: str) -> str:
    """Find or create a member by display_name (used as login_name).

    Returns the agent-generated hex member_id.
    Raises ValueError if display_name is invalid or member is disabled.
    """
    _validate_display_name(display_name)
    row = store.get_member_by_login(display_name)
    if row:
        if row.get("enabled") != 1:
            raise ValueError(f"Member {display_name!r} is not active")
        return row["id"]
    mid = store.create_member(
        display_name=display_name,
        login_name=display_name,
        platform="webui",
    )
    if not mid:
        raise ValueError("Failed to create member")
    from agent.membership import ensure_member_dirs
    ensure_member_dirs(mid)
    return mid


def _validate_display_name(raw: str) -> str:
    """Validate a display name. Returns normalized name or raises ValueError."""
    if not raw or not isinstance(raw, str):
        raise ValueError("display_name must be a non-empty string")
    v = raw.strip()
    if not v:
        raise ValueError("display_name must not be blank")
    if len(v) > 128:
        raise ValueError("display_name too long (max 128 characters)")
    if not re.match(r'^[\w\s.\-@+\'()ऀ-ॿ฀-๿‘’·一-鿿぀-ゟ゠-ヿ가-힯]{1,128}$', v):
        raise ValueError("display_name contains invalid characters")
    return v
from urllib.parse import parse_qs, urlparse

from api.helpers import MAX_BODY_BYTES, bad, j_with_cookies, login_redirect_location, redirect, read_body
from api.helpers import j as json_response

logger = logging.getLogger(__name__)

_TEAM_HEADER = "X-Intellect-Team"
_PROJECT_HEADER = "X-Intellect-Project"
_TOKEN_PREFIX = "imt_"
_COOKIE_PATH = "/"

_tls = threading.local()

_MEMBER_PUBLIC_PREFIXES = (
    "/api/members/status",
    "/api/members/oauth/",
    "/api/members/register",
)

_MEMBER_PUBLIC_EXACT = frozenset({
    "/api/members/oauth/providers",
    "/api/members/register/check",
    "/api/members/register/pending",
    "/api/members/register/local",
    "/api/members/register",
    "/api/members/login",
})


class _WebUIRequest:
    """Minimal request adapter for agent.members_oauth callback_base_url()."""

    def __init__(self, handler, parsed) -> None:
        self._handler = handler
        self._parsed = parsed
        self.headers = handler.headers
        self.client = SimpleNamespace(host=(handler.client_address or ("127.0.0.1", 0))[0])
        self.app = SimpleNamespace(state=SimpleNamespace(url_prefix="", bound_host="127.0.0.1"))
        host = (handler.headers.get("Host") or "127.0.0.1:9119").split(",")[0].strip()
        scheme = "https" if handler.headers.get("X-Forwarded-Proto", "").strip().lower() == "https" else "http"
        self.url = SimpleNamespace(scheme=scheme, netloc=host)

    @property
    def query_params(self) -> SimpleNamespace:
        raw = parse_qs(self._parsed.query or "", keep_blank_values=True)

        class _QP:
            def get(self, key: str, default=None):
                vals = raw.get(key)
                if not vals:
                    return default
                return vals[0]

        return _QP()

    @property
    def cookies(self) -> dict[str, str]:
        out: dict[str, str] = {}
        header = self.headers.get("Cookie", "")
        if not header:
            return out
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(header)
        except http.cookies.CookieError:
            return out
        for key in jar:
            out[key] = jar[key].value
        return out


def agent_membership_available() -> bool:
    try:
        from api.config import _INTELLECT_FOUND

        if not _INTELLECT_FOUND:
            return False
        from agent.membership import MembershipStore  # noqa: F401

        return True
    except Exception:
        return False


def load_members_config() -> dict[str, Any]:
    """Public config loader for auth/login integration."""
    return _load_config()


def member_session_cookie_lines(member_id: str) -> list[str]:
    """Cookie lines for a new member browser session."""
    from agent.members_oauth import get_oauth_config

    config = _load_config()
    oauth_cfg = get_oauth_config(config)
    ttl = float(oauth_cfg.get("session_ttl_hours") or 168)
    return _session_cookie_lines(member_id, ttl_hours=ttl)


def is_loopback_client(handler) -> bool:
    from api.auth import is_loopback_client as _loopback

    return _loopback(handler)


def _load_config() -> dict[str, Any]:
    from intellect_cli.config import load_config

    return load_config()


def local_registration_requires_approval(config: Optional[dict[str, Any]] = None) -> bool:
    """Whether local self-registration (admin approval) is enabled for this WebUI."""
    from api.config import load_settings
    from agent.membership import get_registration_config

    settings = load_settings()
    if "members_local_requires_approval" in settings:
        return bool(settings.get("members_local_requires_approval"))
    cfg = config if config is not None else _load_config()
    return bool(get_registration_config(cfg).get("local_requires_approval", True))


def _store():
    from agent.membership import MembershipStore

    return MembershipStore(config=_load_config())


def _webui_return_to(path: Optional[str]) -> str:
    from agent.members_oauth import sanitize_return_to

    raw = sanitize_return_to(path)
    if raw in ("/chat", "/members", "/teams"):
        return "/"
    return raw


def _morsel(name: str, value: str, *, max_age: int | None = None) -> str:
    c = http.cookies.SimpleCookie()
    c[name] = value
    c[name]["path"] = _COOKIE_PATH
    c[name]["httponly"] = True
    c[name]["samesite"] = "Lax"
    if max_age is not None:
        c[name]["max-age"] = str(max_age)
    return c.output(header="").strip()


def _request_bound_host() -> str:
    return "127.0.0.1"


def _request_client_host(handler) -> str:
    return (handler.client_address or ("127.0.0.1", 0))[0]


_OAUTH_CALLBACK_SUFFIX = "/api/members/oauth/callback"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def oauth_canonical_origin(config: dict[str, Any]) -> str | None:
    """Origin from ``callback_base_url`` when it targets loopback (dev OAuth)."""
    from agent.members_oauth import get_oauth_config, is_oauth_enabled

    if not is_oauth_enabled(config):
        return None
    oauth = get_oauth_config(config)
    override = _normalize_callback_base_url(str(oauth.get("callback_base_url") or ""))
    if not override:
        return None
    from urllib.parse import urlparse

    parsed = urlparse(override)
    host = (parsed.hostname or "").lower().strip("[]")
    if host not in _LOOPBACK_HOSTS:
        return None
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    hostport = host if port in (80, 443) else f"{host}:{port}"
    return f"{parsed.scheme}://{hostport}"


def _request_origin(handler) -> str:
    host = (handler.headers.get("Host") or "").split(",")[0].strip()
    if not host:
        return ""
    scheme = (
        "https"
        if handler.headers.get("X-Forwarded-Proto", "").strip().lower() == "https"
        else "http"
    )
    return f"{scheme}://{host}"


def oauth_loopback_host_mismatch(config: dict[str, Any], handler) -> str | None:
    """Return canonical origin when loopback Host differs from OAuth callback_base_url."""
    canon = oauth_canonical_origin(config)
    if not canon:
        return None
    req = _request_origin(handler)
    if not req:
        return None
    from urllib.parse import urlparse

    req_p = urlparse(req)
    canon_p = urlparse(canon)
    req_host = (req_p.hostname or "").lower().strip("[]")
    canon_host = (canon_p.hostname or "").lower().strip("[]")
    if req_host not in _LOOPBACK_HOSTS or canon_host not in _LOOPBACK_HOSTS:
        return None
    req_port = req_p.port or (443 if req_p.scheme == "https" else 80)
    canon_port = canon_p.port or (443 if canon_p.scheme == "https" else 80)
    if req_host == canon_host and req_port == canon_port:
        return None
    return canon


def maybe_redirect_oauth_canonical_host(handler, parsed) -> bool:
    """Redirect GET/HEAD to callback_base_url host (localhost vs 127.0.0.1)."""
    if handler.command not in ("GET", "HEAD"):
        return False
    path = parsed.path or ""
    if path in ("/health", "/api/csp-report", "/api/health/agent"):
        return False
    try:
        config = _load_config()
        canon = oauth_loopback_host_mismatch(config, handler)
        if not canon:
            return False
        target = canon.rstrip("/") + (path or "/")
        if parsed.query:
            target += "?" + parsed.query
        redirect(handler, target)
        return True
    except Exception:
        logger.debug("OAuth canonical host redirect skipped", exc_info=True)
        return False


def _normalize_callback_base_url(raw: str) -> str:
    """Strip trailing slash and accidental full callback path from config."""
    base = str(raw or "").strip().rstrip("/")
    lower = base.lower()
    suffix = _OAUTH_CALLBACK_SUFFIX.lower()
    if lower.endswith(suffix):
        base = base[: -len(_OAUTH_CALLBACK_SUFFIX)].rstrip("/")
    return base


def _host_port_pair(host_or_url: str) -> tuple[str, int | None]:
    from urllib.parse import urlparse

    raw = str(host_or_url or "").strip()
    if not raw:
        return "", None
    if "://" in raw:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        return host, parsed.port
    if raw.startswith("[") and "]" in raw:
        host = raw[1 : raw.index("]")].lower()
        rest = raw[raw.index("]") + 1 :]
        if rest.startswith(":"):
            port_s = rest[1:].split(",")[0].strip()
            return host, int(port_s) if port_s.isdigit() else None
        return host, None
    if ":" in raw:
        host, port_s = raw.rsplit(":", 1)
        return host.lower(), int(port_s) if port_s.isdigit() else None
    return raw.lower(), None


def _canonical_loopback_netloc(host_header: str) -> str:
    """Normalize loopback Host for OAuth redirect_uri (Gitee treats localhost ≠ 127.0.0.1)."""
    host = (host_header or "localhost:9119").split(",")[0].strip()
    h, port = _host_port_pair(host)
    if h in {"127.0.0.1", "::1"}:
        h = "localhost"
    return f"{h}:{port}" if port else h


def _request_public_base(handler, parsed) -> str:
    req = _WebUIRequest(handler, parsed)
    return f"{req.url.scheme}://{_canonical_loopback_netloc(handler.headers.get('Host') or '')}".rstrip("/")


def _provider_to_member_dict(p) -> dict[str, Any]:
    """Convert an OAuth provider (engine config or legacy dict) to the member-facing shape."""
    # OAuthEngine OAuthProviderConfig
    if hasattr(p, "id") and hasattr(p, "name"):
        return {
            "id": p.id,
            "display_name": p.name,
            "type": p.auth_flow,
            "auth_flow": p.auth_flow,
            "scopes": p.scopes,
            "logo_svg": p.logo_svg,
            "description": p.description,
        }
    # Legacy dict from members_oauth
    return {
        "id": p.get("id", ""),
        "display_name": p.get("display_name", p.get("id", "")),
        "type": p.get("type", p.get("auth_flow", "")),
    }


def _oauth_engine(config: dict[str, Any], store) -> Any:
    from agent.oauth import OAuthEngine  # type: ignore[import-not-found]

    return OAuthEngine(config=config, db=store)


def _oauth_get_login_provider(
    config: dict[str, Any],
    provider_id: str,
    store,
) -> Any | None:
    from agent.oauth.provider_resolution import normalize_provider_id  # type: ignore[import-not-found]

    engine = _oauth_engine(config, store)
    return engine.get_provider(normalize_provider_id(provider_id))


def _oauth_build_authorize_url(
    config: dict[str, Any],
    provider_id: str,
    *,
    store,
    redirect_uri: str,
    state: str,
    code_challenge: str | None,
) -> tuple[str | None, str | None]:
    """Return (authorization_url, error_code)."""
    from agent.oauth.login_flow import (  # type: ignore[import-not-found]
        build_authorization_url,
        provider_login_ready,
    )

    cfg = _oauth_get_login_provider(config, provider_id, store)
    if not cfg:
        return None, "unknown_provider"
    if not cfg.enabled:
        return None, "provider_disabled"
    if not provider_login_ready(cfg):
        return None, "incomplete_credentials"
    url = build_authorization_url(cfg, redirect_uri, state, code_challenge)
    if not url:
        return None, "build_failed"
    return url, None


def _oauth_exchange_and_claims(
    config: dict[str, Any],
    provider_id: str,
    *,
    store,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    from agent.oauth.login_flow import (  # type: ignore[import-not-found]
        exchange_authorization_code,
        extract_login_claims,
    )

    cfg = _oauth_get_login_provider(config, provider_id, store)
    if not cfg:
        return None, None
    from agent.members_oauth import get_provider_secret
    from agent.oauth.provider_resolution import config_to_members_dict

    provider_dict = config_to_members_dict(cfg)
    if not get_provider_secret(provider_dict):
        return None, None
    verifier = code_verifier or None
    if not cfg.pkce:
        verifier = None
    tokens = exchange_authorization_code(
        cfg,
        code,
        redirect_uri,
        code_verifier=verifier,
    )
    if not tokens:
        return None, None
    claims = extract_login_claims(cfg, tokens)
    return tokens, claims


_OAUTH_ERROR_CODES = frozenset({
    "token_exchange_failed",
    "missing_claims",
    "bind_failed",
    "missing_client_secret",
    "missing_code",
    "invalid_state",
    "unknown_provider",
    "link_member_not_found",
    "oauth_denied",
})


def _oauth_error_code(message: str) -> str:
    """Map provider/debug text to a short stable oauth_error code for the UI."""
    code = str(message or "").strip()
    if code in _OAUTH_ERROR_CODES:
        return code
    low = code.lower()
    if "access_denied" in low or "denied" in low:
        return "oauth_denied"
    if "missing_client_secret" in low:
        return "missing_client_secret"
    if "invalid_state" in low:
        return "invalid_state"
    if "missing_claims" in low or "missing claims" in low:
        return "missing_claims"
    if "bind_failed" in low:
        return "bind_failed"
    return "token_exchange_failed"


def _oauth_error_redirect_path(payload: dict[str, Any] | None, *,
                                message: str = "",
                                prefer_register: bool = False) -> str:
    """Build redirect URL for OAuth errors."""
    from urllib.parse import urlencode

    message = _oauth_error_code(message)

    link_member_id = str((payload or {}).get("link_member_id") or "").strip()
    if link_member_id:
        params: dict[str, str] = {
            "panel": "members",
            "membersSection": "identities",
        }
        if message:
            params["oauth_error"] = message
        return f"/?{urlencode(params)}"

    params = {"error": message} if message else {}
    base = "/register" if prefer_register else "/login"
    qs = urlencode(params)
    return f"{base}?{qs}" if qs else base


def _oauth_link_return_to(raw: str | None) -> str:
    """Default post-bind landing path when none was supplied."""
    path = _sanitize_return_to(raw)
    if path in ("", "/"):
        return "/?panel=members&membersSection=identities"
    return path


def _oauth_link_success_return_to(payload: dict[str, Any] | None) -> str:
    """Landing URL after a successful identity-link OAuth callback."""
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    raw = _oauth_link_return_to(str((payload or {}).get("return_to") or ""))
    parsed = urlparse(raw)
    pairs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    pairs["panel"] = "members"
    pairs["membersSection"] = "identities"
    pairs["oauth"] = "linked"
    qs = urlencode(pairs)
    path = parsed.path if parsed.path else "/"
    return f"{path}?{qs}" if qs else path


def _sanitize_return_to(raw: str | None) -> str:
    """Sanitize return_to to prevent open redirect."""
    if not raw:
        return "/"
    from urllib.parse import urlparse
    parsed = urlparse(str(raw))
    if parsed.netloc:
        return "/"
    return str(raw) or "/"


def _oauth_platform(provider_id: str) -> str:
    """Normalize provider_id to a platform key for identity storage."""
    return f"oauth:{provider_id}"


def webui_oauth_callback_uri(config: dict[str, Any], handler, parsed) -> str:
    """OAuth redirect_uri for WebUI (normalizes callback_base_url misconfiguration).

    When ``members.oauth.callback_base_url`` is set, it wins over loopback Host
    canonicalization so authorize + token exchange match Gitee/GitHub registration
    (localhost and 127.0.0.1 are different redirect URIs).
    """
    from agent.members_oauth import get_oauth_config

    from api.auth import is_loopback_client

    oauth = get_oauth_config(config)
    override = _normalize_callback_base_url(str(oauth.get("callback_base_url") or ""))
    if override:
        return f"{override.rstrip('/')}{_OAUTH_CALLBACK_SUFFIX}"

    if is_loopback_client(handler):
        return f"{_request_public_base(handler, parsed)}{_OAUTH_CALLBACK_SUFFIX}"

    req = _WebUIRequest(handler, parsed)
    host = (handler.headers.get("Host") or "localhost").split(",")[0].strip()
    origin = f"{req.url.scheme}://{host}"
    return f"{origin.rstrip('/')}{_OAUTH_CALLBACK_SUFFIX}"


def _oauth_status_fields(handler, parsed, config: dict[str, Any]) -> dict[str, Any]:
    try:
        from agent.members_oauth import (
            dashboard_member_login_required,
            get_oauth_config,
            is_oauth_enabled,
            is_trusted_header_enabled,
            oauth_require_for_dashboard,
        )
    except ImportError:
        return {
            "oauth_enabled": False,
            "oauth_required": False,
            "member_login_required": False,
            "allow_picker_on_localhost": False,
            "trusted_header_enabled": False,
            "oauth_providers": [],
        }

    oauth_on = is_oauth_enabled(config)
    oauth_cfg = get_oauth_config(config)
    required = oauth_require_for_dashboard(
        config,
        bound_host=_request_bound_host(),
        client_host=_request_client_host(handler),
    )
    members_on = dashboard_member_login_required(config)
    picker_allowed = (
        bool(oauth_cfg.get("allow_picker_on_localhost", False))
        and not members_on
        and not required
    )
    # Try unified OAuthEngine first for richer provider data (DB + config + builtins)
    providers = []
    if oauth_on:
        try:
            from agent.oauth import OAuthEngine  # type: ignore[import-not-found]
            from agent.oauth.login_flow import provider_login_ready  # type: ignore[import-not-found]

            store = _store()
            try:
                engine = OAuthEngine(config=config, db=store)
                for p in engine.list_providers(usage="login", enabled_only=True):
                    if provider_login_ready(p):
                        providers.append(_provider_to_member_dict(p))
            finally:
                store.close()
        except Exception:
            try:
                from agent.members_oauth import list_enabled_providers

                providers = list_enabled_providers(config)
            except Exception:
                pass

    fields = {
        "oauth_enabled": oauth_on,
        "oauth_required": required,
        "member_login_required": members_on,
        "allow_picker_on_localhost": picker_allowed,
        "trusted_header_enabled": is_trusted_header_enabled(config),
        "oauth_providers": providers,
    }
    if oauth_on:
        fields["oauth_callback_uri"] = webui_oauth_callback_uri(config, handler, parsed)
        canon = oauth_canonical_origin(config)
        fields["oauth_canonical_origin"] = canon or ""
        fields["oauth_host_mismatch"] = bool(oauth_loopback_host_mismatch(config, handler))
    return fields


def _try_resolve_trusted_header_member(handler, config: dict[str, Any]) -> Optional[str]:
    from agent.members_oauth import (
        OAuthMemberNotLinkedError,
        get_oauth_config,
        is_trusted_header_enabled,
        resolve_trusted_header_member,
    )

    if not is_trusted_header_enabled(config):
        return None
    th_cfg = get_oauth_config(config).get("trusted_header", {})
    # Only allow trusted header from loopback or common Docker bridge networks.
    # Docker reverse proxies on the default bridge appear as 172.17.x.x.
    client_host = _request_client_host(handler)
    if client_host not in ("127.0.0.1", "::1", "localhost") and not client_host.startswith("172.17."):
        return None
    header_name = str(th_cfg.get("header") or "X-Forwarded-User").strip()
    if not header_name:
        return None
    raw = handler.headers.get(header_name, "").strip()
    if not raw:
        return None
    store = _store()
    try:
        return resolve_trusted_header_member(header_value=raw, config=config, db=store)
    except OAuthMemberNotLinkedError:
        return None
    except Exception as exc:
        logger.debug("trusted header auth rejected: %s", exc)
        logger.exception("Unexpected error in trusted header auth")
        return None
    finally:
        store.close()


def resolve_member_id(handler, parsed) -> Optional[str]:
    if not agent_membership_available():
        return None
    try:
        from agent.member_session import member_session_cookie_name
        from agent.members_oauth import is_oauth_enabled, oauth_require_for_dashboard
        from intellect_cli.members_http import member_cookie_name
        from agent.membership import validate_member_id
    except ImportError:
        return None

    config = _load_config()
    req = _WebUIRequest(handler, parsed)
    session_id = req.cookies.get(member_session_cookie_name(), "").strip()
    if session_id:
        data = _resolve_browser_session_token(session_id)
        if data:
            try:
                return validate_member_id(str(data["member_id"]))
            except ValueError:
                pass

    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        raw = auth[7:].strip()
        if raw.startswith(_TOKEN_PREFIX):
            store = _store()
            try:
                return store.resolve_api_token(raw)
            finally:
                store.close()

    trusted = _try_resolve_trusted_header_member(handler, config)
    if trusted:
        return trusted

    if oauth_require_for_dashboard(
        config,
        bound_host=_request_bound_host(),
        client_host=_request_client_host(handler),
    ) and is_oauth_enabled(config):
        return None

    cookie = req.cookies.get(member_cookie_name(), "").strip()
    if cookie:
        now = time.time()
        cached = _member_id_cache.get(cookie)
        if cached is not None and cached[1] > now:
            return cached[0] if cached[0] != "__none__" else None
        mid = None
        try:
            mid = validate_member_id(cookie)
        except ValueError:
            pass
        if mid:
            store = _store()
            try:
                row = store.get_member(mid)
                if row and row.get("enabled") == 1:
                    _member_id_cache[cookie] = (mid, now + 60)
                    return mid
                # Fallback: cookie value might be a login_name (old slug)
                row = store.get_member_by_login(mid)
                if row and row.get("enabled") == 1:
                    result = row["id"]
                    _member_id_cache[cookie] = (result, now + 60)
                    return result
            finally:
                store.close()
        _member_id_cache[cookie] = ("__none__", now + 60)
    # Do not fall back to read_dashboard_session() here. That file is a single
    # global slot (last localhost picker / OAuth login) and breaks per-request
    # member isolation when multiple clients or sequential test logins share one
    # WebUI process. Identity must come from this request's cookies/headers only.
    return None


def _member_authenticated_via_session_cookie(handler, parsed) -> bool:
    """True when this request carries a valid member browser session cookie."""
    from agent.member_session import member_session_cookie_name

    req = _WebUIRequest(handler, parsed)
    session_id = req.cookies.get(member_session_cookie_name(), "").strip()
    if not session_id:
        return False
    data = _resolve_browser_session_token(session_id)
    return bool(data and data.get("member_id"))


def _member_password_fields(store, member_id: str) -> dict[str, Any]:

    has_password = store.member_has_password(member_id)
    return {
        "member_has_password": has_password,
        "password_change_required": store.password_change_required(member_id),
        "min_password_length": _MIN_PASSWORD_LENGTH,
    }


def _password_setup_allowed_path(path: str, method: str) -> bool:
    if path == "/api/members/status" and method == "GET":
        return True
    if path == "/api/members/me/password" and method == "POST":
        return True
    if path == "/api/members/me/identities/link" and method in ("GET", "POST"):
        return True
    if path.startswith("/api/members/oauth/") and method == "GET":
        return True
    if path == "/api/members/session" and method in ("DELETE",):
        return True
    if path == "/api/auth/logout" and method == "POST":
        return True
    if path.startswith("/static/"):
        return True
    if path in ("/", "/index.html", "/health", "/favicon.ico", "/sw.js"):
        return True
    return False


def _member_password_change_blocks_request(handler, parsed, actor: str) -> bool:
    if not _member_authenticated_via_session_cookie(handler, parsed):
        return False
    store = _store()
    try:
        return store.password_change_required(actor)
    finally:
        store.close()


def resolve_team_id(handler, parsed, *, member_id: Optional[str] = None) -> Optional[str]:
    from intellect_cli.members_http import team_cookie_name
    from agent.membership import validate_team_id

    header = handler.headers.get(_TEAM_HEADER, "").strip()
    if header:
        try:
            tid = validate_team_id(header)
        except ValueError:
            return None
        if member_id and not _member_has_team(member_id, tid):
            return None
        return tid
    req = _WebUIRequest(handler, parsed)
    cookie = req.cookies.get(team_cookie_name(), "").strip()
    if cookie:
        try:
            tid = validate_team_id(cookie)
        except ValueError:
            return None
        if member_id and not _member_has_team(member_id, tid):
            return None
        return tid
    return None


def _member_has_team(member_id: str, team_id: str) -> bool:
    store = _store()
    try:
        m = store.get_membership(team_id, member_id)
        return bool(m and m.get("status") == "active")
    finally:
        store.close()


def _record_db_member_session(store, member_id: str, external_id: str) -> None:
    """Mirror WebUI login into state.db ``member_sessions`` (best-effort)."""
    if not member_id or not external_id:
        return
    try:
        record = getattr(store, "record_session", None)
        if callable(record):
            record(
                member_id,
                _WEBUI_PLATFORM,
                session_type="login",
                external_id=external_id,
            )
    except Exception:
        logger.debug("record_session failed for member %s", member_id, exc_info=True)


def _end_db_member_session(store, member_id: str, external_id: str = "") -> None:
    """End WebUI DB presence row on logout (best-effort)."""
    if not member_id:
        return
    try:
        end = getattr(store, "end_session", None)
        if callable(end):
            end(member_id, _WEBUI_PLATFORM, external_id=external_id or "")
    except Exception:
        logger.debug("end_session failed for member %s", member_id, exc_info=True)


def _touch_db_member_activity(store, member_id: str, external_id: str = "") -> None:
    """Refresh DB online status while the browser session cookie is valid."""
    if not member_id:
        return
    key = (member_id, external_id or "")
    now = time.time()
    last = _activity_touch_at.get(key, 0.0)
    if now - last < _ACTIVITY_TOUCH_INTERVAL:
        return
    try:
        touch = getattr(store, "update_activity", None)
        if callable(touch):
            touch(member_id, _WEBUI_PLATFORM, external_id=external_id or "")
            _activity_touch_at[key] = now
    except Exception:
        logger.debug("update_activity failed for member %s", member_id, exc_info=True)


def _session_cookie_lines(
    member_id: str,
    *,
    provider_id: str = "",
    external_id: str = "",
    ttl_hours: float = 168,
) -> list[str]:
    from intellect_cli.members_http import member_cookie_name
    from agent.member_session import create_member_session, member_session_cookie_name

    session_id = create_member_session(
        member_id,
        provider_id=provider_id,
        external_id=external_id,
        ttl_hours=ttl_hours,
    )
    try:
        store = _store()
        try:
            _record_db_member_session(store, member_id, session_id)
        finally:
            store.close()
    except Exception:
        logger.debug("DB member session record skipped", exc_info=True)
    max_age = int(max(float(ttl_hours), 0.01) * 3600)
    lines = [
        _morsel(member_cookie_name(), member_id, max_age=60 * 60 * 24 * 365),
        _morsel(member_session_cookie_name(), session_id, max_age=max_age),
    ]
    return lines


def _clear_session_cookie_lines(handler) -> list[str]:
    from intellect_cli.members_http import member_cookie_name, project_cookie_name, team_cookie_name
    from agent.member_session import delete_member_session, member_session_cookie_name, resolve_member_session
    req = _WebUIRequest(handler, urlparse(handler.path))
    sid = req.cookies.get(member_session_cookie_name(), "").strip()
    member_id = req.cookies.get(member_cookie_name(), "").strip() or None
    if sid:
        data = resolve_member_session(sid)
        if data and data.get("member_id"):
            member_id = str(data["member_id"])
    if member_id:
        try:
            store = _store()
            try:
                _end_db_member_session(store, member_id, sid)
            finally:
                store.close()
        except Exception:
            logger.debug("DB member session end skipped", exc_info=True)
        _activity_touch_at.pop((member_id, sid or ""), None)
    if sid:
        delete_member_session(sid)
    expired = "Max-Age=0"
    return [
        f"{member_cookie_name()}=; Path={_COOKIE_PATH}; HttpOnly; SameSite=Lax; {expired}",
        f"{member_session_cookie_name()}=; Path={_COOKIE_PATH}; HttpOnly; SameSite=Lax; {expired}",
        f"{team_cookie_name()}=; Path={_COOKIE_PATH}; HttpOnly; SameSite=Lax; {expired}",
        f"{project_cookie_name()}=; Path={_COOKIE_PATH}; HttpOnly; SameSite=Lax; {expired}",
    ]


def _team_cookie_line(team_id: str) -> str:
    from intellect_cli.members_http import team_cookie_name

    return _morsel(team_cookie_name(), team_id, max_age=60 * 60 * 24 * 365)


def resolve_project_id(handler, parsed, *, member_id: Optional[str] = None) -> Optional[str]:
    from intellect_cli.members_http import project_cookie_name
    from agent.membership import validate_project_id

    header = handler.headers.get(_PROJECT_HEADER, "").strip()
    if header:
        try:
            pid = validate_project_id(header)
        except ValueError:
            return None
        if member_id and not _member_has_project(member_id, pid):
            return None
        return pid
    req = _WebUIRequest(handler, parsed)
    cookie = req.cookies.get(project_cookie_name(), "").strip()
    if cookie:
        try:
            pid = validate_project_id(cookie)
        except ValueError:
            return None
        if member_id and not _member_has_project(member_id, pid):
            return None
        return pid
    return None


def _member_has_project(member_id: str, project_id: str) -> bool:
    store = _store()
    try:
        m = store.get_project_membership(project_id, member_id)
        return bool(m and m.get("status") == "active")
    finally:
        store.close()


def _project_cookie_line(project_id: str) -> str:
    from intellect_cli.members_http import project_cookie_name

    return _morsel(project_cookie_name(), project_id, max_age=60 * 60 * 24 * 365)


def _member_is_project_admin_on_any(store, member_id: str) -> bool:
    row = store.conn.execute(
        "SELECT 1 FROM project_memberships "
        "WHERE member_id=? AND role='project_admin' AND joined_at IS NOT NULL LIMIT 1",
        (member_id,),
    ).fetchone()
    return row is not None


def _member_is_team_admin_on_any(store, member_id: str) -> bool:
    row = store.conn.execute(
        "SELECT 1 FROM team_memberships "
        "WHERE member_id=? AND role='admin' AND joined_at IS NOT NULL LIMIT 1",
        (member_id,),
    ).fetchone()
    return row is not None


def _resolve_bound_project_id(
    handler,
    parsed,
    *,
    member_id: str | None,
    team_id: str | None,
    config: dict,
    store,
) -> str | None:
    """Cookie/header project, then agent 8-step resolution (single-project auto-select, etc.)."""
    from agent.membership import is_projects_enabled
    from agent.runtime_context import resolve_project_id as agent_resolve_project_id

    if not member_id or not is_projects_enabled(config):
        return None
    pid = resolve_project_id(handler, parsed, member_id=member_id)
    if pid:
        return pid
    headers = {k: v for k, v in handler.headers.items()}
    return agent_resolve_project_id(
        config=config,
        member_id=member_id,
        team_id=team_id,
        headers=headers,
        db=store,
    )


def build_runtime_context(
    *,
    member_id: str | None,
    team_id: str | None = None,
    project_id: str | None = None,
    config: dict | None = None,
    platform: str = "webui",
) -> Any:
    """Build a full RuntimeContext (cwd + project workspace) for WebUI agent runs."""
    from agent.runtime_context import (
        RuntimeContext,
        resolve_effective_member_id,
        resolve_project_workspace,
        resolve_terminal_cwd,
    )

    if not member_id:
        return None
    config = config or _load_config()
    member_resolved = resolve_effective_member_id(
        config=config, explicit_member_id=member_id,
    )
    base = RuntimeContext(
        member_id=member_resolved,
        team_id=team_id,
        project_id=project_id,
        platform=platform,
    )
    pws = resolve_project_workspace(project_id, config) if project_id else None
    return RuntimeContext(
        member_id=member_resolved,
        team_id=team_id,
        project_id=project_id,
        platform=platform,
        terminal_cwd=resolve_terminal_cwd(base, config=config),
        project_workspace=pws,
    )


def bind_request_member_context(handler, parsed) -> None:
    """Resolve member/team/project for this HTTP request (thread-local)."""
    _tls.member_id = None
    _tls.team_id = None
    _tls.project_id = None
    _tls.runtime_context = None
    _tls.session_scope = None
    if not agent_membership_available():
        return
    try:
        from agent.membership import is_members_enabled, is_projects_enabled, is_teams_enabled
        from agent.members_team import TeamRequiredError, resolve_member_team_id
    except ImportError:
        return

    config = _load_config()
    if not is_members_enabled(config):
        return
    mid = resolve_member_id(handler, parsed)
    tid = resolve_team_id(handler, parsed, member_id=mid) if mid else None
    store = _store()
    try:
        if tid is None and mid and is_teams_enabled(config):
            try:
                tid = resolve_member_team_id(mid, config, store=store, for_dashboard=True)
            except TeamRequiredError:
                tid = None
        pid = None
        if mid and is_projects_enabled(config):
            pid = _resolve_bound_project_id(
                handler, parsed, member_id=mid, team_id=tid, config=config, store=store,
            )
        ctx = build_runtime_context(member_id=mid, team_id=tid, project_id=pid, config=config)
        _tls.member_id = mid
        _tls.team_id = tid
        _tls.project_id = pid
        _tls.runtime_context = ctx
        if mid:
            try:
                from agent.member_session import member_session_cookie_name

                req = _WebUIRequest(handler, parsed)
                sid = req.cookies.get(member_session_cookie_name(), "").strip()
                _touch_db_member_activity(store, mid, sid)
            except Exception:
                logger.debug("DB member activity touch skipped", exc_info=True)
        try:
            from agent.session_visibility import SessionListScope, resolve_session_list_scope

            _tls.session_scope = resolve_session_list_scope(
                config=config,
                store=store,
                actor_member_id=mid,
                active_team_id=tid,
            )
        except ImportError:
            pass
    finally:
        store.close()


def get_request_session_scope():
    return getattr(_tls, "session_scope", None)


def bind_worker_session_scope(scope) -> None:
    """Install session list scope on the current (worker) thread."""
    _tls.session_scope = scope


def clear_request_member_context() -> None:
    _tls.member_id = None
    _tls.team_id = None
    _tls.project_id = None
    _tls.runtime_context = None
    _tls.session_scope = None


def get_bound_runtime_context():
    return getattr(_tls, "runtime_context", None)


def apply_runtime_context_to_agent(agent, ctx=None) -> None:
    """Attach resolved member/team/project context to an AIAgent instance."""
    if ctx is None:
        ctx = get_bound_runtime_context()
    if ctx and getattr(ctx, "member_id", None):
        agent.runtime_context = ctx


def _resolve_member_role(member_id: str | None) -> str | None:
    if not member_id:
        return None
    store = _store()
    try:
        return _actor_role(store, member_id)
    except Exception:
        return None
    finally:
        store.close()


def push_member_runtime_env(ctx=None) -> dict[str, Optional[str]]:
    """Apply member/team/project + scoped WIKI_* env for agent runs; return prior snapshot."""
    if ctx is None:
        ctx = get_bound_runtime_context()
    old: dict[str, Optional[str]] = {
        "INTELLECT_MEMBER_ID": os.environ.get("INTELLECT_MEMBER_ID"),
        "INTELLECT_TEAM": os.environ.get("INTELLECT_TEAM"),
        "INTELLECT_PROJECT": os.environ.get("INTELLECT_PROJECT"),
    }
    try:
        from agent.runtime_context import inject_wiki_runtime_env, snapshot_wiki_runtime_env

        old.update(snapshot_wiki_runtime_env())
    except Exception:
        pass
    if not ctx or not ctx.member_id:
        return old
    os.environ["INTELLECT_MEMBER_ID"] = str(ctx.member_id)
    if ctx.team_id:
        os.environ["INTELLECT_TEAM"] = str(ctx.team_id)
    else:
        os.environ.pop("INTELLECT_TEAM", None)
    if ctx.project_id:
        os.environ["INTELLECT_PROJECT"] = str(ctx.project_id)
    else:
        os.environ.pop("INTELLECT_PROJECT", None)
    try:
        from agent.runtime_context import inject_wiki_runtime_env

        config = _load_config()
        role = _resolve_member_role(ctx.member_id)
        inject_wiki_runtime_env(ctx, config, actor_role=role)
    except Exception:
        logger.debug("inject_wiki_runtime_env failed", exc_info=True)
    return old


def pop_member_runtime_env(snapshot: dict[str, Optional[str]]) -> None:
    for key in ("INTELLECT_MEMBER_ID", "INTELLECT_TEAM", "INTELLECT_PROJECT"):
        val = snapshot.get(key)
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val
    try:
        from agent.runtime_context import restore_wiki_runtime_env

        restore_wiki_runtime_env(snapshot)
    except Exception:
        for key in (
            "WIKI_PATH", "WIKI_SCOPE", "WIKI_SCOPE_ID",
            "WIKI_WRITE_MODE", "WIKI_SKILL_VERSION", "WIKI_TARGET_SCOPE",
        ):
            val = snapshot.get(key)
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


def _member_public_path(path: str, method: str) -> bool:
    if path in _MEMBER_PUBLIC_EXACT:
        return True
    if method == "GET" and path.startswith("/api/members/oauth/"):
        return True
    if path == "/api/members/status":
        return True
    if path == "/api/members/redeem" and method == "POST":
        return True
    if path == "/api/members/login" and method == "POST":
        return True
    if path.startswith("/api/members/register"):
        return True
    if path == "/api/members/session" and method in ("POST", "DELETE"):
        return True
    return False


def member_access_required(handler, parsed) -> bool:
    """True when an authenticated member must be present for this request."""
    if not agent_membership_available():
        return False
    try:
        from agent.membership import is_members_enabled
        from agent.members_oauth import oauth_require_for_dashboard
    except ImportError:
        return False

    config = _load_config()
    if not is_members_enabled(config):
        return False
    if _member_public_path(parsed.path, handler.command):
        return False
    if (
        parsed.path.startswith("/api/members")
        or parsed.path.startswith("/api/teams")
        or parsed.path.startswith("/api/member-projects")
    ):
        return parsed.path not in ("/api/members/status",) and not parsed.path.startswith(
            "/api/members/oauth/"
        )
    if not oauth_require_for_dashboard(
        config,
        bound_host=_request_bound_host(),
        client_host=_request_client_host(handler),
    ):
        return False
    if parsed.path.startswith("/static/") or parsed.path.startswith("/session/static/"):
        return False
    if parsed.path in ("/", "/index.html", "/login", "/register", "/health", "/favicon.ico", "/sw.js"):
        return False
    if parsed.path.startswith("/session/") and handler.command == "GET":
        return False
    if parsed.path.startswith("/api/auth/"):
        return False
    if parsed.path.startswith("/api/onboarding/"):
        return False
    if parsed.path.startswith("/api/user/"):
        return False
    if not parsed.path.startswith("/api/"):
        return False
    return True


def _is_session_deeplink_path(path: str) -> bool:
    """True for GET /session/<session_id> app-shell routes (not static/manifest)."""
    if not path or not path.startswith("/session/"):
        return False
    tail = path[len("/session/") :].split("?", 1)[0].strip("/")
    if not tail:
        return False
    if tail in ("login", "manifest.json", "manifest.webmanifest"):
        return False
    if tail.startswith("static/"):
        return False
    return "/" not in tail


def check_member_access(handler, parsed) -> bool:
    """Gate API when member OAuth is required. Returns False if response sent."""
    if (
        handler.command == "GET"
        and _is_session_deeplink_path(parsed.path or "")
        and agent_membership_available()
    ):
        from agent.membership import is_members_enabled

        if is_members_enabled(_load_config()) and not resolve_member_id(handler, parsed):
            redirect(handler, login_redirect_location(parsed))
            return False

    if not member_access_required(handler, parsed):
        return True
    actor = resolve_member_id(handler, parsed)
    if actor:
        if _member_password_change_blocks_request(handler, parsed, actor):
            if not _password_setup_allowed_path(parsed.path or "", handler.command):
                json_response(
                    handler,
                    {
                        "error": "Password change required",
                        "error_code": "password_change_required",
                    },
                    status=403,
                )
                return False
        return True
    if parsed.path.startswith("/api/"):
        json_response(handler, {"error": "Member login required"}, status=401)
    else:
        redirect(handler, login_redirect_location(parsed))
    return False


def _actor_role(store, actor: str | None) -> str:
    if not actor:
        return "member"
    row = store.get_member(actor)
    return str(row.get("role") or "member") if row else "member"


def _member_authorize(
    store,
    actor: str | None,
    action,
    *,
    resource=None,
    config: dict[str, Any] | None = None,
) -> bool:
    """Scoped authorize() wrapper — supports v1 role matrix and v2 custom roles."""
    from agent.membership import authorize

    if action is None or not actor:
        return False
    cfg = config if config is not None else _load_config()
    role = _actor_role(store, actor)
    try:
        return authorize(
            role,
            action,
            store=store,
            actor_member_id=actor,
            resource=resource,
            config=cfg,
        )
    except (TypeError, ValueError):
        return False


def _actor_capabilities(store, member_id: str) -> dict[str, bool]:
    from agent.membership import (
        Action,
        Resource,
        can_create_invites,
        can_manage_registrations,
        can_view_member_audit,
    )

    config = _load_config()
    row = store.get_member(member_id) if member_id else None
    role = str(row.get("role") or "member") if row else "member"
    reg = can_manage_registrations(store, member_id, config)
    inv = can_create_invites(store, member_id, config)
    is_owner = role == "owner"
    project_manage = False
    try:
        project_manage = (
            _member_authorize(store, member_id, getattr(Action, "PROJECT_MANAGE", None))
            or _member_authorize(store, member_id, getattr(Action, "PROJECT_APPROVE_JOIN", None))
            or (
                bool(member_id)
                and _member_is_project_admin_on_any(store, member_id)
            )
        )
    except Exception:
        project_manage = False
    member_resource = Resource.for_scope("member", member_id) if member_id else None
    return {
        "can_invite": inv,
        "can_approve_registrations": reg,
        "can_view_audit": can_view_member_audit(store, member_id, config),
        "can_create_team": _member_authorize(store, member_id, getattr(Action, "TEAM_CREATE", None)),
        "can_archive_team": _member_authorize(store, member_id, getattr(Action, "TEAM_ARCHIVE", None)),
        "can_manage_team_memberships": (
            _member_authorize(store, member_id, getattr(Action, "TEAM_MEMBER_ADD", None))
            or _member_authorize(store, member_id, getattr(Action, "TEAM_MANAGE", None))
            or (
                bool(member_id)
                and _member_is_team_admin_on_any(store, member_id)
            )
        ),
        "can_create_project": _member_authorize(store, member_id, getattr(Action, "PROJECT_CREATE", None)),
        "can_archive_project": _member_authorize(store, member_id, getattr(Action, "PROJECT_ARCHIVE", None)),
        "can_manage_project_memberships": project_manage,
        "can_manage_tokens": _member_authorize(
            store,
            member_id,
            getattr(Action, "API_TOKEN_MANAGE", None),
            resource=member_resource,
        ),
        "can_grant_admin": _member_authorize(store, member_id, getattr(Action, "ADMIN", None)),
        "can_grant_owner": _member_authorize(store, member_id, getattr(Action, "ADMIN", None)),
        "can_delete_members": _member_authorize(store, member_id, getattr(Action, "ADMIN", None)),
        "can_lifecycle_manage_owners": is_owner,
    }


def _lifecycle_manage_guard(
    store, actor: str, raw_target: str,
) -> tuple[str | None, str | None, str | None, int | None]:
    """Return (actor_role, target_id, error_message, http_status) or (role, id, None, None)."""
    from agent.membership import (
        Action,
        actor_may_lifecycle_manage_target,
    )

    actor_row = store.get_member(actor) if actor else None
    actor_role = str(actor_row.get("role") or "member") if actor_row else "member"
    if not _member_authorize(store, actor, Action.MEMBER_INVITE):
        return None, None, "Forbidden", 403
    target_id = _resolve_member_target(store, raw_target)
    if not target_id:
        return None, None, "Member not found", 404
    target = store.get_member(target_id)
    if not target:
        return None, None, "Member not found", 404
    target_role = str(target.get("role") or "member")
    if not actor_may_lifecycle_manage_target(actor_role, target_role):
        return None, None, "Admins cannot modify owner accounts", 403
    return actor_role, target_id, None, None


def _resolve_actor_display_name(actor: str) -> str:
    """Look up the display_name for a member_id."""
    try:
        store = _store()
        try:
            row = store.get_member(actor)
            if row:
                return str(row.get("display_name") or row.get("login_name") or actor)
        finally:
            store.close()
    except Exception:
        pass
    return actor


def _check_member_auth(store, actor, action) -> bool:
    """Check if *actor* has permission for *action* using scoped authorize()."""
    return _member_authorize(store, actor, action)


def _resolve_member_target(store, raw: str) -> str | None:
    """Resolve path/login to member id for mutations on existing rows."""
    from agent.membership import validate_member_id_existing

    key = (raw or "").strip().strip("/")
    if not key:
        return None
    try:
        mid = validate_member_id_existing(key)
    except ValueError:
        return None
    if store.get_member(mid):
        return mid
    row = store.get_member_by_login(key)
    return str(row["id"]) if row else None


def _member_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "display_name": row.get("display_name"),
        "status": row.get("status"),
        "profile_role": row.get("profile_role"),
    }


def _get_members(handler, parsed, actor: str) -> None:
    from agent.membership import can_create_invites

    config = _load_config()
    store = _store()
    try:
        if not can_create_invites(store, actor, config):
            return bad(handler, "Forbidden", status=403)
        members = store.list_members(enabled_only=False)
        result = []
        for m in members:
            # Get teams
            teams = []
            projects = []
            try:
                tms = store.list_member_team_memberships(m["id"])
                for tm in tms:
                    if tm.get("status") == "active":
                        teams.append({
                            "id": tm.get("slug") or tm.get("team_id") or "",
                            "role": tm.get("role") or "member",
                        })
            except Exception:
                pass
            try:
                pms = store.list_member_project_memberships(m["id"])
                for pm in pms:
                    if pm.get("status") == "active":
                        projects.append({
                            "id": pm.get("slug") or pm.get("project_id") or "",
                            "role": pm.get("role") or "member",
                        })
            except Exception:
                pass
            result.append({
                "id": m["id"],
                "display_name": m.get("display_name") or "",
                "login_name": m.get("login_name") or "",
                "role": m.get("role") or "member",
                "enabled": bool(m.get("enabled")),
                "created_at": m.get("created_at"),
                "online_status": m.get("online_status") or "offline",
                "last_active_at": m.get("last_active_at"),
                "last_active_platform": m.get("last_active_platform"),
                "teams": teams,
                "projects": projects,
            })
        json_response(handler, {"members": result})
    finally:
        store.close()


def _member_activate(handler, parsed, actor: str, member_id: str) -> None:
    store = _store()
    try:
        actor_role, target_id, err, status = _lifecycle_manage_guard(store, actor, member_id)
        if err:
            return bad(handler, err, status=status or 403)
        if not store.activate_member(target_id, actor_role=actor_role):
            return bad(handler, "Member not found", status=404)
        json_response(handler, {"ok": True, "member_id": target_id, "status": "active"})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _member_deactivate(handler, parsed, actor: str, member_id: str) -> None:
    store = _store()
    try:
        actor_role, target_id, err, status = _lifecycle_manage_guard(store, actor, member_id)
        if err:
            return bad(handler, err, status=status or 403)
        if actor == target_id:
            return bad(handler, "Cannot deactivate your own account", status=403)
        if not store.deactivate_member(target_id, actor_role=actor_role):
            return bad(handler, "Member not found", status=404)
        json_response(handler, {"ok": True, "member_id": target_id, "status": "deactivated"})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _member_reset_password(handler, parsed, actor: str, member_id: str) -> None:
    import secrets

    store = _store()
    try:
        actor_role, target_id, err, status = _lifecycle_manage_guard(store, actor, member_id)
        if err:
            return bad(handler, err, status=status or 403)
        if actor == target_id:
            return bad(handler, "Cannot reset your own password via admin action", status=403)
        temp_password = secrets.token_hex(6)
        store.set_member_password(target_id, temp_password)
        store.force_password_reset(target_id)
        json_response(handler, {
            "ok": True,
            "member_id": target_id,
            "temp_password": temp_password,
        })
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _member_delete(handler, parsed, actor: str, member_id: str) -> None:
    import sqlite3

    from agent.membership import Action

    if actor == (member_id or "").strip().strip("/"):
        return bad(handler, "Cannot delete your own account", status=403)
    store = _store()
    try:
        target_id = _resolve_member_target(store, member_id)
        if not target_id:
            return bad(handler, "Member not found", status=404)
        member_id = target_id
        if actor == member_id:
            return bad(handler, "Cannot delete your own account", status=403)
        role = _actor_role(store, actor)
        if not _member_authorize(store, actor, Action.ADMIN):
            return bad(handler, "Only the profile owner can delete members", status=403)
        target = store.get_member(member_id)
        if not target:
            return bad(handler, "Member not found", status=404)
        # Prevent deleting the last owner — profile would be unmanageable.
        if str(target.get("role") or "member") == "owner":
            all_members = store.list_members(enabled_only=False)
            owner_count = sum(1 for m in all_members if str(m.get("role") or "") == "owner")
            if owner_count <= 1:
                return bad(handler, "Cannot delete the last owner of the profile", status=403)
        if not store.delete_member(
            member_id, actor_role=role, deleted_by=actor, source="webui",
        ):
            return bad(handler, "Member not found", status=404)
        json_response(handler, {"ok": True, "member_id": member_id, "status": "deleted"})
    except ValueError as exc:
        bad(handler, str(exc))
    except sqlite3.IntegrityError as exc:
        bad(
            handler,
            f"Cannot delete member: database constraint ({exc}). "
            "Remove project ownership or active sessions and retry.",
            status=409,
        )
    finally:
        store.close()


def _member_set_admin(handler, parsed, actor: str, member_id: str) -> None:
    from agent.membership import Action
    if actor == member_id:
        return bad(handler, "Cannot change your own role", status=403)
    store = _store()
    try:
        role = _actor_role(store, actor)
        if not _member_authorize(store, actor, Action.ADMIN):
            return bad(handler, "Only the profile owner can grant admin role", status=403)
        target = store.get_member(member_id)
        if not target:
            return bad(handler, "Member not found", status=404)
        if target.get("enabled") != 1:
            return bad(handler, "Cannot change role of a disabled member", status=400)
        target_role = str(target.get("role") or "member")
        if target_role in ("owner", "admin"):
            return bad(handler, "Member is already an owner or admin", status=409)
        store.set_member_role(member_id, "admin", role)
        json_response(handler, {"ok": True, "member_id": member_id, "role": "admin"})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _member_set_owner(handler, parsed, actor: str, member_id: str) -> None:
    from agent.membership import Action
    if actor == member_id:
        return bad(handler, "Cannot change your own role", status=403)
    store = _store()
    try:
        role = _actor_role(store, actor)
        if not _member_authorize(store, actor, Action.ADMIN):
            return bad(handler, "Only the profile owner can transfer ownership", status=403)
        target = store.get_member(member_id)
        if not target:
            return bad(handler, "Member not found", status=404)
        if target.get("enabled") != 1:
            return bad(handler, "Cannot change role of a disabled member", status=400)
        target_role = str(target.get("role") or "member")
        if target_role == "owner":
            return bad(handler, "Member is already the owner", status=409)
        store.set_member_role(member_id, "owner", role)
        json_response(handler, {"ok": True, "member_id": member_id, "role": "owner"})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _membership_project_public(row: dict[str, Any], *, actor: str, store) -> dict[str, Any]:
    from agent.membership import Action, Resource, validate_project_id

    slug = str(row.get("slug") or row.get("project_slug") or "").strip()
    pid = validate_project_id(slug) if slug else validate_project_id(str(row.get("project_id") or ""))
    role = str(row.get("role") or "member")
    status = str(row.get("membership_status") or row.get("status") or "active")
    project_resource = Resource.for_scope("project", pid)
    is_project_admin = (
        role == "project_admin"
        or _member_authorize(store, actor, Action.PROJECT_MANAGE, resource=project_resource)
        or _member_authorize(store, actor, Action.PROJECT_APPROVE_JOIN, resource=project_resource)
    )
    display = row.get("project_display_name") or row.get("display_name")
    return {
        "id": pid,
        "display_name": display,
        "project_status": row.get("project_status"),
        "role": role,
        "status": status,
        "is_project_admin": is_project_admin,
    }


def _membership_team_public(row: dict[str, Any], *, actor: str, store) -> dict[str, Any]:
    from agent.membership import Action, Resource, validate_team_id

    slug = str(row.get("slug") or row.get("team_slug") or "").strip()
    tid = validate_team_id(slug) if slug else validate_team_id(str(row.get("team_id") or ""))
    role = str(row.get("role") or "member")
    status = str(row.get("membership_status") or row.get("status") or "active")
    is_team_admin = (
        role == "admin"
        or _member_authorize(
            store,
            actor,
            Action.TEAM_MEMBER_ADD,
            resource=Resource.for_scope("team", tid),
        )
    )
    return {
        "id": tid,
        "display_name": row.get("display_name"),
        "team_status": row.get("team_status"),
        "role": role,
        "status": status,
        "is_team_admin": is_team_admin,
    }


def get_status(handler, parsed) -> dict[str, Any]:
    try:
        from agent.members_team import member_requires_team_selection
    except ImportError:
        member_requires_team_selection = None  # type: ignore[assignment]
    from agent.membership import is_members_enabled, is_teams_enabled, is_projects_enabled, members_mode

    config = _load_config()
    enabled = is_members_enabled(config)
    teams = is_teams_enabled(config)
    actor = resolve_member_id(handler, parsed) if enabled else None
    from api.auth import is_auth_enabled

    payload: dict[str, Any] = {
        "enabled": enabled,
        "teams_enabled": teams,
        "projects_enabled": is_projects_enabled(config),
        "mode": members_mode(config),
        "actor_member_id": actor,
        "actor_display_name": None,
        "actor_has_avatar": False,
        "actor_avatar_url": None,
        "active_team_id": resolve_team_id(handler, parsed, member_id=actor) if actor else None,
        "active_project_id": resolve_project_id(handler, parsed, member_id=actor) if actor else None,
        "agent_available": agent_membership_available(),
        "webui_auth_enabled": is_auth_enabled(),
    }
    if enabled:
        store = _store()
        try:
            members_list = store.list_members(enabled_only=True)
            payload["bootstrap_complete"] = len(members_list) > 0
        finally:
            store.close()
    else:
        payload["bootstrap_complete"] = False
    if enabled:
        payload.update(_oauth_status_fields(handler, parsed, config))
        payload["local_registration_requires_approval"] = local_registration_requires_approval(config)
    if actor:
        from api.user_profile import _find_avatar_path, _sanitize_profile_key

        store = _store()
        try:
            actor_row = store.get_member(actor)
            if actor_row:
                payload["actor_display_name"] = str(
                    actor_row.get("display_name")
                    or actor_row.get("login_name")
                    or actor
                )
            else:
                payload["actor_display_name"] = actor
            profile_key = _sanitize_profile_key(actor)
            avatar_path = _find_avatar_path(profile_key)
            payload["actor_has_avatar"] = avatar_path is not None
            if avatar_path:
                payload["actor_avatar_url"] = (
                    f"/api/user/profile/avatar?k={profile_key}"
                )
            payload["actor_role"] = (
                str(actor_row.get("role") or "member") if actor_row else "member"
            )
            payload["capabilities"] = _actor_capabilities(store, actor)
            payload.update(_member_password_fields(store, actor))
            if payload.get("local_registration_requires_approval") and payload["capabilities"].get(
                "can_approve_registrations"
            ):
                payload["pending_registrations_count"] = len(store.list_pending_registrations())
            if teams:
                if member_requires_team_selection is not None:
                    payload["requires_team_selection"] = member_requires_team_selection(
                        actor, config, store=store
                    )
                else:
                    payload["requires_team_selection"] = False
                explicit_tid = resolve_team_id(handler, parsed, member_id=actor)
                if payload["requires_team_selection"] and not explicit_tid:
                    payload["active_team_id"] = None
        finally:
            store.close()
    elif teams:
        payload["requires_team_selection"] = False
    from agent.membership import is_projects_enabled

    projects = is_projects_enabled(config)
    if actor and projects:
        store = _store()
        try:
            list_mships = getattr(store, "list_member_project_memberships", None)
            if callable(list_mships):
                rows = list_mships(actor)
                active = [
                    r for r in rows
                    if r.get("status") == "active" and not r.get("archived")
                ]
                explicit_pid = resolve_project_id(handler, parsed, member_id=actor)
                payload["requires_project_selection"] = len(active) > 1 and not explicit_pid
                if payload.get("requires_project_selection"):
                    payload["active_project_id"] = None
                elif not explicit_pid and len(active) == 1:
                    slug = str(active[0].get("slug") or "").strip()
                    if slug:
                        payload["active_project_id"] = slug
        except Exception:
            payload["requires_project_selection"] = False
        finally:
            store.close()
    else:
        payload["requires_project_selection"] = False
    return payload


def handle_get(handler, parsed) -> bool:
    if (
        not parsed.path.startswith("/api/members")
        and not parsed.path.startswith("/api/teams")
        and not parsed.path.startswith("/api/member-projects")
    ):
        return False
    if not agent_membership_available():
        if parsed.path == "/api/members/status":
            from api.auth import is_auth_enabled
            json_response(
                handler,
                {
                    "enabled": False,
                    "teams_enabled": False,
                    "projects_enabled": False,
                    "mode": "legacy",
                    "actor_member_id": None,
                    "active_team_id": None,
                    "agent_available": False,
                    "bootstrap_complete": False,
                    "webui_auth_enabled": is_auth_enabled(),
                },
            )
            return True
        return bad(handler, "Members feature requires intellect-agent", status=503)

    path = parsed.path

    if path == "/api/members/status":
        json_response(handler, get_status(handler, parsed))
        return True

    if path == "/api/members/oauth/providers":
        from agent.membership import is_members_enabled

        config = _load_config()
        if not is_members_enabled(config):
            return bad(handler, "Members feature is disabled", status=404)
        providers = []
        # Try unified OAuthEngine first
        try:
            from agent.oauth import OAuthEngine  # type: ignore[import-not-found]
            from agent.oauth.login_flow import provider_login_ready  # type: ignore[import-not-found]

            store = _store()
            try:
                engine = OAuthEngine(config=config, db=store)
                for p in engine.list_providers(usage="login", enabled_only=True):
                    if provider_login_ready(p):
                        providers.append(_provider_to_member_dict(p))
            finally:
                store.close()
        except Exception:
            from agent.members_oauth import list_enabled_providers

            providers = list_enabled_providers(config)
        json_response(handler, {"providers": providers})
        return True

    if path == "/api/members":
        return _require_actor(handler, parsed, _get_members)

    if path == "/api/members/oauth/authorize":
        return _handle_oauth_authorize(handler, parsed)

    if path == "/api/members/oauth/callback":
        return _handle_oauth_callback(handler, parsed)

    if path == "/api/members/register/check":
        return _get_register_check(handler, parsed)

    if path == "/api/members/registrations/pending":
        return _require_actor(handler, parsed, _get_pending_registrations)

    if path == "/api/members/audit":
        return _require_actor(handler, parsed, _get_member_audit)

    if path == "/api/members":
        from agent.membership import is_members_enabled

        config = _load_config()
        if not is_members_enabled(config):
            return bad(handler, "Members feature is disabled", status=404)
        store = _store()
        try:
            rows = store.list_members()
            json_response(handler, {"members": [_member_public(r) for r in rows]})
        finally:
            store.close()
        return True

    if path == "/api/members/me/identities":
        return _require_actor(handler, parsed, _get_identities)

    if path == "/api/members/me/identities/link":
        return _require_actor(
            handler,
            parsed,
            lambda h, p, a: _identity_link(h, p, a, {}),
        )

    if path == "/api/members/tokens":
        return _require_actor(handler, parsed, _get_tokens)

    if path == "/api/members/me/teams":
        return _require_actor(handler, parsed, _get_me_teams)

    if path == "/api/members/me/projects":
        return _require_actor(handler, parsed, _get_me_projects)

    if path.startswith("/api/member-projects/") and path.count("/") >= 3:
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "member-projects" and parts[3] == "soul":
            return _require_actor(
                handler, parsed, lambda h, p, a: _get_project_soul(h, p, a, parts[2])
            )
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "member-projects":
            return _require_actor(
                handler, parsed, lambda h, p, a: _get_project_detail(h, p, a, parts[2])
            )

    if path == "/api/member-projects":
        return _require_actor(handler, parsed, _get_projects_list)

    if path.startswith("/api/teams/") and path.count("/") >= 3:
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "teams":
            return _require_actor(handler, parsed, lambda h, p, a: _get_team_detail(h, p, a, parts[2]))
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "teams" and parts[3] == "soul":
            return _require_actor(handler, parsed, lambda h, p, a: _get_team_soul(h, p, a, parts[2]))

    if path == "/api/teams":
        return _require_actor(handler, parsed, _get_teams_list)

    return False


def handle_post(handler, parsed) -> bool:
    if (
        not parsed.path.startswith("/api/members")
        and not parsed.path.startswith("/api/teams")
        and not parsed.path.startswith("/api/member-projects")
    ):
        return False
    if not agent_membership_available():
        return bad(handler, "Members feature requires intellect-agent", status=503)

    path = parsed.path
    body = _read_members_post_body(handler, path)

    if path == "/api/members/session":
        return _post_session(handler, parsed, body)
    if path == "/api/members/oauth/logout":
        return _post_oauth_logout(handler)
    if path == "/api/members/invites":
        return _require_actor(handler, parsed, lambda h, p, a: _post_invite(h, p, a, body))
    if path == "/api/members/redeem":
        return bad(
            handler,
            "Invite redemption moved to POST /api/members/register",
            status=410,
        )
    if path == "/api/members/login":
        return _post_member_login(handler, body)
    if path == "/api/members/register/pending":
        return _post_register_pending(handler, body)
    if path == "/api/members/register/local":
        return _post_register_local(handler, body)
    if path == "/api/members/register":
        return _post_register(handler, body)
    if path == "/api/members/tokens":
        return _require_actor(handler, parsed, lambda h, p, a: _post_token(h, p, a, body))
    if path == "/api/members/me/identities/link":
        return _require_actor(handler, parsed, lambda h, p, a: _identity_link(h, p, a, body))
    if path == "/api/members/me/password":
        return _require_actor(handler, parsed, lambda h, p, a: _post_member_password(h, p, a, body))
    if path == "/api/members/active-team":
        return _require_actor(handler, parsed, lambda h, p, a: _post_active_team(h, p, a, body))
    if path == "/api/members/active-project":
        return _require_actor(handler, parsed, lambda h, p, a: _post_active_project(h, p, a, body))
    if path == "/api/teams":
        return _require_actor(handler, parsed, lambda h, p, a: _post_team_create(h, p, a, body))
    if path == "/api/member-projects":
        return _require_actor(handler, parsed, lambda h, p, a: _post_project_create(h, p, a, body))

    if path.startswith("/api/member-projects/"):
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[3] == "join":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_project_join(h, p, a, parts[2])
            )
        if len(parts) == 4 and parts[3] == "approve":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_project_approve(h, p, a, parts[2], body)
            )
        if len(parts) == 4 and parts[3] == "reject":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_project_reject(h, p, a, parts[2], body)
            )
        if len(parts) == 4 and parts[3] == "leave":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_project_leave(h, p, a, parts[2])
            )
        if len(parts) == 4 and parts[3] == "archive":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_project_archive(h, p, a, parts[2])
            )
        if len(parts) == 4 and parts[3] == "admin":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_project_admin(h, p, a, parts[2], body)
            )
        if len(parts) == 4 and parts[3] == "remove":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_project_remove(h, p, a, parts[2], body)
            )
        if len(parts) == 4 and parts[3] == "link-team":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_project_link_team(h, p, a, parts[2], body)
            )
        if len(parts) == 4 and parts[3] == "unlink-team":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_project_unlink_team(h, p, a, parts[2], body)
            )
        if len(parts) == 4 and parts[3] == "soul":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_project_soul(h, p, a, parts[2], body)
            )

    if path.startswith("/api/teams/"):
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[3] == "join":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_team_join(h, p, a, parts[2])
            )
        if len(parts) == 4 and parts[3] == "approve":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_team_approve(h, p, a, parts[2], body)
            )
        if len(parts) == 4 and parts[3] == "reject":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_team_reject(h, p, a, parts[2], body)
            )
        if len(parts) == 4 and parts[3] == "leave":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_team_leave(h, p, a, parts[2])
            )
        if len(parts) == 4 and parts[3] == "archive":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_team_archive(h, p, a, parts[2])
            )
        if len(parts) == 4 and parts[3] == "admin":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_team_admin(h, p, a, parts[2], body)
            )
        if len(parts) == 4 and parts[3] == "remove":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_team_remove(h, p, a, parts[2], body)
            )
        if len(parts) == 5 and parts[3] == "soul" and parts[4] == "refresh":
            return _require_actor(
                handler, parsed, lambda h, p, a: _post_team_soul_refresh(h, p, a, parts[2])
            )

    if path.startswith("/api/members/registrations/") and path.endswith("/approve"):
        member_id = path[len("/api/members/registrations/") : -len("/approve")].strip("/")
        if member_id:
            return _require_actor(
                handler,
                parsed,
                lambda h, p, a: _post_registration_approve(h, p, a, member_id),
            )

    if path.startswith("/api/members/registrations/") and path.endswith("/reject"):
        member_id = path[len("/api/members/registrations/") : -len("/reject")].strip("/")
        if member_id:
            return _require_actor(
                handler,
                parsed,
                lambda h, p, a: _post_registration_reject(h, p, a, member_id),
            )

    # Member management: activate / deactivate / reset-password / set-admin / set-owner
    if path.startswith("/api/members/") and (
        path.endswith("/activate") or path.endswith("/deactivate") or path.endswith("/reset-password")
        or path.endswith("/set-admin") or path.endswith("/set-owner")
    ):
        member_id = path[len("/api/members/"):]
        if member_id.endswith("/activate"):
            return _require_actor(handler, parsed, lambda h, p, a: _member_activate(h, p, a, member_id[:-len("/activate")]))
        elif member_id.endswith("/deactivate"):
            return _require_actor(handler, parsed, lambda h, p, a: _member_deactivate(h, p, a, member_id[:-len("/deactivate")]))
        elif member_id.endswith("/reset-password"):
            return _require_actor(handler, parsed, lambda h, p, a: _member_reset_password(h, p, a, member_id[:-len("/reset-password")]))
        elif member_id.endswith("/set-admin"):
            return _require_actor(handler, parsed, lambda h, p, a: _member_set_admin(h, p, a, member_id[:-len("/set-admin")]))
        elif member_id.endswith("/set-owner"):
            return _require_actor(handler, parsed, lambda h, p, a: _member_set_owner(h, p, a, member_id[:-len("/set-owner")]))

    if path == "/api/members/bootstrap":
        from api.auth import is_loopback_client
        if not is_loopback_client(handler):
            return bad(handler, "Bootstrap requires localhost access", 403)
        admin_login = str(body.get("admin_login") or "").strip()
        password = str(body.get("password") or "")
        if not admin_login:
            return bad(handler, "admin_login is required", 400)
        try:
            _validate_display_name(admin_login)
        except ValueError as exc:
            return bad(handler, str(exc), 400)
        if len(password) < 8:
            return bad(handler, "Password must be at least 8 characters", 400)
        # Enable members so create_member() works, revert on any failure
        from intellect_cli.config import set_config_value
        set_config_value("members.enabled", "true")
        from api.config import reload_config
        reload_config()
        store = _store()
        try:
            if store.get_member_by_login(admin_login):
                raise ValueError(f"Member {admin_login!r} already exists")
            mid = store.create_member(
                display_name=admin_login,
                login_name=admin_login,
                platform="webui",
            )
            if not mid:
                raise RuntimeError("Failed to create member")
            store.set_member_role(mid, "owner")
            store.set_member_password(mid, password)
            from agent.membership import ensure_member_dirs
            ensure_member_dirs(mid)
            if body.get("teams_enabled"):
                set_config_value("members.teams.enabled", "true")
            if body.get("projects_enabled"):
                set_config_value("members.projects.enabled", "true")
            reload_config()
        except Exception as exc:
            # Rollback on any failure — keep the system consistent
            set_config_value("members.enabled", "false")
            reload_config()
            return bad(handler, str(exc), 400 if isinstance(exc, ValueError) else 500)
        finally:
            store.close()
        # Bootstrap complete — clear all sessions so the operator logs in
        # as the new member through the normal login flow
        from api.auth import clear_auth_cookie, parse_cookie, invalidate_session
        cookie_val = parse_cookie(handler)
        if cookie_val:
            invalidate_session(cookie_val)
        clear_auth_cookie(handler)
        cleared = _clear_session_cookie_lines(handler)
        return j_with_cookies(
            handler,
            {"ok": True, "member_id": mid, "display_name": admin_login, "redirect": "login"},
            cookies=cleared,
        )

    return False


def handle_delete(handler, parsed) -> bool:
    if not parsed.path.startswith("/api/members"):
        return False
    if not agent_membership_available():
        return bad(handler, "Members feature requires intellect-agent", status=503)

    path = parsed.path

    if path == "/api/members/session":
        j_with_cookies(handler, {"ok": True}, cookies=_clear_session_cookie_lines(handler))
        return True

    if path == "/api/members/active-team":
        from intellect_cli.members_http import team_cookie_name
        from agent.membership import is_teams_enabled

        config = _load_config()
        if not is_teams_enabled(config):
            return bad(handler, "Teams feature is disabled", status=404)
        actor = resolve_member_id(handler, parsed)
        if not actor:
            return bad(handler, "Member login required", status=401)
        expired = f"{team_cookie_name()}=; Path={_COOKIE_PATH}; HttpOnly; SameSite=Lax; Max-Age=0"
        j_with_cookies(handler, {"ok": True}, cookies=[expired])
        return True

    if path == "/api/members/active-project":
        from intellect_cli.members_http import project_cookie_name
        from agent.membership import is_projects_enabled

        config = _load_config()
        if not is_projects_enabled(config):
            return bad(handler, "Projects feature is disabled", status=404)
        actor = resolve_member_id(handler, parsed)
        if not actor:
            return bad(handler, "Member login required", status=401)
        expired = f"{project_cookie_name()}=; Path={_COOKIE_PATH}; HttpOnly; SameSite=Lax; Max-Age=0"
        j_with_cookies(handler, {"ok": True}, cookies=[expired])
        return True

    if path.startswith("/api/members/me/identities/"):
        parts = path[len("/api/members/me/identities/") :].strip("/").split("/", 1)
        if len(parts) == 2:
            return _require_actor(
                handler,
                parsed,
                lambda h, p, a: _delete_identity(h, p, a, parts[0], parts[1]),
            )

    if path.startswith("/api/members/tokens/"):
        token_id = path[len("/api/members/tokens/") :].strip("/")
        if token_id:
            return _require_actor(
                handler, parsed, lambda h, p, a: _delete_token(h, p, a, token_id)
            )

    # Member management actions
    if path.startswith("/api/members/") and path.endswith("/delete"):
        member_id = path[len("/api/members/") : -len("/delete")].strip("/")
        if member_id:
            return _require_actor(
                handler, parsed, lambda h, p, a: _member_delete(h, p, a, member_id)
            )



def _read_json(handler) -> dict[str, Any]:
    try:
        data = read_body(handler)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def _parse_urlencoded_form(raw: bytes) -> dict[str, Any]:
    qs = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {k: (v[0] if v else "") for k, v in qs.items()}


def _read_members_post_body(handler, path: str) -> dict[str, Any]:
    """JSON body, or form fields for identity-link HTML form posts."""
    is_link = path == "/api/members/me/identities/link"
    length = int(handler.headers.get("Content-Length", 0) or 0)
    if length > MAX_BODY_BYTES:
        raise ValueError(f"Request body too large ({length} bytes)")
    if getattr(handler, "_body_drained", False):
        return {}

    raw = handler.rfile.read(length) if length else b""
    handler._body_drained = True
    if not raw:
        return {}

    ct = (handler.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if is_link and ct in ("application/x-www-form-urlencoded", "multipart/form-data"):
        return _parse_urlencoded_form(raw)
    if is_link and ct in ("", "text/plain") and b"=" in raw:
        return _parse_urlencoded_form(raw)

    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        if is_link and b"=" in raw:
            return _parse_urlencoded_form(raw)
        return {}


def _require_actor(handler, parsed, fn) -> bool:
    actor = resolve_member_id(handler, parsed)
    if not actor:
        return bad(handler, "Member login required", status=401)
    fn(handler, parsed, actor)
    return True


def _oauth_authorize_fail_redirect(handler, message: str, *, return_to: str | None = None) -> bool:
    """Browser navigation to authorize should land on /login with an error, not JSON."""
    from urllib.parse import urlencode

    loc = "/login"
    params: dict[str, str] = {}
    if message:
        params["error"] = message
    if return_to:
        params["next"] = _sanitize_return_to(return_to)
    if params:
        loc = f"{loc}?{urlencode(params)}"
    redirect(handler, loc)
    return True


def _handle_oauth_authorize(handler, parsed) -> bool:
    from agent.members_oauth import (
        create_oauth_state,
        generate_pkce_pair,
        is_oauth_enabled,
    )
    from agent.oauth.provider_resolution import normalize_provider_id  # type: ignore[import-not-found]

    config = _load_config()
    req = _WebUIRequest(handler, parsed)
    return_to = _sanitize_return_to(req.query_params.get("return_to"))
    if not is_oauth_enabled(config):
        return _oauth_authorize_fail_redirect(handler, "Member OAuth is disabled", return_to=return_to)
    provider_id = normalize_provider_id(str(req.query_params.get("provider") or "").strip())
    if not provider_id:
        return _oauth_authorize_fail_redirect(handler, "Missing provider", return_to=return_to)
    invite = str(req.query_params.get("invite") or "").strip() or None
    registration_member_id = (
        str(req.query_params.get("registration_token") or "").strip() or None
    )
    if registration_member_id:
        from agent.membership import validate_hex_member_id

        try:
            registration_member_id = validate_hex_member_id(registration_member_id)
        except ValueError as exc:
            return _oauth_authorize_fail_redirect(handler, str(exc), return_to=return_to)
    link_member_id = None
    if str(req.query_params.get("link") or "").strip().lower() in {"1", "true", "yes"}:
        link_member_id = resolve_member_id(handler, parsed)
        if not link_member_id:
            return _oauth_authorize_fail_redirect(
                handler, "Login required to link OAuth identity", return_to=return_to,
            )
        return_to = _oauth_link_return_to(return_to)
    store = _store()
    try:
        cfg = _oauth_get_login_provider(config, provider_id, store)
        if not cfg:
            return _oauth_authorize_fail_redirect(
                handler, f"Unknown OAuth provider: {provider_id}", return_to=return_to,
            )
        if not cfg.enabled:
            return _oauth_authorize_fail_redirect(
                handler, f"OAuth provider not enabled: {provider_id}", return_to=return_to,
            )

        code_verifier, code_challenge = generate_pkce_pair()
        if not cfg.pkce:
            code_verifier, code_challenge = "", None

        redirect_uri = webui_oauth_callback_uri(config, handler, parsed)
        state = create_oauth_state(
            provider_id, code_verifier,
            invite_code=invite, return_to=return_to,
            link_member_id=link_member_id,
            registration_member_id=registration_member_id,
            redirect_uri=redirect_uri,
        )
        url, err = _oauth_build_authorize_url(
            config,
            provider_id,
            store=store,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=code_challenge if cfg.pkce else None,
        )
        if err == "incomplete_credentials":
            return _oauth_authorize_fail_redirect(
                handler, "OAuth provider credentials incomplete", return_to=return_to,
            )
        if not url:
            return _oauth_authorize_fail_redirect(
                handler, "Failed to build authorization URL", return_to=return_to,
            )
        redirect(handler, url)
        return True
    except Exception as exc:
        logger.exception("OAuth authorize failed")
        return _oauth_authorize_fail_redirect(handler, str(exc), return_to=return_to)
    finally:
        store.close()


def _handle_oauth_callback(handler, parsed) -> bool:
    from agent.members_oauth import (
        OAuthMemberNotLinkedError,
        get_oauth_config,
        is_oauth_enabled,
        resolve_oauth_member,
        verify_and_consume_oauth_state,
    )

    config = _load_config()
    if not is_oauth_enabled(config):
        return bad(handler, "Member OAuth is disabled", status=404)
    req = _WebUIRequest(handler, parsed)
    error = str(req.query_params.get("error") or "").strip()
    if error:
        desc = str(req.query_params.get("error_description") or error)
        loc = _oauth_error_redirect_path(None, message=desc)
        redirect(handler, loc)
        return True
    code = str(req.query_params.get("code") or "").strip()
    state = str(req.query_params.get("state") or "").strip()
    if not code or not state:
        loc = _oauth_error_redirect_path(None, message="missing_code")
        redirect(handler, loc)
        return True
    payload: dict[str, Any] = {}
    claims: dict[str, str] | None = None
    provider_id = ""
    try:
        payload = verify_and_consume_oauth_state(state)
        if not payload:
            loc = _oauth_error_redirect_path(None, message="invalid_state")
            redirect(handler, loc)
            return True
        provider_id = str(payload.get("provider_id") or "")
        code_verifier = str(payload.get("code_verifier") or "")
        redirect_uri = str(payload.get("redirect_uri") or "").strip()
        if not redirect_uri:
            redirect_uri = webui_oauth_callback_uri(config, handler, parsed)
        store = _store()
        try:
            cfg = _oauth_get_login_provider(config, provider_id, store)
            if not cfg:
                loc = _oauth_error_redirect_path(payload, message="unknown_provider")
                redirect(handler, loc)
                return True
            from agent.members_oauth import get_provider_secret
            from agent.oauth.provider_resolution import config_to_members_dict

            if not get_provider_secret(config_to_members_dict(cfg)):
                loc = _oauth_error_redirect_path(payload, message="missing_client_secret")
                redirect(handler, loc)
                return True
            tokens, claims = _oauth_exchange_and_claims(
                config,
                provider_id,
                store=store,
                code=code,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier,
            )
            if not tokens:
                loc = _oauth_error_redirect_path(payload, message="token_exchange_failed")
                redirect(handler, loc)
                return True
            if not claims:
                loc = _oauth_error_redirect_path(payload, message="missing_claims")
                redirect(handler, loc)
                return True

            link_member_id = str(payload.get("link_member_id") or "").strip() or None
            registration_member_id = (
                str(payload.get("registration_member_id") or "").strip() or None
            )
            was_link_flow = bool(link_member_id)
            if link_member_id:
                if not store.get_member(link_member_id):
                    loc = _oauth_error_redirect_path(payload, message="link_member_not_found")
                    redirect(handler, loc)
                    return True
                external_id = str(claims.get("sub") or "")
                if not external_id:
                    loc = _oauth_error_redirect_path(payload, message="missing_claims")
                    redirect(handler, loc)
                    return True
                if not store.bind_identity(
                    link_member_id,
                    _oauth_platform(provider_id),
                    external_id,
                    email=str(claims.get("email") or ""),
                    display_name=str(claims.get("name") or ""),
                ):
                    loc = _oauth_error_redirect_path(payload, message="bind_failed")
                    redirect(handler, loc)
                    return True
                member_id = link_member_id
            elif registration_member_id:
                member_id = store.complete_oauth_registration(
                    registration_member_id,
                    provider_id,
                    claims,
                )
            else:
                member_id = resolve_oauth_member(
                    provider_id,
                    claims,
                    config=config,
                    db=store,
                    invite_code=payload.get("invite"),
                )
            if was_link_flow:
                return_to = _oauth_link_success_return_to(payload)
            else:
                return_to = _sanitize_return_to(payload.get("return_to"))
            oauth_cfg = get_oauth_config(config)
            ttl = float(oauth_cfg.get("session_ttl_hours") or 168)
            cookies = _session_cookie_lines(
                member_id,
                provider_id=provider_id,
                external_id=str(claims.get("sub") or ""),
                ttl_hours=ttl,
            )
            redirect(handler, return_to, cookies=cookies)
            return True
        finally:
            store.close()
    except OAuthMemberNotLinkedError as exc:
        # If we have a link_member_id, bind the identity even on NotLinked error
        link_member_id = str(payload.get("link_member_id") or "").strip() or None
        if link_member_id and claims:
            store = _store()
            try:
                external_id = str(claims.get("sub") or "")
                store.bind_identity(
                    link_member_id,
                    _oauth_platform(provider_id),
                    external_id,
                    email=str(claims.get("email") or ""),
                    display_name=str(claims.get("name") or ""),
                )
            finally:
                store.close()
            return_to = _oauth_link_success_return_to(payload)
            oauth_cfg = get_oauth_config(config)
            ttl = float(oauth_cfg.get("session_ttl_hours") or 168)
            cookies = _session_cookie_lines(
                link_member_id,
                provider_id=provider_id,
                external_id=str(claims.get("sub") or ""),
                ttl_hours=ttl,
            )
            redirect(handler, return_to, cookies=cookies)
            return True
        loc = _oauth_error_redirect_path(payload, message=str(exc), prefer_register=True)
        redirect(handler, loc)
        return True
    except Exception as exc:
        logger.warning("OAuth callback failed: %s", exc)
        logger.exception("Unexpected error in OAuth callback")
        loc = _oauth_error_redirect_path(payload, message=str(exc))
        redirect(handler, loc)
        return True


def _post_session(handler, parsed, body: dict[str, Any]) -> bool:
    from agent.membership import is_members_enabled

    config = _load_config()
    if not is_members_enabled(config):
        return bad(handler, "Members feature is disabled", status=404)
    if not is_loopback_client(handler):
        return bad(
            handler,
            "Member picker login is disabled. Use OAuth or redeem an invite.",
            status=403,
        )
    display_name = str(body.get("member_id") or body.get("display_name") or "").strip()
    if not display_name:
        return bad(handler, "member_id or display_name required", 400)
    store = _store()
    try:
        mid = _resolve_or_create_member(store, display_name)
    except ValueError as exc:
        return bad(handler, str(exc), 400)
    finally:
        store.close()
    oauth_cfg = {}
    try:
        from agent.members_oauth import get_oauth_config

        oauth_cfg = get_oauth_config(config)
    except Exception:
        pass
    ttl = float(oauth_cfg.get("session_ttl_hours") or 168)
    j_with_cookies(
        handler,
        {"ok": True, "member_id": mid, "display_name": display_name},
        cookies=_session_cookie_lines(mid, ttl_hours=ttl),
    )
    from api.session_events import publish_session_list_changed
    publish_session_list_changed("member_session_changed")
    return True


def _post_oauth_logout(handler) -> bool:
    j_with_cookies(handler, {"ok": True}, cookies=_clear_session_cookie_lines(handler))
    return True


def _parse_registration_fields(
    body: dict[str, Any],
    *,
    require_code: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    if str(body.get("member_id") or "").strip():
        return None, "member_id is assigned automatically; omit it from the request"
    display_name = str(body.get("display_name") or "").strip()
    password = str(body.get("password") or "")
    password_confirm = str(body.get("password_confirm") or password)
    try:
        dn = _validate_display_name(display_name)
    except ValueError as exc:
        return None, str(exc)
    if len(password) < _MIN_PASSWORD_LENGTH:
        return None, f"Password must be at least {_MIN_PASSWORD_LENGTH} characters"
    if password != password_confirm:
        return None, "Passwords do not match"
    code = str(body.get("code") or "").strip()
    if require_code and not code:
        return None, "Missing invite code"
    payload = {"display_name": dn, "password": password}
    if require_code:
        payload["code"] = code
    return payload, None


def _get_register_check(handler, parsed) -> bool:
    from agent.membership import is_members_enabled

    config = _load_config()
    if not is_members_enabled(config):
        return bad(handler, "Members feature is disabled", status=404)
    qs = parse_qs(parsed.query or "", keep_blank_values=True)
    display_name = (qs.get("display_name") or [""])[0].strip()
    store = _store()
    try:
        out: dict[str, Any] = {"member_id_auto_assigned": True}
        if display_name:
            try:
                dn = _validate_display_name(display_name)
                out["display_name_available"] = not store.display_name_taken(dn)
            except ValueError as exc:
                out["display_name_available"] = False
                out["display_name_error"] = str(exc)
        json_response(handler, out)
        return True
    finally:
        store.close()


def _post_register_pending(handler, body: dict[str, Any]) -> bool:
    from agent.membership import hash_password, is_members_enabled

    config = _load_config()
    if not is_members_enabled(config):
        return bad(handler, "Members feature is disabled", status=404)
    fields, err = _parse_registration_fields(body)
    if err or not fields:
        return bad(handler, err or "Invalid registration fields")
    store = _store()
    try:
        pw_hash, pw_salt = hash_password(fields["password"])
        token = store.create_registration_pending(
            display_name=fields["display_name"],
            password_hash=pw_hash,
        )
    except ValueError as exc:
        return bad(handler, str(exc))
    finally:
        store.close()
    json_response(handler, {"ok": True, "registration_token": token})
    return True


def _post_register(handler, body: dict[str, Any]) -> bool:
    from agent.membership import is_members_enabled

    config = _load_config()
    if not is_members_enabled(config):
        return bad(handler, "Members feature is disabled", status=404)
    fields, err = _parse_registration_fields(body, require_code=True)
    if err or not fields:
        return bad(handler, err or "Invalid registration fields")
    store = _store()
    try:
        mid = store.register_from_invite(
            fields["code"],
            display_name=fields["display_name"],
            password=fields["password"],
        )
    except ValueError as exc:
        return bad(handler, str(exc))
    finally:
        store.close()
    from agent.members_oauth import get_oauth_config

    oauth_cfg = get_oauth_config(config)
    ttl = float(oauth_cfg.get("session_ttl_hours") or 168)
    j_with_cookies(
        handler,
        {"ok": True, "member_id": mid},
        cookies=_session_cookie_lines(mid, ttl_hours=ttl),
    )
    return True


def _post_register_local(handler, body: dict[str, Any]) -> bool:
    from agent.membership import is_members_enabled

    config = _load_config()
    if not is_members_enabled(config):
        return bad(handler, "Members feature is disabled", status=404)
    if not local_registration_requires_approval(config):
        return bad(handler, "Local self-registration is disabled", status=404)
    fields, err = _parse_registration_fields(body)
    if err or not fields:
        return bad(handler, err or "Invalid registration fields")
    from agent.membership import hash_password

    store = _store()
    try:
        pw_hash, pw_salt = hash_password(fields["password"])
        mid = store.register_local_pending(
            display_name=fields["display_name"],
            password_hash=pw_hash,
            password_salt=pw_salt,
        )
    except ValueError as exc:
        return bad(handler, str(exc))
    finally:
        store.close()
    json_response(
        handler,
        {
            "ok": True,
            "member_id": mid,
            "status": "invited",
            "pending_approval": True,
        },
    )
    return True


def _get_pending_registrations(handler, parsed, actor: str) -> None:
    from agent.membership import Action, authorize, is_members_enabled

    config = _load_config()
    if not is_members_enabled(config):
        return bad(handler, "Members feature is disabled", status=404)
    if not local_registration_requires_approval(config):
        json_response(handler, {"registrations": []})
        return
    store = _store()
    try:
        row = store.get_member(actor) if actor else None
        role = str(row.get("role") or "member") if row else "member"
        from agent.membership import can_manage_registrations

        if not can_manage_registrations(store, actor, config):
            return bad(handler, "Forbidden", status=403)
        rows = store.list_pending_registrations()
        json_response(
            handler,
            {
                "registrations": [
                    {
                        "id": row["id"],
                        "display_name": row.get("display_name"),
                        "created_at": row.get("created_at"),
                    }
                    for row in rows
                ]
            },
        )
    finally:
        store.close()


def _post_registration_approve(handler, parsed, actor: str, member_id: str) -> None:
    from agent.membership import can_manage_registrations, is_members_enabled

    config = _load_config()
    if not is_members_enabled(config):
        return bad(handler, "Members feature is disabled", status=404)
    if not local_registration_requires_approval(config):
        return bad(handler, "Local registration approval is disabled", status=404)
    store = _store()
    try:
        if not can_manage_registrations(store, actor, config):
            return bad(handler, "Forbidden", status=403)
        target_id = _resolve_member_target(store, member_id)
        if not target_id:
            return bad(handler, "Pending registration not found", status=404)
        if not store.approve_registration(
            target_id, approved_by=actor, source="webui",
        ):
            return bad(handler, "Pending registration not found", status=404)
    except ValueError as exc:
        return bad(handler, str(exc))
    finally:
        store.close()
    json_response(handler, {"ok": True, "member_id": target_id, "status": "active"})
    return


def _post_registration_reject(handler, parsed, actor: str, member_id: str) -> None:
    from agent.membership import can_manage_registrations, is_members_enabled

    config = _load_config()
    if not is_members_enabled(config):
        return bad(handler, "Members feature is disabled", status=404)
    if not local_registration_requires_approval(config):
        return bad(handler, "Local registration approval is disabled", status=404)
    store = _store()
    try:
        if not can_manage_registrations(store, actor, config):
            return bad(handler, "Forbidden", status=403)
        target_id = _resolve_member_target(store, member_id)
        if not target_id:
            return bad(handler, "Pending registration not found", status=404)
        if not store.reject_registration(
            target_id, rejected_by=actor, source="webui",
        ):
            return bad(handler, "Pending registration not found", status=404)
    except ValueError as exc:
        return bad(handler, str(exc))
    finally:
        store.close()
    json_response(handler, {"ok": True, "member_id": target_id, "status": "deleted"})
    return


def _get_member_audit(handler, parsed, actor: str) -> None:
    from agent.membership import can_view_member_audit, is_members_enabled
    from urllib.parse import parse_qs

    config = _load_config()
    if not is_members_enabled(config):
        return bad(handler, "Members feature is disabled", status=404)
    qs = parse_qs(parsed.query or "", keep_blank_values=True)
    limit = int((qs.get("limit") or ["50"])[0])
    before_id = (qs.get("before_id") or [None])[0]
    before_id_int = int(before_id) if before_id else None
    action = (qs.get("action") or [None])[0]
    target = (qs.get("target") or [None])[0]
    store = _store()
    try:
        if not can_view_member_audit(store, actor, config):
            return bad(handler, "Forbidden", status=403)
        rows = store.list_member_admin_audit(
            target_member_id=target,
            action=action,
            limit=limit,
            before_id=before_id_int,
        )
        entries = []
        for row in rows:
            actor_row = store.get_member(row.get("actor_member_id") or "")
            target_row = (
                store.get_member(row["target_member_id"])
                if row.get("target_member_id")
                else None
            )
            entries.append({
                "id": row.get("id"),
                "timestamp": row.get("timestamp"),
                "action": row.get("action"),
                "source": row.get("source"),
                "actor_member_id": row.get("actor_member_id"),
                "actor_display_name": (
                    (actor_row or {}).get("display_name")
                    or (actor_row or {}).get("login_name")
                ),
                "target_member_id": row.get("target_member_id"),
                "target_display_name": (
                    (target_row or {}).get("display_name")
                    or (target_row or {}).get("login_name")
                ),
                "detail": row.get("detail"),
            })
        json_response(handler, {"entries": entries})
    finally:
        store.close()


def _post_member_login(handler, body: dict[str, Any]) -> bool:
    from agent.membership import is_members_enabled, validate_member_id
    from api.auth import _check_login_rate, _record_login_attempt

    config = _load_config()
    if not is_members_enabled(config):
        return bad(handler, "Members feature is disabled", status=404)
    canon = oauth_loopback_host_mismatch(config, handler)
    if canon:
        json_response(
            handler,
            {
                "error": (
                    f"Member login must use {canon} (OAuth callback host). "
                    "localhost and 127.0.0.1 do not share cookies."
                ),
                "error_code": "oauth_host_mismatch",
                "oauth_canonical_origin": canon,
            },
            status=409,
        )
        return True
    member_id_raw = str(body.get("member_id") or "").strip()
    password = str(body.get("password") or "")
    if not member_id_raw:
        return bad(handler, "Missing member id")
    if not password:
        return bad(handler, "Missing password")
    client_ip = handler.client_address[0]
    if not _check_login_rate(client_ip):
        return bad(handler, "Too many attempts. Try again in a minute.", status=429)
    store = _store()
    try:
        row = None
        # Step 1: try login_name (display_name) first — accepts any format
        row = store.get_member_by_login(member_id_raw)
        if row:
            mid = row["id"]
        else:
            # Step 2: try as member_id (hex or slug)
            try:
                mid = validate_member_id(member_id_raw)
            except ValueError as exc:
                return bad(handler, f"Invalid member id: {exc}")
            row = store.get_member(mid)
        if not row:
            _record_login_attempt(client_ip)
            return bad(handler, "Invalid member id or password", status=401)
        if row.get("enabled") != 1:
            _record_login_attempt(client_ip)
            json_response(
                handler,
                {
                    "error": "Account pending admin approval",
                    "error_code": "pending_approval",
                },
                status=403,
            )
            return True
        if not store.verify_member_password(mid, password):
            _record_login_attempt(client_ip)
            return bad(handler, "Invalid member id or password", status=401)
    finally:
        store.close()
    from agent.members_oauth import get_oauth_config

    oauth_cfg = get_oauth_config(config)
    ttl = float(oauth_cfg.get("session_ttl_hours") or 168)
    j_with_cookies(
        handler,
        {
            "ok": True,
            "member_id": mid,
            "password_change_required": False,
            "member_has_password": True,
        },
        cookies=_session_cookie_lines(mid, ttl_hours=ttl),
    )
    return True


def _post_member_password(handler, parsed, actor: str, body: dict[str, Any]) -> None:
    from api.auth import _check_login_rate, _record_login_attempt

    new_password = str(body.get("new_password") or body.get("password") or "")
    new_confirm = str(
        body.get("new_password_confirm") or body.get("password_confirm") or new_password
    )
    current_password = str(body.get("current_password") or "")

    if len(new_password) < _MIN_PASSWORD_LENGTH:
        bad(handler, f"Password must be at least {_MIN_PASSWORD_LENGTH} characters")
        return
    if new_password != new_confirm:
        bad(handler, "Passwords do not match")
        return

    store = _store()
    try:
        row = store.get_member(actor)
        if not row or row.get("status") != "active":
            bad(handler, "Member account is not active", status=403)
            return
        has_password = store.member_has_password(actor)
        if has_password:
            if not current_password:
                bad(handler, "Current password is required")
                return
            client_ip = handler.client_address[0]
            if not _check_login_rate(client_ip):
                bad(handler, "Too many attempts. Try again in a minute.", status=429)
                return
            if not store.verify_member_password(actor, current_password):
                _record_login_attempt(client_ip)
                bad(handler, "Current password is incorrect", status=401)
                return
        store.change_member_password(
            actor,
            current_password=current_password if has_password else None,
            new_password=new_password,
        )
    except ValueError as exc:
        bad(handler, str(exc))
        return
    finally:
        store.close()

    json_response(
        handler,
        {
            "ok": True,
            "member_has_password": True,
            "password_change_required": False,
        },
    )


def _post_invite(handler, parsed, actor: str, body: dict[str, Any]) -> None:
    from agent.membership import can_create_invites

    config = _load_config()
    store = _store()
    try:
        if not can_create_invites(store, actor, config):
            bad(handler, "Owner or admin required", status=403)
            return
        reserved = body.get("member_id")
        if reserved is not None and str(reserved).strip():
            from agent.membership import validate_hex_member_id

            reserved = validate_hex_member_id(str(reserved).strip())
        else:
            reserved = None
        code = store.create_invite(
            actor,
            reserved_member_id=reserved,
            ttl_hours=float(body.get("ttl_hours") or 168),
        )
        json_response(
            handler,
            {"code": code, "expires_in_hours": float(body.get("ttl_hours") or 168)},
        )
    finally:
        store.close()


def _get_identities(handler, parsed, actor: str) -> None:
    from agent.members_oauth import format_member_identities

    store = _store()
    try:
        json_response(handler, {"identities": format_member_identities(store, actor)})
    finally:
        store.close()


def _identity_link(handler, parsed, actor: str, body: dict[str, Any]) -> None:
    from agent.members_oauth import (
        create_oauth_state,
        generate_pkce_pair,
        is_oauth_enabled,
    )
    from agent.oauth.provider_resolution import normalize_provider_id  # type: ignore[import-not-found]

    config = _load_config()
    if not is_oauth_enabled(config):
        bad(handler, "Member OAuth is disabled", status=404)
        return
    req = _WebUIRequest(handler, parsed)
    provider_raw = str(body.get("provider") or req.query_params.get("provider") or "").strip()
    return_to_raw = body.get("return_to") or req.query_params.get("return_to") or "/"
    provider_id = normalize_provider_id(provider_raw)
    if not provider_id:
        accept = (handler.headers.get("Accept") or "").lower()
        ct = (handler.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        wants_json = "application/json" in accept and ct != "application/x-www-form-urlencoded"
        if not wants_json:
            _oauth_authorize_fail_redirect(
                handler,
                "Missing provider",
                return_to=_sanitize_return_to(return_to_raw),
            )
            return
        bad(handler, "Missing provider")
        return
    return_to = _oauth_link_return_to(return_to_raw)
    store = _store()
    try:
        cfg = _oauth_get_login_provider(config, provider_id, store)
        if not cfg:
            bad(handler, f"Unknown OAuth provider: {provider_id}", status=404)
            return
        code_verifier, code_challenge = generate_pkce_pair()
        if not cfg.pkce:
            code_verifier, code_challenge = "", None
        redirect_uri = webui_oauth_callback_uri(config, handler, parsed)
        state = create_oauth_state(
            provider_id, code_verifier,
            return_to=return_to, link_member_id=actor,
            redirect_uri=redirect_uri,
        )
        authorize_url, err = _oauth_build_authorize_url(
            config,
            provider_id,
            store=store,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=code_challenge if cfg.pkce else None,
        )
        if not authorize_url:
            bad(handler, "Failed to build authorization URL")
            return
        accept = (handler.headers.get("Accept") or "").lower()
        content_type = (handler.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if "application/json" not in content_type and "application/json" not in accept:
            redirect(handler, authorize_url)
            return
        json_response(handler, {"authorize_url": authorize_url})
    except Exception as exc:
        logger.exception("Identity link failed")
        bad(handler, str(exc))
    finally:
        store.close()


def _delete_identity(handler, parsed, actor: str, provider_id: str, external_id: str) -> None:
    config = _load_config()
    platform = _oauth_platform(provider_id)
    store = _store()
    try:
        # Guard: prevent unlinking the last identity if member has no password
        identities = store.list_identities(actor)
        has_password = store.member_has_password(actor)
        if len(identities) <= 1 and not has_password:
            bad(handler, "Cannot unlink your only identity without a password set", status=403)
            return
        if not store.unbind_identity(platform, external_id, member_id=actor):
            bad(handler, "Identity not found", status=404)
            return
        json_response(handler, {"ok": True})
    except Exception as exc:
        logger.exception("Identity unlink failed")
        bad(handler, str(exc))
    finally:
        store.close()


def _get_tokens(handler, parsed, actor: str) -> None:
    store = _store()
    try:
        tokens = store.list_api_tokens(actor)
        json_response(
            handler,
            {
                "tokens": [
                    {
                        "id": t["id"],
                        "label": t.get("label"),
                        "created_at": t.get("created_at"),
                        "revoked_at": t.get("revoked_at"),
                        "status": "revoked" if t.get("revoked_at") else "active",
                    }
                    for t in tokens
                ]
            },
        )
    finally:
        store.close()


def _post_token(handler, parsed, actor: str, body: dict[str, Any]) -> None:
    from agent.membership import Action, Resource, authorize

    store = _store()
    try:
        if not authorize(
            store=store,
            actor_member_id=actor,
            action=Action.API_TOKEN_MANAGE,
            resource=Resource.for_scope("member", actor),
        ):
            bad(handler, "Not authorized", status=403)
            return
        token_id, raw = store.create_api_token(actor, label=body.get("label") or None)
        json_response(handler, {"token_id": token_id, "bearer": raw})
    finally:
        store.close()


def _delete_token(handler, parsed, actor: str, token_id: str) -> None:
    from agent.membership import Action, Resource, authorize

    store = _store()
    try:
        row = store.get_api_token(token_id)
        if not row:
            bad(handler, "Token not found", status=404)
            return
        owner = row.get("member_id")
        if not authorize(
            store=store,
            actor_member_id=actor,
            action=Action.API_TOKEN_MANAGE,
            resource=Resource.for_scope("member", owner or actor),
        ):
            bad(handler, "Not authorized", status=403)
            return
        if not store.revoke_api_token(token_id):
            bad(handler, "Token already revoked", status=400)
            return
        json_response(handler, {"ok": True})
    finally:
        store.close()


def _get_me_teams(handler, parsed, actor: str) -> None:
    try:
        from agent.members_team import member_requires_team_selection
    except ImportError:
        member_requires_team_selection = None  # type: ignore[assignment]
    from agent.membership import is_teams_enabled

    config = _load_config()
    if not is_teams_enabled(config):
        bad(handler, "Teams feature is disabled", status=404)
        return
    store = _store()
    try:
        rows = store.list_member_team_memberships(actor)
        teams = [_membership_team_public(r, actor=actor, store=store) for r in rows]
        active_team_id = resolve_team_id(handler, parsed, member_id=actor)
        requires = (
            member_requires_team_selection(actor, config, store=store)
            if member_requires_team_selection is not None
            else False
        )
        if requires and not active_team_id:
            active_team_id = None
        elif not active_team_id:
            active = [t for t in teams if t.get("status") == "active"]
            if len(active) == 1:
                active_team_id = str(active[0]["id"])
        json_response(
            handler,
            {
                "teams": teams,
                "active_team_id": active_team_id,
                "requires_team_selection": requires,
            },
        )
    finally:
        store.close()


def _post_active_team(handler, parsed, actor: str, body: dict[str, Any]) -> None:
    from agent.membership import is_teams_enabled, validate_team_id

    config = _load_config()
    if not is_teams_enabled(config):
        bad(handler, "Teams feature is disabled", status=404)
        return
    tid = validate_team_id(str(body.get("team_id") or ""))
    store = _store()
    try:
        m = store.get_membership(tid, actor)
        if not m or m.get("status") != "active":
            bad(handler, f"You are not an active member of team {tid!r}", status=403)
            return
    finally:
        store.close()
    j_with_cookies(handler, {"ok": True, "team_id": tid}, cookies=[_team_cookie_line(tid)])
    from api.session_events import publish_session_list_changed
    publish_session_list_changed("active_team_changed")


def _get_teams_list(handler, parsed, actor: str) -> None:
    from agent.membership import Action, authorize, is_teams_enabled

    config = _load_config()
    if not is_teams_enabled(config):
        bad(handler, "Teams feature is disabled", status=404)
        return
    scope = parse_qs(parsed.query or "").get("scope", [""])[0].strip().lower()
    store = _store()
    try:
        if scope == "all":
            if not _check_member_auth(store, actor, Action.MEMBER_INVITE):
                bad(handler, "Owner or admin required", status=403)
                return
            teams = store.list_teams_brief()
        elif scope in {"", "mine"}:
            teams = store.list_teams_brief(actor)
        else:
            bad(handler, f"Unknown scope {scope!r}")
            return
        json_response(handler, {"teams": teams})
    finally:
        store.close()


def _get_team_detail(handler, parsed, actor: str, team_id: str) -> None:
    from agent.membership import Action, Resource, authorize, is_teams_enabled, validate_team_id
    from agent.members_teams_webui import team_row_for_api

    config = _load_config()
    if not is_teams_enabled(config):
        bad(handler, "Teams feature is disabled", status=404)
        return
    tid = validate_team_id(team_id)
    store = _store()
    try:
        team = store.get_team_by_ref(tid)
        if not team:
            bad(handler, "Team not found", status=404)
            return
        is_admin = authorize(
            store=store,
            actor_member_id=actor,
            action=Action.TEAM_APPROVE_JOIN,
            resource=Resource.for_scope("team", tid),
        )
        m = store.get_membership(tid, actor)
        if not is_admin and (not m or m.get("status") != "active"):
            bad(handler, "Not a member of this team", status=403)
            return
        memberships = store.list_team_memberships(tid)
        pending = [x for x in memberships if x.get("status") == "pending"]
        if not is_admin:
            memberships = [x for x in memberships if x.get("member_id") == actor]
            pending = []
        json_response(
            handler,
            {
                "team": team_row_for_api(team),
                "memberships": memberships,
                "pending": pending,
                "can_approve": is_admin,
            },
        )
    finally:
        store.close()


def _post_team_create(handler, parsed, actor: str, body: dict[str, Any]) -> None:
    from agent.membership import Action, is_teams_enabled, validate_team_id

    config = _load_config()
    if not is_teams_enabled(config):
        bad(handler, "Teams feature is disabled", status=404)
        return
    tid = validate_team_id(str(body.get("team_id") or ""))
    name = (body.get("display_name") or "").strip() or tid
    store = _store()
    try:
        if not _check_member_auth(store, actor, Action.TEAM_CREATE):
            bad(handler, "Owner or admin required", status=403)
            return
        if store.get_team_by_ref(tid):
            bad(handler, f"Team {tid!r} already exists")
            return
        actor_row = store.get_member(actor)
        actor_role = str(actor_row.get("role") or "member") if actor_row else "member"
        team = store.create_team_for_webui(
            tid, name, actor, actor_role=actor_role,
        )
        if not team:
            bad(handler, "Could not create team", status=500)
            return
        json_response(handler, {"ok": True, "team": team})
    finally:
        store.close()


def _post_team_join(handler, parsed, actor: str, team_id: str) -> None:
    from agent.membership import validate_team_id

    tid = validate_team_id(team_id)
    store = _store()
    try:
        store.request_team_join(tid, actor)
        json_response(handler, {"ok": True, "status": "pending"})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _post_team_approve(handler, parsed, actor: str, team_id: str, body: dict[str, Any]) -> None:
    from agent.membership import Action, Resource, authorize, validate_team_id

    tid = validate_team_id(team_id)
    store = _store()
    target = _resolve_member_target(store, str(body.get("member_id") or ""))
    if not target:
        bad(handler, "member_id required", status=400)
        return
    try:
        if not authorize(
            store=store,
            actor_member_id=actor,
            action=Action.TEAM_APPROVE_JOIN,
            resource=Resource.for_scope("team", tid),
        ):
            bad(handler, "Team admin required", status=403)
            return
        store.approve_team_join(tid, target, approved_by=actor)
        json_response(handler, {"ok": True, "member_id": target, "status": "active"})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _post_team_reject(handler, parsed, actor: str, team_id: str, body: dict[str, Any]) -> None:
    from agent.membership import Action, Resource, authorize, validate_team_id

    tid = validate_team_id(team_id)
    store = _store()
    target = _resolve_member_target(store, str(body.get("member_id") or ""))
    if not target:
        bad(handler, "member_id required", status=400)
        return
    try:
        if not authorize(
            store=store,
            actor_member_id=actor,
            action=Action.TEAM_APPROVE_JOIN,
            resource=Resource.for_scope("team", tid),
        ):
            bad(handler, "Team admin required", status=403)
            return
        store.reject_team_join(tid, target, rejected_by=actor)
        json_response(handler, {"ok": True, "member_id": target, "status": "rejected"})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _post_team_leave(handler, parsed, actor: str, team_id: str) -> None:
    from agent.membership import validate_team_id

    tid = validate_team_id(team_id)
    store = _store()
    try:
        actor_row = store.get_member(actor)
        actor_role = str(actor_row.get("role") or "member") if actor_row else "member"
        store.leave_team(tid, actor, actor_role=actor_role, actor_member_id=actor)
        json_response(handler, {"ok": True, "team_id": tid})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _post_team_archive(handler, parsed, actor: str, team_id: str) -> None:
    from agent.membership import Action, is_teams_enabled, validate_team_id

    config = _load_config()
    if not is_teams_enabled(config):
        bad(handler, "Teams feature is disabled", status=404)
        return
    tid = validate_team_id(team_id)
    store = _store()
    try:
        if not _check_member_auth(store, actor, Action.TEAM_ARCHIVE):
            bad(handler, "Owner required", status=403)
            return
        actor_row = store.get_member(actor)
        actor_role = str(actor_row.get("role") or "member") if actor_row else "member"
        store.archive_team_by_ref(tid, actor_role=actor_role)
        json_response(handler, {"ok": True, "team_id": tid})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _post_team_admin(handler, parsed, actor: str, team_id: str, body: dict[str, Any]) -> None:
    from agent.membership import Action, Resource, authorize, validate_team_id

    tid = validate_team_id(team_id)
    store = _store()
    target = _resolve_member_target(store, str(body.get("member_id") or ""))
    if not target:
        bad(handler, "member_id required", status=400)
        return
    op = str(body.get("action") or "add").strip().lower()
    promote = op in {"add", "promote"}
    try:
        if not authorize(
            store=store,
            actor_member_id=actor,
            action=Action.TEAM_MEMBER_ADD,
            resource=Resource.for_scope("team", tid),
        ):
            bad(handler, "Team admin required", status=403)
            return
        actor_row = store.get_member(actor)
        actor_role = str(actor_row.get("role") or "member") if actor_row else "member"
        store.set_team_admin(
            tid, target, promote=promote, actor_role=actor_role, actor_member_id=actor,
        )
        json_response(handler, {"ok": True, "member_id": target, "role": "admin" if promote else "member"})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _post_team_remove(handler, parsed, actor: str, team_id: str, body: dict[str, Any]) -> None:
    from agent.membership import Action, Resource, authorize, validate_team_id

    tid = validate_team_id(team_id)
    store = _store()
    target = _resolve_member_target(store, str(body.get("member_id") or ""))
    if not target:
        bad(handler, "member_id required", status=400)
        return
    if target == actor:
        bad(handler, "Cannot remove yourself; use leave", status=400)
        return
    try:
        if not authorize(
            store=store,
            actor_member_id=actor,
            action=Action.TEAM_MEMBER_REMOVE,
            resource=Resource.for_scope("team", tid),
        ):
            bad(handler, "Team admin required", status=403)
            return
        internal = store.resolve_team_internal_id(tid)
        if not internal:
            bad(handler, f"Team {tid!r} not found", status=404)
            return
        actor_row = store.get_member(actor)
        actor_role = str(actor_row.get("role") or "member") if actor_row else "member"
        store.remove_team_member(
            internal, target, actor_role=actor_role, actor_member_id=actor,
        )
        json_response(handler, {"ok": True, "member_id": target, "team_id": tid})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _get_team_soul(handler, parsed, actor: str, team_id: str) -> None:
    from agent.membership import is_teams_enabled, validate_team_id
    from intellect_constants import get_intellect_home

    config = _load_config()
    if not is_teams_enabled(config):
        bad(handler, "Teams feature is disabled", status=404)
        return
    tid = validate_team_id(team_id)
    store = _store()
    try:
        m = store.get_membership(tid, actor)
        if not m or m.get("status") != "active":
            bad(handler, "Not a member of this team", status=403)
            return
        path = get_intellect_home() / "teams" / tid / "SOUL.md"
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        json_response(handler, {"team_id": tid, "path": str(path), "content": text})
    finally:
        store.close()


def _post_team_soul_refresh(handler, parsed, actor: str, team_id: str) -> None:
    from agent.membership import Action, Resource, authorize, is_teams_enabled, validate_team_id
    from agent.team_soul import synthesize_team_soul

    config = _load_config()
    if not is_teams_enabled(config):
        bad(handler, "Teams feature is disabled", status=404)
        return
    tid = validate_team_id(team_id)
    store = _store()
    try:
        if not authorize(
            store=store,
            actor_member_id=actor,
            action=Action.TEAM_MANAGE,
            resource=Resource.for_scope("team", tid),
        ) and not authorize(
            store=store,
            actor_member_id=actor,
            action=Action.TEAM_APPROVE_JOIN,
            resource=Resource.for_scope("team", tid),
        ):
            bad(handler, "Team admin required", status=403)
            return
        ok, msg = synthesize_team_soul(tid, config=config)
        if not ok:
            bad(handler, msg or "SOUL refresh failed", status=500)
            return
        json_response(handler, {"ok": True, "team_id": tid, "message": msg})
    finally:
        store.close()


def _get_me_projects(handler, parsed, actor: str) -> None:
    from agent.membership import is_projects_enabled

    config = _load_config()
    if not is_projects_enabled(config):
        bad(handler, "Projects feature is disabled", status=404)
        return
    store = _store()
    try:
        rows = store.list_member_project_memberships(actor)
        projects = [_membership_project_public(r, actor=actor, store=store) for r in rows]
        active_project_id = resolve_project_id(handler, parsed, member_id=actor)
        active_rows = [
            p for p in projects if p.get("status") == "active" and p.get("project_status") != "archived"
        ]
        requires = len(active_rows) > 1 and not active_project_id
        if requires:
            active_project_id = None
        elif not active_project_id and len(active_rows) == 1:
            active_project_id = str(active_rows[0]["id"])
        json_response(
            handler,
            {
                "projects": projects,
                "active_project_id": active_project_id,
                "requires_project_selection": requires,
            },
        )
    finally:
        store.close()


def _post_active_project(handler, parsed, actor: str, body: dict[str, Any]) -> None:
    from agent.membership import is_projects_enabled, validate_project_id

    config = _load_config()
    if not is_projects_enabled(config):
        bad(handler, "Projects feature is disabled", status=404)
        return
    pid = validate_project_id(str(body.get("project_id") or ""))
    store = _store()
    try:
        m = store.get_project_membership(pid, actor)
        if not m or m.get("status") != "active":
            bad(handler, f"You are not an active member of project {pid!r}", status=403)
            return
    finally:
        store.close()
    j_with_cookies(handler, {"ok": True, "project_id": pid}, cookies=[_project_cookie_line(pid)])
    from api.session_events import publish_session_list_changed
    publish_session_list_changed("active_project_changed")


def _get_projects_list(handler, parsed, actor: str) -> None:
    from agent.membership import Action, is_projects_enabled

    config = _load_config()
    if not is_projects_enabled(config):
        bad(handler, "Projects feature is disabled", status=404)
        return
    scope = parse_qs(parsed.query or "").get("scope", [""])[0].strip().lower()
    store = _store()
    try:
        if scope == "all":
            if not _check_member_auth(store, actor, Action.PROJECT_MANAGE):
                bad(handler, "Owner or admin required", status=403)
                return
            projects = store.list_projects_brief()
        elif scope in {"", "mine"}:
            projects = store.list_projects_brief(actor)
        else:
            bad(handler, f"Unknown scope {scope!r}")
            return
        json_response(handler, {"projects": projects})
    finally:
        store.close()


def _get_project_detail(handler, parsed, actor: str, project_id: str) -> None:
    from agent.membership import Action, Resource, authorize, is_projects_enabled, validate_project_id
    from agent.members_projects_webui import project_row_for_api

    config = _load_config()
    if not is_projects_enabled(config):
        bad(handler, "Projects feature is disabled", status=404)
        return
    pid = validate_project_id(project_id)
    store = _store()
    try:
        project = store.get_project_by_ref(pid)
        if not project:
            bad(handler, "Project not found", status=404)
            return
        is_admin = authorize(
            store=store,
            actor_member_id=actor,
            action=Action.PROJECT_APPROVE_JOIN,
            resource=Resource.for_scope("project", pid),
        )
        m = store.get_project_membership(pid, actor)
        if not is_admin and (not m or m.get("status") != "active"):
            bad(handler, "Not a member of this project", status=403)
            return
        memberships = store.list_project_memberships(pid)
        pending = [x for x in memberships if x.get("status") == "pending"]
        if not is_admin:
            memberships = [x for x in memberships if x.get("member_id") == actor]
            pending = []
        linked_teams = []
        if is_admin or (m and m.get("status") == "active"):
            try:
                for row in store.get_project_teams(project["id"]):
                    linked_teams.append({
                        "id": row.get("slug") or row.get("id") or "",
                        "display_name": row.get("display_name") or "",
                        "role": row.get("role") or "member",
                    })
            except Exception:
                pass
        json_response(
            handler,
            {
                "project": project_row_for_api(project),
                "memberships": memberships,
                "pending": pending,
                "linked_teams": linked_teams,
                "can_approve": is_admin,
            },
        )
    finally:
        store.close()


def _post_project_create(handler, parsed, actor: str, body: dict[str, Any]) -> None:
    from agent.membership import Action, is_projects_enabled, validate_project_id

    config = _load_config()
    if not is_projects_enabled(config):
        bad(handler, "Projects feature is disabled", status=404)
        return
    pid = validate_project_id(str(body.get("project_id") or body.get("slug") or ""))
    name = (body.get("display_name") or "").strip() or pid
    store = _store()
    try:
        if not _check_member_auth(store, actor, Action.PROJECT_CREATE):
            bad(handler, "Owner or admin required", status=403)
            return
        if store.get_project_by_ref(pid):
            bad(handler, f"Project {pid!r} already exists")
            return
        actor_row = store.get_member(actor)
        actor_role = str(actor_row.get("role") or "member") if actor_row else "member"
        team_id = str(body.get("team_id") or "").strip() or None
        repo_url = str(body.get("repo_url") or "").strip() or None
        project = store.create_project_for_webui(
            pid, name, actor, actor_role=actor_role, team_id=team_id, repo_url=repo_url,
        )
        if not project:
            bad(handler, "Could not create project", status=500)
            return
        json_response(handler, {"ok": True, "project": project})
    finally:
        store.close()


def _post_project_join(handler, parsed, actor: str, project_id: str) -> None:
    from agent.membership import validate_project_id

    pid = validate_project_id(project_id)
    store = _store()
    try:
        store.request_project_join(pid, actor)
        json_response(handler, {"ok": True, "status": "pending"})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _post_project_approve(handler, parsed, actor: str, project_id: str, body: dict[str, Any]) -> None:
    from agent.membership import Action, Resource, authorize, validate_project_id

    pid = validate_project_id(project_id)
    store = _store()
    target = _resolve_member_target(store, str(body.get("member_id") or ""))
    if not target:
        bad(handler, "member_id required", status=400)
        return
    try:
        if not authorize(
            store=store,
            actor_member_id=actor,
            action=Action.PROJECT_APPROVE_JOIN,
            resource=Resource.for_scope("project", pid),
        ):
            bad(handler, "Project admin required", status=403)
            return
        store.approve_project_join(pid, target, approved_by=actor)
        json_response(handler, {"ok": True, "member_id": target, "status": "active"})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _post_project_reject(handler, parsed, actor: str, project_id: str, body: dict[str, Any]) -> None:
    from agent.membership import Action, Resource, authorize, validate_project_id

    pid = validate_project_id(project_id)
    store = _store()
    target = _resolve_member_target(store, str(body.get("member_id") or ""))
    if not target:
        bad(handler, "member_id required", status=400)
        return
    try:
        if not authorize(
            store=store,
            actor_member_id=actor,
            action=Action.PROJECT_APPROVE_JOIN,
            resource=Resource.for_scope("project", pid),
        ):
            bad(handler, "Project admin required", status=403)
            return
        store.reject_project_join(pid, target, rejected_by=actor)
        json_response(handler, {"ok": True, "member_id": target, "status": "rejected"})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _post_project_leave(handler, parsed, actor: str, project_id: str) -> None:
    from agent.membership import validate_project_id

    pid = validate_project_id(project_id)
    store = _store()
    try:
        actor_row = store.get_member(actor)
        actor_role = str(actor_row.get("role") or "member") if actor_row else "member"
        store.leave_project(pid, actor, actor_role=actor_role, actor_member_id=actor)
        json_response(handler, {"ok": True, "project_id": pid})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _post_project_archive(handler, parsed, actor: str, project_id: str) -> None:
    from agent.membership import Action, is_projects_enabled, validate_project_id

    config = _load_config()
    if not is_projects_enabled(config):
        bad(handler, "Projects feature is disabled", status=404)
        return
    pid = validate_project_id(project_id)
    store = _store()
    try:
        if not _check_member_auth(store, actor, Action.PROJECT_ARCHIVE):
            bad(handler, "Owner required", status=403)
            return
        actor_row = store.get_member(actor)
        actor_role = str(actor_row.get("role") or "member") if actor_row else "member"
        store.archive_project_by_ref(pid, actor_role=actor_role)
        json_response(handler, {"ok": True, "project_id": pid})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _post_project_admin(handler, parsed, actor: str, project_id: str, body: dict[str, Any]) -> None:
    from agent.membership import Action, Resource, authorize, validate_project_id

    pid = validate_project_id(project_id)
    store = _store()
    target = _resolve_member_target(store, str(body.get("member_id") or ""))
    if not target:
        bad(handler, "member_id required", status=400)
        return
    op = str(body.get("action") or "add").strip().lower()
    promote = op in {"add", "promote"}
    try:
        if not authorize(
            store=store,
            actor_member_id=actor,
            action=Action.PROJECT_MANAGE,
            resource=Resource.for_scope("project", pid),
        ) and not authorize(
            store=store,
            actor_member_id=actor,
            action=Action.PROJECT_APPROVE_JOIN,
            resource=Resource.for_scope("project", pid),
        ):
            bad(handler, "Project admin required", status=403)
            return
        actor_row = store.get_member(actor)
        actor_role = str(actor_row.get("role") or "member") if actor_row else "member"
        store.set_project_admin(
            pid, target, promote=promote, actor_role=actor_role, actor_member_id=actor,
        )
        role = "project_admin" if promote else "member"
        json_response(handler, {"ok": True, "member_id": target, "role": role})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _post_project_remove(handler, parsed, actor: str, project_id: str, body: dict[str, Any]) -> None:
    from agent.membership import Action, Resource, authorize, validate_project_id

    pid = validate_project_id(project_id)
    store = _store()
    target = _resolve_member_target(store, str(body.get("member_id") or ""))
    if not target:
        bad(handler, "member_id required", status=400)
        return
    if target == actor:
        bad(handler, "Cannot remove yourself; use leave", status=400)
        return
    try:
        if not authorize(
            store=store,
            actor_member_id=actor,
            action=Action.PROJECT_MANAGE,
            resource=Resource.for_scope("project", pid),
        ):
            bad(handler, "Project admin required", status=403)
            return
        internal = store.resolve_project_internal_id(pid)
        if not internal:
            bad(handler, f"Project {pid!r} not found", status=404)
            return
        actor_row = store.get_member(actor)
        actor_role = str(actor_row.get("role") or "member") if actor_row else "member"
        store.remove_project_member(
            internal, target, actor_role=actor_role, actor_member_id=actor,
        )
        json_response(handler, {"ok": True, "member_id": target, "project_id": pid})
    except ValueError as exc:
        bad(handler, str(exc))
    finally:
        store.close()


def _get_project_soul(handler, parsed, actor: str, project_id: str) -> None:
    from agent.membership import is_projects_enabled, validate_project_id
    from agent.project_env import read_project_soul
    from agent.projects import get_project_dir

    config = _load_config()
    if not is_projects_enabled(config):
        bad(handler, "Projects feature is disabled", status=404)
        return
    pid = validate_project_id(project_id)
    store = _store()
    try:
        m = store.get_project_membership(pid, actor)
        if not m or m.get("status") != "active":
            bad(handler, "Not a member of this project", status=403)
            return
        path = get_project_dir(pid, config) / "SOUL.md"
        text = read_project_soul(pid, config) or ""
        json_response(handler, {"project_id": pid, "path": str(path), "content": text})
    finally:
        store.close()


def _post_project_soul(handler, parsed, actor: str, project_id: str, body: dict[str, Any]) -> None:
    from agent.membership import Action, Resource, authorize, is_projects_enabled, validate_project_id
    from agent.project_env import write_project_soul

    config = _load_config()
    if not is_projects_enabled(config):
        bad(handler, "Projects feature is disabled", status=404)
        return
    pid = validate_project_id(project_id)
    content = str(body.get("content") or "")
    store = _store()
    try:
        if not authorize(
            store=store,
            actor_member_id=actor,
            action=Action.PROJECT_MANAGE,
            resource=Resource.for_scope("project", pid),
        ):
            bad(handler, "Project admin required", status=403)
            return
        write_project_soul(pid, content, config=config)
        json_response(handler, {"ok": True, "project_id": pid})
    finally:
        store.close()


def _post_project_link_team(handler, parsed, actor: str, project_id: str, body: dict[str, Any]) -> None:
    from agent.membership import Action, Resource, authorize, is_projects_enabled, validate_project_id, validate_team_id

    config = _load_config()
    if not is_projects_enabled(config):
        bad(handler, "Projects feature is disabled", status=404)
        return
    pid = validate_project_id(project_id)
    team_slug = validate_team_id(str(body.get("team_id") or ""))
    store = _store()
    try:
        if not authorize(
            store=store,
            actor_member_id=actor,
            action=Action.PROJECT_MANAGE,
            resource=Resource.for_scope("project", pid),
        ):
            bad(handler, "Project admin required", status=403)
            return
        project = store.get_project_by_ref(pid)
        team = store.get_team_by_ref(team_slug)
        if not project or not team:
            bad(handler, "Project or team not found", status=404)
            return
        store.link_project_team(project["id"], team["id"])
        json_response(handler, {"ok": True, "project_id": pid, "team_id": team_slug})
    finally:
        store.close()


def _post_project_unlink_team(handler, parsed, actor: str, project_id: str, body: dict[str, Any]) -> None:
    from agent.membership import Action, Resource, authorize, is_projects_enabled, validate_project_id, validate_team_id

    config = _load_config()
    if not is_projects_enabled(config):
        bad(handler, "Projects feature is disabled", status=404)
        return
    pid = validate_project_id(project_id)
    team_slug = validate_team_id(str(body.get("team_id") or ""))
    store = _store()
    try:
        if not authorize(
            store=store,
            actor_member_id=actor,
            action=Action.PROJECT_MANAGE,
            resource=Resource.for_scope("project", pid),
        ):
            bad(handler, "Project admin required", status=403)
            return
        project = store.get_project_by_ref(pid)
        team = store.get_team_by_ref(team_slug)
        if not project or not team:
            bad(handler, "Project or team not found", status=404)
            return
        store.unlink_project_team(project["id"], team["id"])
        json_response(handler, {"ok": True, "project_id": pid, "team_id": team_slug})
    finally:
        store.close()
