"""MSAL (Microsoft Authentication Library) adapter for Azure AD member login (P3-2).

Provides native Azure AD authentication via the ``msal`` library, enabling:
- WAM broker integration on Windows (seamless SSO)
- Conditional Access policy compliance
- Cross-process token cache (SerializableTokenCache persisted to disk)
- Protocol-correct PKCE, nonce, and token refresh handling

Activation:
  Set ``members.oauth.azure_ad.use_msal: true`` in config.yaml.
  When false (default), the existing hand-rolled OIDC flow is used.

Token cache:
  Cached at ``{INTELLECT_HOME}/.msal-token-cache.json`` with mode 0o600.
  Shared across process restarts; ``remove_account()`` clears both the
  in-memory cache and the persisted file.

Dependencies:
  ``msal>=1.31`` (added to ``[project.optional-dependencies] msal``)

Fixes from code audit (2026-06-06):
  F1: ``acquire_token_silent()`` now null-checks the result before ``"error" in result``
  F4: ``tenant_id`` default changed from ``"common"`` to ``"organizations"``
  F5: ``redirect_uri`` default aligned with OAuth engine (``http://127.0.0.1:18923/callback``)
  F7: ``SerializableTokenCache`` persisted to ``{INTELLECT_HOME}/.msal-token-cache.json``
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from intellect_constants import get_intellect_home

logger = logging.getLogger(__name__)

# Sentinel for optional import
_msal: Any = None

# Default redirect URI — aligned with OAuthEngine default
_DEFAULT_REDIRECT_URI = "http://127.0.0.1:18923/callback"

# Token cache path
def _cache_path() -> Path:
    return get_intellect_home() / ".msal-token-cache.json"


def _get_msal():
    """Lazy-import msal so the dependency is optional."""
    global _msal
    if _msal is None:
        try:
            import msal
            _msal = msal
        except ImportError:
            raise ImportError(
                "MSAL support requires the 'msal' package. "
                "Install with: pip install intellect-agent[msal]"
            )
    return _msal


def _load_cache() -> Any:
    """Load the persisted MSAL token cache, or create an empty one."""
    msal = _get_msal()
    cache = msal.SerializableTokenCache()
    cache_path = _cache_path()
    if cache_path.exists():
        try:
            with open(cache_path, "r") as f:
                cache.deserialize(f.read())
        except Exception:
            logger.debug("Failed to load MSAL token cache; starting fresh", exc_info=True)
    return cache


def _save_cache(cache: Any) -> None:
    """Persist the MSAL token cache to disk (0o600)."""
    cache_path = _cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = cache.serialize()
        if not payload:
            return  # empty cache — skip write rather than persisting "{}"
        fd = os.open(str(cache_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(payload)
        except Exception:
            try:
                cache_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    except Exception:
        logger.debug("Failed to persist MSAL token cache", exc_info=True)


def build_msal_client(
    client_id: str,
    client_secret: str,
    tenant_id: str = "organizations",
    *,
    authority: str | None = None,
) -> Any:
    """Create a ConfidentialClientApplication for the given Azure AD tenant.

    Args:
        client_id: Azure AD application (client) ID
        client_secret: Client secret or certificate thumbprint
        tenant_id: Tenant ID.  Defaults to ``"organizations"`` (work/school
            accounts only, excludes personal Microsoft accounts).  Use
            ``"common"`` for multi-tenant, ``"consumers"`` for personal
            accounts, or a specific tenant GUID for single-tenant.
        authority: Override the full authority URL
    """
    msal = _get_msal()
    auth = authority or f"https://login.microsoftonline.com/{tenant_id}"
    cache = _load_cache()
    return msal.ConfidentialClientApplication(
        client_id,
        authority=auth,
        client_credential=client_secret,
        token_cache=cache,
    )


def initiate_auth_code_flow(
    app: Any,
    scopes: list[str],
    *,
    redirect_uri: str = _DEFAULT_REDIRECT_URI,
    state: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str = "S256",
) -> dict[str, Any]:
    """Start an authorization code flow with PKCE.

    Returns a dict with ``auth_uri`` and ``flow_session``.
    """
    msal = _get_msal()
    flow = app.initiate_auth_code_flow(
        scopes=scopes,
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
    )
    _save_cache(app.token_cache)
    return {
        "auth_uri": flow["auth_uri"],
        "flow_session": flow,
    }


def acquire_token_by_auth_code_flow(
    app: Any,
    flow_session: dict[str, Any],
    auth_response: dict[str, str],
) -> dict[str, Any] | None:
    """Exchange an authorization code for tokens.

    Returns a dict with ``access_token``, ``refresh_token``, ``id_token``,
    ``expires_in``, and ``id_token_claims`` on success, or None on failure.
    """
    msal = _get_msal()
    result = app.acquire_token_by_auth_code_flow(
        flow_session,
        auth_response,
    )
    if "error" in result:
        logger.warning("MSAL token acquisition failed: %s — %s",
                       result.get("error"), result.get("error_description", ""))
        return None
    _save_cache(app.token_cache)
    return result


def acquire_token_silent(
    app: Any,
    account: dict[str, Any] | None = None,
    scopes: list[str] | None = None,
) -> dict[str, Any] | None:
    """Attempt silent token acquisition from the local cache.

    Returns None if no valid cached token is available (F1: handles the
    case where MSAL returns None when no matching token exists).
    """
    msal = _get_msal()
    accounts = app.get_accounts()
    if not accounts:
        return None
    target_account = account or accounts[0]
    result = app.acquire_token_silent(
        scopes=scopes or ["User.Read"],
        account=target_account,
    )
    # F1: MSAL returns None when no matching token is found
    if result is None:
        return None
    if "error" in result:
        return None
    _save_cache(app.token_cache)
    return result


def get_accounts(app: Any) -> list[dict[str, Any]]:
    """Return all cached accounts from the MSAL token cache."""
    return app.get_accounts()


def remove_account(app: Any, account: dict[str, Any]) -> None:
    """Remove an account from the MSAL token cache (logout).

    Clears both the in-memory cache AND the persisted cache file (F7).
    """
    app.remove_account(account)
    _save_cache(app.token_cache)
