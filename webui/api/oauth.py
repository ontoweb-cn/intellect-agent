"""In-app OAuth flow implementations for onboarding.

The browser receives only WebUI-local flow metadata (flow_id, user_code,
verification_uri, high-level status). Provider device/auth codes and OAuth
tokens stay server-side and are persisted to the active Intellect profile's
``auth.json`` credential_pool.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Compatibility for older helper tests and self-heal code that import these.
AUTH_JSON_PATH = Path.home() / ".intellect" / "auth.json"

CODEX_ISSUER = "https://auth.openai.com"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_VERIFICATION_URI = f"{CODEX_ISSUER}/codex/device"
CODEX_USER_CODE_URL = f"{CODEX_ISSUER}/api/accounts/deviceauth/usercode"
CODEX_DEVICE_TOKEN_URL = f"{CODEX_ISSUER}/api/accounts/deviceauth/token"
CODEX_TOKEN_URL = f"{CODEX_ISSUER}/oauth/token"
CODEX_REDIRECT_URI = f"{CODEX_ISSUER}/deviceauth/callback"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_FLOW_MAX_WAIT_SECONDS = 15 * 60

_ALLOWED_ONBOARDING_OAUTH_PROVIDERS = {"openai-codex", "anthropic", "claude", "claude-code"}
_ANTHROPIC_PROVIDER_ALIASES = {"anthropic", "claude", "claude-code"}
_REJECTED_ONBOARDING_OAUTH_PROVIDERS = {
    "qwen-oauth",
    "gemini-cli",
    "google-gemini-cli",
    "minimax",
    "minimax-oauth",
    "copilot",
    "copilot-acp",
}

ANTHROPIC_CREDENTIAL_POLL_SECONDS = 5
ANTHROPIC_FLOW_MAX_WAIT_SECONDS = 15 * 60
ANTHROPIC_PUBLIC_LINK_ERROR = "Claude Code credential linking failed. Check server logs."

_OAUTH_FLOWS: dict[str, dict[str, Any]] = {}
_OAUTH_FLOWS_LOCK = threading.Lock()
_ANTHROPIC_ENV_KEYS = ("ANTHROPIC_TOKEN", "ANTHROPIC_API_KEY")

# ── OAuth provider catalog (Phase 1: list + disconnect) ─────────────────────

_OAUTH_PROVIDER_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "anthropic",
        "name": "Anthropic (Claude API)",
        "flow": "pkce",
        "cli_command": "intellect auth add anthropic",
        "docs_url": "https://docs.claude.com/en/api/getting-started",
    },
    {
        "id": "claude-code",
        "name": "Claude Code (subscription)",
        "flow": "external",
        "cli_command": "claude setup-token",
        "docs_url": "https://docs.claude.com/en/docs/claude-code",
    },
    {
        "id": "openai-codex",
        "name": "OpenAI Codex (ChatGPT)",
        "flow": "device_code",
        "cli_command": "intellect auth add openai-codex",
        "docs_url": "https://platform.openai.com/docs",
    },
    {
        "id": "qwen-oauth",
        "name": "Qwen (via Qwen CLI)",
        "flow": "external",
        "cli_command": "intellect auth add qwen-oauth",
        "docs_url": "https://github.com/QwenLM/qwen-code",
    },
    {
        "id": "minimax-oauth",
        "name": "MiniMax (OAuth)",
        "flow": "device_code",
        "cli_command": "intellect auth add minimax-oauth",
        "docs_url": "https://www.minimax.io",
    },
    {
        "id": "xai-oauth",
        "name": "xAI Grok (SuperGrok)",
        "flow": "external",
        "cli_command": "intellect auth add xai-oauth",
        "docs_url": "https://accounts.x.ai",
    },
)


def _clear_process_anthropic_env_values() -> None:
    """Clear Anthropic process env fallbacks under the streaming env lock."""
    from api.streaming import _ENV_LOCK

    with _ENV_LOCK:
        for key in _ANTHROPIC_ENV_KEYS:
            os.environ.pop(key, None)


def resolve_runtime_provider_with_anthropic_env_lock(resolver, *args, **kwargs):
    """Resolve runtime credentials under the Anthropic onboarding env lock.

    Request paths must resolve Anthropic env fallbacks per outbound request,
    not cache ANTHROPIC_TOKEN or ANTHROPIC_API_KEY across onboarding. Sharing
    the process-env lock prevents a chat stream from observing one stale
    Anthropic env value while onboarding has already cleared the other.
    """
    from api.streaming import _ENV_LOCK

    with _ENV_LOCK:
        return resolver(*args, **kwargs)


def _normalize_onboarding_oauth_provider(provider: str) -> str:
    provider = str(provider or "").strip().lower()
    if provider in _ANTHROPIC_PROVIDER_ALIASES:
        return "anthropic"
    return provider or "openai-codex"


def _get_active_intellect_home() -> Path:
    try:
        from api.profiles import get_active_intellect_home

        return Path(get_active_intellect_home())
    except Exception as exc:
        # Per Opus advisor on stage-296: log the silent fallback so a corrupt
        # profile state ending up writing tokens to ~/.intellect (instead of the
        # active profile) is observable in logs rather than failing silently.
        logger.warning(
            "Falling back to ~/.intellect for OAuth credential storage: "
            "active-profile resolution failed: %s",
            exc,
        )
        return Path.home() / ".intellect"


# ── legacy auth.json helpers ────────────────────────────────────────────────

def _read_auth_json(auth_path: Path | None = None) -> dict[str, Any]:
    """Read auth.json and return parsed dict, or an empty compatible store."""
    path = auth_path or AUTH_JSON_PATH
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse %s: %s", path, exc)
            return {}
    return {}


def read_auth_json():
    """Public wrapper for streaming credential self-heal code."""
    return _read_auth_json()


def _write_auth_json(data: dict[str, Any], auth_path: Path | None = None) -> Path:
    """Atomically write auth.json with owner-only permissions.

    OAuth access/refresh tokens live in this file. The temp file is chmod 0600
    before rename so the final path never inherits a permissive process umask.
    """
    path = auth_path or AUTH_JSON_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError as exc:
            logger.warning("Failed to chmod 0600 on %s: %s", tmp, exc)
        tmp.replace(path)
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return path
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _persist_model_token_to_db(provider_id: str, token_data: dict[str, Any]) -> None:
    """Mirror model OAuth tokens into state.db (PR-A4 / W4)."""
    access_token = str(token_data.get("access_token") or "").strip()
    if not access_token:
        return
    try:
        from agent.membership import MembershipStore  # type: ignore[import-not-found]
        from agent.oauth.model_tokens import persist_model_token  # type: ignore[import-not-found]

        refresh_token = str(token_data.get("refresh_token") or "").strip() or None
        expires_in = int(token_data.get("expires_in") or 0)
        store = MembershipStore()
        try:
            persist_model_token(
                store,
                provider_id,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=expires_in,
            )
        finally:
            store.close()
    except Exception:
        logger.debug("Failed to persist %s token to oauth_tokens", provider_id, exc_info=True)


def _persist_codex_credentials(intellect_home: Path, token_data: dict[str, Any]) -> Path:
    """Persist Codex OAuth credentials to state.db (PR-A10; auth.json optional)."""
    access_token = str(token_data.get("access_token") or "").strip()
    refresh_token = str(token_data.get("refresh_token") or "").strip()
    if not access_token:
        raise RuntimeError("Codex token exchange did not return an access_token")

    _persist_model_token_to_db("openai-codex", token_data)

    now = _now_iso()
    try:
        from agent.oauth.pool_storage import try_write_pool_entries  # type: ignore[import-not-found]

        try_write_pool_entries(
            "openai-codex",
            [
                {
                    "id": "codex-oauth-webui",
                    "label": "Codex OAuth",
                    "auth_type": "oauth",
                    "priority": 0,
                    "source": "manual:device_code",
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "base_url": CODEX_BASE_URL,
                    "last_refresh": now,
                    "updated_at": now,
                }
            ],
        )
    except Exception:
        logger.debug("persist Codex pool entry failed", exc_info=True)

    try:
        from api.config import invalidate_credential_pool_cache

        invalidate_credential_pool_cache("openai-codex")
    except Exception:
        logger.debug("Failed to invalidate openai-codex credential cache", exc_info=True)

    try:
        from agent.oauth.runtime_settings import should_write_auth_json  # type: ignore[import-not-found]

        if not should_write_auth_json():
            return Path(intellect_home) / "state.db"
    except Exception:
        return Path(intellect_home) / "state.db"

    auth_path = Path(intellect_home) / "auth.json"
    auth = _read_auth_json(auth_path)
    auth.setdefault("version", 1)
    pool = auth.setdefault("credential_pool", {})
    if not isinstance(pool, dict):
        pool = {}
        auth["credential_pool"] = pool
    entries = pool.setdefault("openai-codex", [])
    if not isinstance(entries, list):
        entries = []
        pool["openai-codex"] = entries

    now = _now_iso()
    entry = None
    # Per Opus advisor on stage-296: also accept the legacy `source ==
    # "oauth_device"` value so users with prior Codex OAuth credentials
    # (written by older WebUI versions before this PR's source-key change)
    # get their existing entry updated in-place rather than accumulating a
    # stale duplicate pool entry.
    _accept_sources = {"manual:device_code", "oauth_device"}
    for candidate in entries:
        if isinstance(candidate, dict) and candidate.get("source") in _accept_sources:
            entry = candidate
            break
    if entry is None:
        entry = {
            "id": "codex-oauth-" + uuid.uuid4().hex[:12],
            "label": "Codex OAuth",
            "auth_type": "oauth",
            "priority": 0,
            "source": "manual:device_code",
            "base_url": CODEX_BASE_URL,
            "created_at": now,
        }
        entries.insert(0, entry)

    entry.update(
        {
            "label": "Codex OAuth",
            "auth_type": "oauth",
            "priority": 0,
            "source": "manual:device_code",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "base_url": CODEX_BASE_URL,
            "last_refresh": now,
            "updated_at": now,
        }
    )
    auth["updated_at"] = now
    path = _write_auth_json(auth, auth_path)

    try:
        from api.config import invalidate_credential_pool_cache

        invalidate_credential_pool_cache("openai-codex")
    except Exception:
        logger.debug("Failed to invalidate openai-codex credential cache", exc_info=True)

    return path


# Backward-compatible wrapper used by older code/tests.
def _save_codex_credentials(token_data):
    return _persist_codex_credentials(_get_active_intellect_home(), token_data)


# ── Anthropic / Claude Code credential linking ─────────────────────────────

def _read_claude_code_credentials() -> dict[str, Any] | None:
    """Read Claude Code OAuth credentials from the host without exposing them.

    Delegates to the agent adapter which knows about ~/.claude/.credentials.json
    and macOS Keychain. Returns the credential dict or None.
    """
    try:
        from agent.anthropic_adapter import (
            is_claude_code_token_valid,
            read_claude_code_credentials,
        )

        creds = read_claude_code_credentials()
        if creds and (
            is_claude_code_token_valid(creds) or bool(creds.get("refreshToken"))
        ):
            return creds
    except Exception as exc:
        logger.debug("Could not read Claude Code credentials: %s", exc)
    return None


def _clear_anthropic_env_values(intellect_home: Path) -> None:
    """Clear Anthropic API/setup-token env values in the active profile only.

    The .env write path already clears os.environ while holding the streaming
    env lock. Keep a locked process-env clear here too so import/write failures
    cannot leave or partially clear stale Anthropic fallbacks.
    """
    try:
        from api.providers import _write_env_file

        _write_env_file(
            Path(intellect_home) / ".env",
            {key: None for key in _ANTHROPIC_ENV_KEYS},
        )
    except Exception as exc:
        logger.warning("Failed to clear Anthropic env values: %s", exc)
    _clear_process_anthropic_env_values()


def _persist_anthropic_link_to_db(creds: dict[str, Any] | None) -> None:
    """Mirror Claude Code OAuth into ``oauth_tokens`` + pool marker (PR-A8)."""
    access = ""
    refresh = None
    expires_ms = 0
    if creds:
        access = str(creds.get("accessToken") or "").strip()
        refresh = str(creds.get("refreshToken") or "").strip() or None
        expires_ms = int(creds.get("expiresAt") or 0)
    if access:
        try:
            from agent.oauth.model_tokens import try_persist_model_token  # type: ignore[import-not-found]

            expires_in = 0
            if expires_ms:
                import time

                expires_in = max(60, int((expires_ms / 1000.0) - time.time()))
            try_persist_model_token(
                "anthropic",
                access_token=access,
                refresh_token=refresh,
                expires_in=expires_in,
                metadata={"expires_at_ms": expires_ms, "source": "claude_code_linked"},
            )
        except Exception:
            logger.debug("persist anthropic link to oauth_tokens failed", exc_info=True)

    now = _now_iso()
    marker = {
        "id": "anthropic-claude-code-linked",
        "label": "Claude Code (linked)",
        "auth_type": "oauth",
        "priority": 0,
        "source": "claude_code_linked",
        "updated_at": now,
    }
    if access:
        marker["access_token"] = access
        if refresh:
            marker["refresh_token"] = refresh
        if expires_ms:
            marker["expires_at_ms"] = expires_ms
    try:
        from agent.oauth.pool_storage import try_write_pool_entries  # type: ignore[import-not-found]

        try_write_pool_entries("anthropic", [marker])
    except Exception:
        logger.debug("persist anthropic pool marker failed", exc_info=True)


def _link_anthropic_credentials(intellect_home: Path) -> None:
    """Link Intellect to use Claude Code's credential store.

    Clears ANTHROPIC_TOKEN and ANTHROPIC_API_KEY from the Intellect .env so
    that resolve_anthropic_token() falls through to reading Claude Code's
    ~/.claude/.credentials.json directly — the same thing the CLI's
    ``use_anthropic_claude_code_credentials()`` does.

    Persists tokens to state.db and optionally writes a marker in auth.json.
    """
    _clear_anthropic_env_values(intellect_home)
    cc_creds = _read_claude_code_credentials()
    _persist_anthropic_link_to_db(cc_creds)

    try:
        from agent.oauth.runtime_settings import should_write_auth_json  # type: ignore[import-not-found]

        if not should_write_auth_json():
            try:
                from api.config import invalidate_credential_pool_cache
                invalidate_credential_pool_cache("anthropic")
            except Exception:
                logger.debug('non-critical operation failed', exc_info=True)
            return
    except Exception:
        logger.debug('non-critical operation failed', exc_info=True)

    auth_path = Path(intellect_home) / "auth.json"
    auth = _read_auth_json(auth_path)
    auth.setdefault("version", 1)
    pool = auth.setdefault("credential_pool", {})
    if not isinstance(pool, dict):
        pool = {}
        auth["credential_pool"] = pool
    entries = pool.setdefault("anthropic", [])
    if not isinstance(entries, list):
        entries = []
        pool["anthropic"] = entries

    now = _now_iso()
    entry = None
    for candidate in entries:
        if isinstance(candidate, dict) and candidate.get("source") == "claude_code_linked":
            entry = candidate
            break
    if entry is None:
        entry = {
            "id": "anthropic-claude-code-" + uuid.uuid4().hex[:12],
            "label": "Claude Code (linked)",
            "auth_type": "oauth",
            "priority": 0,
            "source": "claude_code_linked",
            "created_at": now,
        }
        entries.insert(0, entry)

    entry.update({
        "label": "Claude Code (linked)",
        "auth_type": "oauth",
        "priority": 0,
        "source": "claude_code_linked",
        "updated_at": now,
    })
    auth["updated_at"] = now
    _write_auth_json(auth, auth_path)

    try:
        from api.config import invalidate_credential_pool_cache
        invalidate_credential_pool_cache("anthropic")
    except Exception:
        logger.debug("Failed to invalidate anthropic credential cache", exc_info=True)


def _anthropic_public_start_payload(flow_id: str, flow: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "provider": "anthropic",
        "flow_id": flow_id,
        "status": flow.get("status", "pending"),
        "poll_interval_seconds": flow.get("poll_interval_seconds", ANTHROPIC_CREDENTIAL_POLL_SECONDS),
    }
    if flow.get("status") == "pending":
        payload["action_required"] = (
            "Claude Code credentials were not found on this server. "
            "Please run 'claude login' or 'claude setup-token' in a terminal "
            "on the host, then return here — this page will detect the credentials automatically."
        )
    if flow.get("expires_at"):
        payload["expires_at"] = flow["expires_at"]
    return payload


def _anthropic_public_status_payload(flow_id: str, flow: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "provider": "anthropic",
        "flow_id": flow_id,
        "status": flow.get("status", "error"),
    }
    if flow.get("status") == "error" and flow.get("error"):
        payload["error"] = ANTHROPIC_PUBLIC_LINK_ERROR
    return payload


def _spawn_anthropic_credential_worker(flow_id: str) -> None:
    worker = threading.Thread(
        target=_run_anthropic_credential_worker, args=(flow_id,), daemon=True,
    )
    worker.start()


def _run_anthropic_credential_worker(flow_id: str) -> None:
    """Poll for Claude Code credential appearance until found, cancelled, or expired."""
    while True:
        with _OAUTH_FLOWS_LOCK:
            flow = dict(_OAUTH_FLOWS.get(flow_id) or {})
        if not flow:
            return
        if flow.get("status") != "pending":
            return
        if float(flow.get("expires_at") or 0) <= time.time():
            _set_flow_status(flow_id, "expired")
            return

        time.sleep(max(1, int(flow.get("poll_interval_seconds") or ANTHROPIC_CREDENTIAL_POLL_SECONDS)))

        # Re-check status under lock (cancel may have arrived during sleep)
        with _OAUTH_FLOWS_LOCK:
            live = _OAUTH_FLOWS.get(flow_id)
            if not live or live.get("status") != "pending":
                return

        try:
            creds = _read_claude_code_credentials()
            if creds is None:
                continue

            # Re-check status under lock before linking — cancel must win
            with _OAUTH_FLOWS_LOCK:
                current = _OAUTH_FLOWS.get(flow_id)
                if not current or current.get("status") != "pending":
                    return

            intellect_home = Path(flow["intellect_home"])
            _link_anthropic_credentials(intellect_home)
            with _OAUTH_FLOWS_LOCK:
                current = _OAUTH_FLOWS.get(flow_id)
                if not current or current.get("status") != "pending":
                    cancelled = bool(current and current.get("status") == "cancelled")
                else:
                    current["status"] = "success"
                    current["updated_at"] = time.time()
                    _drop_sensitive_flow_fields(current)
                    cancelled = False
            if cancelled:
                _remove_anthropic_link_marker(intellect_home)
            return
        except Exception as exc:
            logger.warning("Anthropic credential polling failed: %s", exc)
            with _OAUTH_FLOWS_LOCK:
                current = _OAUTH_FLOWS.get(flow_id)
                if current and current.get("status") == "pending":
                    current["status"] = "error"
                    current["updated_at"] = time.time()
                    current["error"] = str(exc)
                    _drop_sensitive_flow_fields(current)
            return


def _remove_anthropic_link_marker(intellect_home: Path) -> None:
    """Remove the secret-free Claude Code linked marker after a cancelled race."""
    auth_path = Path(intellect_home) / "auth.json"
    auth = _read_auth_json(auth_path)
    pool = auth.get("credential_pool")
    if not isinstance(pool, dict):
        return
    entries = pool.get("anthropic")
    if not isinstance(entries, list):
        return
    kept = [entry for entry in entries if not (isinstance(entry, dict) and entry.get("source") == "claude_code_linked")]
    if len(kept) == len(entries):
        return
    if kept:
        pool["anthropic"] = kept
    else:
        pool.pop("anthropic", None)
    auth["updated_at"] = _now_iso()
    _write_auth_json(auth, auth_path)
    try:
        from api.config import invalidate_credential_pool_cache
        invalidate_credential_pool_cache("anthropic")
    except Exception:
        logger.debug("Failed to invalidate anthropic credential cache", exc_info=True)


# ── Codex protocol ──────────────────────────────────────────────────────────

def _json_request(url: str, payload: dict[str, Any], *, form: bool = False) -> dict[str, Any]:
    if form:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        content_type = "application/x-www-form-urlencoded"
    else:
        data = json.dumps(payload).encode("utf-8")
        content_type = "application/json"
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": content_type, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _request_codex_user_code() -> dict[str, Any]:
    return _json_request(CODEX_USER_CODE_URL, {"client_id": CODEX_CLIENT_ID})


def _poll_codex_authorization(device_auth_id: str, user_code: str) -> dict[str, Any] | None:
    try:
        return _json_request(
            CODEX_DEVICE_TOKEN_URL,
            {"device_auth_id": device_auth_id, "user_code": user_code},
        )
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return None
        raise


def _exchange_codex_authorization(authorization_code: str, code_verifier: str) -> dict[str, Any]:
    return _json_request(
        CODEX_TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": CODEX_REDIRECT_URI,
            "client_id": CODEX_CLIENT_ID,
            "code_verifier": code_verifier,
        },
        form=True,
    )


def _codex_public_start_payload(flow_id: str, flow: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "provider": "openai-codex",
        "flow_id": flow_id,
        "status": flow.get("status", "pending"),
        "verification_uri": CODEX_VERIFICATION_URI,
        "user_code": flow.get("user_code", ""),
        "expires_at": flow.get("expires_at"),
        "poll_interval_seconds": flow.get("poll_interval_seconds", 5),
    }


def _codex_public_status_payload(flow_id: str, flow: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "ok": True,
        "provider": "openai-codex",
        "flow_id": flow_id,
        "status": flow.get("status", "error"),
    }
    if flow.get("status") == "error" and flow.get("error"):
        payload["error"] = str(flow.get("error"))[:200]
    return payload


def _public_start_payload(flow_id: str, flow: dict[str, Any]) -> dict[str, Any]:
    provider = flow.get("provider", "openai-codex")
    if provider == "anthropic":
        return _anthropic_public_start_payload(flow_id, flow)
    return _codex_public_start_payload(flow_id, flow)


def _public_status_payload(flow_id: str, flow: dict[str, Any]) -> dict[str, Any]:
    provider = flow.get("provider", "openai-codex")
    if provider == "anthropic":
        return _anthropic_public_status_payload(flow_id, flow)
    return _codex_public_status_payload(flow_id, flow)


def _drop_sensitive_flow_fields(flow: dict[str, Any]) -> None:
    for key in (
        "device_auth_id",
        "authorization_code",
        "code_verifier",
        "access_token",
        "refresh_token",
        "token_data",
    ):
        flow.pop(key, None)


def _cleanup_oauth_flows(now: float | None = None) -> None:
    now = now or time.time()
    cutoff = now - 300
    with _OAUTH_FLOWS_LOCK:
        for fid, flow in list(_OAUTH_FLOWS.items()):
            status = flow.get("status")
            if status == "pending" and float(flow.get("expires_at") or 0) <= now:
                flow["status"] = "expired"
                _drop_sensitive_flow_fields(flow)
            if status in {"success", "expired", "cancelled", "error"} and float(flow.get("updated_at") or 0) < cutoff:
                _OAUTH_FLOWS.pop(fid, None)


def _spawn_codex_oauth_worker(flow_id: str) -> None:
    worker = threading.Thread(target=_run_codex_oauth_worker, args=(flow_id,), daemon=True)
    worker.start()


def _set_flow_status(flow_id: str, status: str, **fields: Any) -> None:
    with _OAUTH_FLOWS_LOCK:
        flow = _OAUTH_FLOWS.get(flow_id)
        if not flow:
            return
        flow["status"] = status
        flow["updated_at"] = time.time()
        flow.update(fields)
        if status in {"success", "expired", "cancelled", "error"}:
            _drop_sensitive_flow_fields(flow)


def _run_codex_oauth_worker(flow_id: str) -> None:
    while True:
        with _OAUTH_FLOWS_LOCK:
            flow = dict(_OAUTH_FLOWS.get(flow_id) or {})
        if not flow:
            return
        status = flow.get("status")
        if status != "pending":
            return
        if float(flow.get("expires_at") or 0) <= time.time():
            _set_flow_status(flow_id, "expired")
            return

        time.sleep(max(1, int(flow.get("poll_interval_seconds") or 5)))

        with _OAUTH_FLOWS_LOCK:
            live = dict(_OAUTH_FLOWS.get(flow_id) or {})
        if live.get("status") != "pending":
            return
        try:
            code_resp = _poll_codex_authorization(
                str(live.get("device_auth_id") or ""),
                str(live.get("user_code") or ""),
            )
            if code_resp is None:
                continue
            authorization_code = str(code_resp.get("authorization_code") or "").strip()
            code_verifier = str(code_resp.get("code_verifier") or "").strip()
            if not authorization_code or not code_verifier:
                raise RuntimeError("Device auth response missing authorization_code or code_verifier")
            tokens = _exchange_codex_authorization(authorization_code, code_verifier)
            # Re-check status under lock before persisting: a cancel/expire that
            # raced with the device-token + token-exchange network calls must
            # win, so we don't persist credentials the user explicitly aborted.
            with _OAUTH_FLOWS_LOCK:
                current = _OAUTH_FLOWS.get(flow_id)
                if not current or current.get("status") != "pending":
                    return
            _persist_codex_credentials(Path(live["intellect_home"]), tokens)
            _set_flow_status(flow_id, "success")
            return
        except Exception as exc:
            logger.warning("Codex OAuth onboarding flow failed: %s", exc)
            _set_flow_status(flow_id, "error", error=str(exc))
            return


def _start_anthropic_flow(intellect_home: Path) -> dict[str, Any]:
    """Start or immediately complete the Anthropic credential-linking flow."""
    creds = _read_claude_code_credentials()
    flow_id = uuid.uuid4().hex

    if creds:
        # Credentials already exist — link and return success immediately.
        _link_anthropic_credentials(intellect_home)
        flow = {
            "provider": "anthropic",
            "status": "success",
            "intellect_home": str(intellect_home),
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        with _OAUTH_FLOWS_LOCK:
            _OAUTH_FLOWS[flow_id] = flow
        return _public_start_payload(flow_id, flow)

    # No credentials found — create a pending flow that polls for them.
    expires_at = time.time() + ANTHROPIC_FLOW_MAX_WAIT_SECONDS
    flow = {
        "provider": "anthropic",
        "status": "pending",
        "expires_at": expires_at,
        "poll_interval_seconds": ANTHROPIC_CREDENTIAL_POLL_SECONDS,
        "intellect_home": str(intellect_home),
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    with _OAUTH_FLOWS_LOCK:
        _OAUTH_FLOWS[flow_id] = flow
    _spawn_anthropic_credential_worker(flow_id)
    return _public_start_payload(flow_id, flow)


def start_onboarding_oauth_flow(body: dict[str, Any] | None) -> dict[str, Any]:
    """Start the supported onboarding OAuth flow.

    Supports OpenAI Codex (device-code flow) and Anthropic/Claude Code
    (credential-linking flow). Other providers are rejected.
    """
    _cleanup_oauth_flows()
    provider = str((body or {}).get("provider") or "").strip().lower()
    if provider not in _ALLOWED_ONBOARDING_OAUTH_PROVIDERS:
        if provider in _REJECTED_ONBOARDING_OAUTH_PROVIDERS or provider:
            raise ValueError(
                "Only OpenAI Codex and Anthropic/Claude OAuth are supported "
                "in WebUI onboarding right now"
            )
        raise ValueError("provider is required")

    # Normalize Claude aliases to canonical "anthropic"
    if provider in _ANTHROPIC_PROVIDER_ALIASES:
        return _start_anthropic_flow(_get_active_intellect_home())

    # Codex flow
    intellect_home = _get_active_intellect_home()
    try:
        device = _request_codex_user_code()
    except Exception as exc:
        raise RuntimeError(f"Failed to start Codex OAuth: {exc}") from exc

    user_code = str(device.get("user_code") or "").strip()
    device_auth_id = str(device.get("device_auth_id") or "").strip()
    if not user_code or not device_auth_id:
        raise RuntimeError("Device code response missing required fields")

    interval = max(3, int(device.get("interval") or 5))
    expires_in = int(device.get("expires_in") or CODEX_FLOW_MAX_WAIT_SECONDS)
    expires_at = time.time() + min(max(expires_in, 60), CODEX_FLOW_MAX_WAIT_SECONDS)
    flow_id = uuid.uuid4().hex
    flow = {
        "provider": "openai-codex",
        "status": "pending",
        "device_auth_id": device_auth_id,
        "user_code": user_code,
        "expires_at": expires_at,
        "poll_interval_seconds": interval,
        "intellect_home": str(intellect_home),
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    with _OAUTH_FLOWS_LOCK:
        _OAUTH_FLOWS[flow_id] = flow
    _spawn_codex_oauth_worker(flow_id)
    return _public_start_payload(flow_id, flow)


def poll_onboarding_oauth_flow(flow_id: str) -> dict[str, Any]:
    _cleanup_oauth_flows()
    fid = str(flow_id or "").strip()
    if not fid:
        raise ValueError("flow_id is required")
    with _OAUTH_FLOWS_LOCK:
        flow = _OAUTH_FLOWS.get(fid)
        if not flow:
            raise KeyError("OAuth flow not found")
        if flow.get("status") == "pending" and float(flow.get("expires_at") or 0) <= time.time():
            flow["status"] = "expired"
            flow["updated_at"] = time.time()
            _drop_sensitive_flow_fields(flow)
        return _public_status_payload(fid, dict(flow))


def cancel_onboarding_oauth_flow(body: dict[str, Any] | None) -> dict[str, Any]:
    fid = str((body or {}).get("flow_id") or "").strip()
    if not fid:
        raise ValueError("flow_id is required")
    requested_provider = _normalize_onboarding_oauth_provider(str((body or {}).get("provider") or ""))
    if requested_provider not in {"openai-codex", "anthropic"}:
        requested_provider = "openai-codex"
    with _OAUTH_FLOWS_LOCK:
        flow = _OAUTH_FLOWS.get(fid)
        if not flow:
            return {"ok": True, "provider": requested_provider, "flow_id": fid, "status": "cancelled"}
        if flow.get("status") == "pending":
            flow["status"] = "cancelled"
            flow["updated_at"] = time.time()
            _drop_sensitive_flow_fields(flow)
        result = _public_status_payload(fid, dict(flow))
    return result


# Backward-compatible names from the abandoned spike. They intentionally do not
# expose provider device secrets to callers anymore.
def start_codex_device_code():
    return start_onboarding_oauth_flow({"provider": "openai-codex"})


def poll_codex_token(device_code, interval=5):
    yield {"status": "error", "error": "Use /api/onboarding/oauth/poll with flow_id"}


# ── Phase 1: OAuth provider management endpoints ────────────────────────────

import base64
import hashlib
import secrets


def _truncate_token(value: str | None, visible: int = 6) -> str:
    """Return ``…XXXXXX`` (last N chars) for safe UI display. Strips JWT prefix."""
    if not value:
        return ""
    s = str(value)
    if "." in s and s.count(".") >= 2:
        s = s.rsplit(".", 1)[-1]
    if len(s) <= visible:
        return s
    return f"…{s[-visible:]}"


def _resolve_oauth_provider_status(provider_id: str) -> dict[str, Any]:
    """Check whether an OAuth provider is authenticated, with token previews."""
    status: dict[str, Any] = {
        "logged_in": False,
        "source": None,
        "source_label": None,
        "token_preview": None,
        "expires_at": None,
        "has_refresh_token": False,
    }
    home = _get_active_intellect_home()

    if provider_id == "anthropic":
        # 1) Intellect-managed PKCE file
        try:
            from agent.anthropic_adapter import _INTELLECT_OAUTH_FILE
            if _INTELLECT_OAUTH_FILE.exists():
                data = json.loads(_INTELLECT_OAUTH_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("accessToken"):
                    status.update(
                        logged_in=True,
                        source="intellect_pkce",
                        source_label="Intellect PKCE OAuth",
                        token_preview=_truncate_token(data.get("accessToken")),
                        has_refresh_token=bool(data.get("refreshToken")),
                    )
                    return status
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)
        # 2) Claude Code credentials (linked)
        creds = _read_claude_code_credentials()
        if creds:
            status.update(
                logged_in=True,
                source="claude_code",
                source_label="Claude Code linked",
                token_preview=_truncate_token(creds.get("token") or creds.get("accessToken")),
                has_refresh_token=bool(creds.get("refreshToken")),
            )
            return status
        try:
            from agent.oauth.pool_storage import try_read_pool_entries  # type: ignore[import-not-found]

            for e in try_read_pool_entries("anthropic"):
                if e.get("source") == "claude_code_linked" or e.get("access_token"):
                    status.update(
                        logged_in=True,
                        source=e.get("source") or "credential_pool",
                        source_label=e.get("label") or "Credential pool",
                        has_refresh_token=bool(e.get("refresh_token")),
                    )
                    return status
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)
        try:
            from agent.oauth.runtime_settings import should_read_auth_json  # type: ignore[import-not-found]

            if should_read_auth_json():
                auth = _read_auth_json(home / "auth.json")
                entries = auth.get("credential_pool", {}).get("anthropic", [])
                if isinstance(entries, list):
                    for e in entries:
                        if isinstance(e, dict) and (
                            e.get("source") == "claude_code_linked" or e.get("auth_type") == "oauth"
                        ):
                            status.update(
                                logged_in=True,
                                source=e.get("source") or "credential_pool",
                                source_label=e.get("label") or "Credential pool",
                                has_refresh_token=bool(e.get("refresh_token")),
                            )
                            return status
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)
        return status

    if provider_id == "claude-code":
        creds = _read_claude_code_credentials()
        if creds:
            status.update(
                logged_in=True,
                source="claude_code",
                source_label="Claude Code",
                token_preview=_truncate_token(creds.get("token") or creds.get("accessToken")),
                has_refresh_token=bool(creds.get("refreshToken")),
            )
        return status

    if provider_id == "openai-codex":
        try:
            from agent.oauth.model_tokens import try_model_token_auth_status  # type: ignore[import-not-found]

            db_st = try_model_token_auth_status("openai-codex")
            if db_st and db_st.get("logged_in"):
                status.update(
                    logged_in=True,
                    source="oauth_tokens",
                    source_label="state.db",
                    has_refresh_token=bool(db_st.get("has_refresh_token")),
                )
                return status
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)
        try:
            from agent.oauth.pool_storage import try_read_pool_entries  # type: ignore[import-not-found]

            for e in try_read_pool_entries("openai-codex"):
                if e.get("access_token") or e.get("refresh_token"):
                    status.update(
                        logged_in=True,
                        source=e.get("source") or "device_code",
                        source_label=e.get("label") or "Codex OAuth",
                        token_preview=_truncate_token(e.get("access_token")),
                        has_refresh_token=bool(e.get("refresh_token")),
                    )
                    return status
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)
        try:
            from agent.oauth.runtime_settings import should_read_auth_json  # type: ignore[import-not-found]

            if should_read_auth_json():
                auth = _read_auth_json(home / "auth.json")
                entries = auth.get("credential_pool", {}).get("openai-codex", [])
                if isinstance(entries, list):
                    for e in entries:
                        if isinstance(e, dict) and (e.get("access_token") or e.get("refresh_token")):
                            status.update(
                                logged_in=True,
                                source=e.get("source") or "device_code",
                                source_label=e.get("label") or "Codex OAuth",
                                token_preview=_truncate_token(e.get("access_token")),
                                has_refresh_token=bool(e.get("refresh_token")),
                            )
                            return status
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)
        # Fall back to CLI auth
        try:
            from intellect_cli.auth import get_codex_auth_status
            raw = get_codex_auth_status()
            if raw.get("logged_in"):
                status.update(
                    logged_in=True,
                    source=raw.get("source") or "codex",
                    source_label=raw.get("auth_mode") or "Codex OAuth",
                    token_preview=_truncate_token(raw.get("api_key")),
                    has_refresh_token=False,
                )
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)
        return status

    if provider_id == "qwen-oauth":
        try:
            from intellect_cli.auth import get_qwen_auth_status
            raw = get_qwen_auth_status()
            if raw.get("logged_in"):
                status.update(
                    logged_in=True,
                    source="qwen_cli",
                    source_label=raw.get("auth_store_path") or "Qwen CLI",
                    token_preview=_truncate_token(raw.get("access_token")),
                    expires_at=raw.get("expires_at"),
                    has_refresh_token=bool(raw.get("has_refresh_token")),
                )
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)
        return status

    if provider_id == "minimax-oauth":
        try:
            from intellect_cli.auth import get_minimax_oauth_auth_status
            raw = get_minimax_oauth_auth_status()
            if raw.get("logged_in"):
                status.update(
                    logged_in=True,
                    source="minimax_oauth",
                    source_label=f"MiniMax ({raw.get('region', 'global')})",
                    token_preview=None,
                    expires_at=raw.get("expires_at"),
                    has_refresh_token=True,
                )
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)
        return status

    if provider_id == "xai-oauth":
        # auth.json provider state
        try:
            from intellect_cli.auth import _load_provider_state as _lp
            state = _lp(home / "auth.json", "xai-oauth")
            tokens = state.get("tokens", {}) if isinstance(state, dict) else {}
            if tokens.get("access_token"):
                status.update(
                    logged_in=True,
                    source="xai_oauth",
                    source_label="xAI Grok OAuth",
                    token_preview=_truncate_token(tokens.get("access_token")),
                    has_refresh_token=bool(tokens.get("refresh_token")),
                )
                return status
        except Exception:
            logger.debug('non-critical operation failed', exc_info=True)
        # credential_pool fallback
        auth = _read_auth_json(home / "auth.json")
        entries = auth.get("credential_pool", {}).get("xai-oauth", [])
        if isinstance(entries, list):
            for e in entries:
                if isinstance(e, dict) and (e.get("access_token") or e.get("refresh_token")):
                    status.update(
                        logged_in=True,
                        source="credential_pool",
                        source_label="auth.json credential pool",
                        token_preview=_truncate_token(e.get("access_token")),
                        has_refresh_token=bool(e.get("refresh_token")),
                    )
                    return status
        return status

    return status


def list_oauth_providers() -> dict[str, Any]:
    """Enumerate every OAuth-capable provider with current status."""
    providers = []
    for p in _OAUTH_PROVIDER_CATALOG:
        st = _resolve_oauth_provider_status(p["id"])
        providers.append({
            "id": p["id"],
            "name": p["name"],
            "flow": p["flow"],
            "cli_command": p["cli_command"],
            "docs_url": p["docs_url"],
            "status": st,
        })
    return {"providers": providers}


def _remove_credential_pool_entries(provider_id: str, source_filter: str | None = None) -> bool:
    """Remove pool entries from state.db and optionally auth.json (A10)."""
    cleared = False
    try:
        from agent.membership import MembershipStore  # type: ignore[import-not-found]
        from agent.oauth.pool_storage import delete_pool_entries, try_read_pool_entries  # type: ignore[import-not-found]

        store = MembershipStore()
        try:
            entries = try_read_pool_entries(provider_id)
            if source_filter:
                kept = [
                    e for e in entries
                    if not (isinstance(e, dict) and e.get("source") == source_filter)
                ]
                if len(kept) != len(entries):
                    from agent.oauth.pool_storage import try_write_pool_entries

                    try_write_pool_entries(provider_id, kept)
                    cleared = True
            elif entries and delete_pool_entries(store, provider_id):
                cleared = True
        finally:
            store.close()
    except Exception:
        pass  # intentionally silent — cleanup/teardown path

    try:
        from agent.oauth.runtime_settings import should_read_auth_json  # type: ignore[import-not-found]

        if not should_read_auth_json():
            return cleared
    except Exception:
        if cleared:
            return True

    home = _get_active_intellect_home()
    auth_path = home / "auth.json"
    auth = _read_auth_json(auth_path)
    pool = auth.get("credential_pool")
    if not isinstance(pool, dict):
        return False
    entries = pool.get(provider_id)
    if not isinstance(entries, list):
        return False
    original_len = len(entries)
    if source_filter:
        entries = [e for e in entries if not (isinstance(e, dict) and e.get("source") == source_filter)]
    else:
        entries = []
    if len(entries) == original_len:
        return False
    if entries:
        pool[provider_id] = entries
    else:
        pool.pop(provider_id, None)
    auth["updated_at"] = _now_iso()
    _write_auth_json(auth, auth_path)
    try:
        from api.config import invalidate_credential_pool_cache
        invalidate_credential_pool_cache(provider_id)
    except Exception:
        logger.debug('non-critical operation failed', exc_info=True)
    return True


def disconnect_oauth_provider(provider_id: str) -> dict[str, Any]:
    """Disconnect an OAuth provider by clearing stored credentials."""
    valid_ids = {p["id"] for p in _OAUTH_PROVIDER_CATALOG}
    if provider_id not in valid_ids:
        raise ValueError(f"Unknown provider: {provider_id}. Valid: {', '.join(sorted(valid_ids))}")

    if provider_id in {"anthropic", "claude-code"}:
        try:
            from agent.anthropic_adapter import _INTELLECT_OAUTH_FILE
            if _INTELLECT_OAUTH_FILE.exists():
                _INTELLECT_OAUTH_FILE.unlink()
        except Exception:
            pass  # intentionally silent — cleanup/teardown path
        try:
            _remove_anthropic_link_marker(_get_active_intellect_home())
        except Exception:
            pass  # intentionally silent — cleanup/teardown path
        _remove_credential_pool_entries("anthropic", "manual:dashboard_pkce")
        _remove_credential_pool_entries("anthropic", "claude_code_linked")
        try:
            from agent.membership import MembershipStore  # type: ignore[import-not-found]
            from agent.oauth.model_tokens import delete_model_token  # type: ignore[import-not-found]
            from agent.oauth.pool_storage import delete_pool_entries  # type: ignore[import-not-found]

            store = MembershipStore()
            try:
                delete_model_token(store, "anthropic")
                delete_pool_entries(store, "anthropic")
            finally:
                store.close()
        except Exception:
            pass  # intentionally silent — cleanup/teardown path
        try:
            from api.config import invalidate_credential_pool_cache
            invalidate_credential_pool_cache("anthropic")
        except Exception:
            pass  # intentionally silent — cleanup/teardown path
        logger.info("oauth/disconnect: %s", provider_id)
        return {"ok": True, "provider": provider_id}

    # For other providers, try CLI helper first, then manual credential_pool removal.
    cleared = False
    try:
        from intellect_cli.auth import clear_provider_auth

        cleared = bool(clear_provider_auth(provider_id))
    except Exception:
        cleared = _remove_credential_pool_entries(provider_id)
    if not cleared:
        cleared = _remove_credential_pool_entries(provider_id)
    try:
        from agent.membership import MembershipStore  # type: ignore[import-not-found]
        from agent.oauth.model_tokens import delete_model_token  # type: ignore[import-not-found]
        from agent.oauth.pool_storage import delete_pool_entries  # type: ignore[import-not-found]

        store = MembershipStore()
        try:
            if delete_model_token(store, provider_id):
                cleared = True
            if delete_pool_entries(store, provider_id):
                cleared = True
        finally:
            store.close()
    except Exception:
        pass  # intentionally silent — cleanup/teardown path
    logger.info("oauth/disconnect: %s (cleared=%s)", provider_id, cleared)
    return {"ok": bool(cleared), "provider": provider_id}


# ── PKCE helpers ─────────────────────────────────────────────────────────────

_OAUTH_SESSION_TTL_SECONDS = 15 * 60
_oauth_sessions: dict[str, dict[str, Any]] = {}
_oauth_sessions_lock = threading.Lock()

ANTHROPIC_OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"


def _generate_pkce_pair() -> tuple[str, str]:
    """Generate (code_verifier, code_challenge) pair (S256 method)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _get_anthropic_oauth_constants() -> tuple[str, str, str, str] | None:
    """Return (client_id, token_url, redirect_uri, scopes) from agent adapter, or None."""
    try:
        from agent.anthropic_adapter import (
            _OAUTH_CLIENT_ID,
            _OAUTH_TOKEN_URL,
            _OAUTH_REDIRECT_URI,
            _OAUTH_SCOPES,
        )
        return (_OAUTH_CLIENT_ID, _OAUTH_TOKEN_URL, _OAUTH_REDIRECT_URI, _OAUTH_SCOPES)
    except ImportError:
        return None


def _gc_oauth_sessions() -> None:
    """Drop expired sessions. Called opportunistically on /start."""
    cutoff = time.time() - _OAUTH_SESSION_TTL_SECONDS
    with _oauth_sessions_lock:
        stale = [sid for sid, sess in _oauth_sessions.items()
                 if sess.get("created_at", 0) < cutoff]
        for sid in stale:
            _oauth_sessions.pop(sid, None)


def _new_oauth_session(provider_id: str, flow: str) -> tuple[str, dict[str, Any]]:
    """Create + register a new OAuth session, return (session_id, session_dict)."""
    sid = uuid.uuid4().hex
    sess = {
        "session_id": sid,
        "provider": provider_id,
        "flow": flow,
        "created_at": time.time(),
        "status": "pending",
        "error_message": None,
    }
    with _oauth_sessions_lock:
        _oauth_sessions[sid] = sess
    return sid, sess


def _set_oauth_session_error(session_id: str, message: str) -> None:
    """Mark a session as errored."""
    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(session_id)
        if sess:
            sess["status"] = "error"
            sess["error_message"] = message


# ── Anthropic PKCE flow ─────────────────────────────────────────────────────

def _start_anthropic_pkce() -> dict[str, Any]:
    """Begin PKCE flow for Anthropic. Returns the auth_url the UI should open."""
    consts = _get_anthropic_oauth_constants()
    if consts is None:
        raise RuntimeError("Anthropic OAuth not available (missing adapter)")

    client_id, _token_url, redirect_uri, scopes = consts
    verifier, challenge = _generate_pkce_pair()
    sid, sess = _new_oauth_session("anthropic", "pkce")
    sess["verifier"] = verifier
    sess["state"] = verifier

    params = {
        "code": "true",
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    }
    auth_url = f"{ANTHROPIC_OAUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
    return {
        "session_id": sid,
        "flow": "pkce",
        "auth_url": auth_url,
        "expires_in": _OAUTH_SESSION_TTL_SECONDS,
    }


def _save_anthropic_oauth_creds(access_token: str, refresh_token: str, expires_at_ms: int) -> None:
    """Persist Anthropic PKCE creds to file and credential pool."""
    import uuid as _uuid
    try:
        from agent.anthropic_adapter import _INTELLECT_OAUTH_FILE
    except ImportError:
        _INTELLECT_OAUTH_FILE = Path(_get_active_intellect_home()) / ".anthropic_oauth.json"

    payload = {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": expires_at_ms,
    }
    oauth_file = Path(_INTELLECT_OAUTH_FILE)
    oauth_file.parent.mkdir(parents=True, exist_ok=True)
    oauth_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Also write to auth.json credential pool for runtime resolution.
    home = _get_active_intellect_home()
    auth_path = home / "auth.json"
    auth = _read_auth_json(auth_path)
    auth.setdefault("version", 1)
    pool = auth.setdefault("credential_pool", {})
    if not isinstance(pool, dict):
        pool = {}
        auth["credential_pool"] = pool
    entries = pool.setdefault("anthropic", [])
    if not isinstance(entries, list):
        entries = []
        pool["anthropic"] = entries

    entries[:] = [e for e in entries if not (
        isinstance(e, dict) and e.get("source") in {"manual:dashboard_pkce", "dashboard_pkce"}
    )]

    now_iso = _now_iso()
    entry = {
        "id": "anthropic-pkce-" + _uuid.uuid4().hex[:12],
        "label": "Anthropic PKCE (WebUI)",
        "auth_type": "oauth",
        "priority": 0,
        "source": "manual:dashboard_pkce",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at_ms": expires_at_ms,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    entries.insert(0, entry)
    auth["updated_at"] = now_iso
    _write_auth_json(auth, auth_path)
    try:
        from api.config import invalidate_credential_pool_cache
        invalidate_credential_pool_cache("anthropic")
    except Exception:
        logger.debug('non-critical operation failed', exc_info=True)


def _submit_anthropic_pkce(session_id: str, code_input: str) -> dict[str, Any]:
    """Exchange authorization code for tokens. Persists on success."""
    consts = _get_anthropic_oauth_constants()
    if consts is None:
        raise RuntimeError("Anthropic OAuth not available (missing adapter)")
    client_id, token_url, redirect_uri, _scopes = consts

    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(session_id)
    if not sess or sess["provider"] != "anthropic" or sess["flow"] != "pkce":
        raise ValueError("Unknown or expired session")
    if sess["status"] != "pending":
        return {"ok": False, "status": sess["status"], "message": sess.get("error_message")}

    parts = code_input.strip().split("#", 1)
    code = parts[0].strip()
    if not code:
        return {"ok": False, "status": "error", "message": "No code provided"}

    exchange_data = json.dumps({
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "state": sess.get("state", ""),
        "redirect_uri": redirect_uri,
        "code_verifier": sess["verifier"],
    }).encode()

    req = urllib.request.Request(
        token_url,
        data=exchange_data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "intellect-webui/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode())
    except Exception as e:
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = f"Token exchange failed: {e}"
        return {"ok": False, "status": "error", "message": sess["error_message"]}

    access_token = result.get("access_token", "")
    refresh_token = result.get("refresh_token", "")
    expires_in = int(result.get("expires_in") or 3600)
    if not access_token:
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = "No access token returned"
        return {"ok": False, "status": "error", "message": sess["error_message"]}

    expires_at_ms = int(time.time() * 1000) + (expires_in * 1000)
    try:
        _save_anthropic_oauth_creds(access_token, refresh_token, expires_at_ms)
    except Exception as e:
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = f"Save failed: {e}"
        return {"ok": False, "status": "error", "message": sess["error_message"]}

    with _oauth_sessions_lock:
        sess["status"] = "approved"
    logger.info("oauth/pkce: anthropic login completed (session=%s)", session_id)
    return {"ok": True, "status": "approved"}


# ── Device code flow (Codex + MiniMax) ──────────────────────────────────────

_CODEX_WORKER_USER_CODE_TIMEOUT_SECONDS = 10


def _codex_device_start_only(session_id: str) -> dict[str, Any]:
    """Start a Codex device-code flow without starting a background worker.
    The caller spawns the worker separately. Returns the user-facing display fields.
    """
    device = _request_codex_user_code()
    user_code = str(device.get("user_code") or "").strip()
    device_auth_id = str(device.get("device_auth_id") or "").strip()
    if not user_code or not device_auth_id:
        raise RuntimeError("Device code response missing required fields")

    interval = max(3, int(device.get("interval") or 5))
    expires_in = int(device.get("expires_in") or CODEX_FLOW_MAX_WAIT_SECONDS)
    expires_at = time.time() + min(max(expires_in, 60), CODEX_FLOW_MAX_WAIT_SECONDS)

    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(session_id)
        if sess:
            sess["user_code"] = user_code
            sess["device_auth_id"] = device_auth_id
            sess["verification_url"] = CODEX_VERIFICATION_URI
            sess["expires_at"] = expires_at
            sess["interval"] = interval

    return {
        "session_id": session_id,
        "flow": "device_code",
        "user_code": user_code,
        "verification_url": CODEX_VERIFICATION_URI,
        "expires_in": expires_in,
        "poll_interval": interval,
    }


def _codex_oauth_runtime_worker(session_id: str) -> None:
    """Run the full Codex device-code flow in a background thread.
    Polls for authorization, exchanges code for tokens, and persists credentials.
    """
    intellect_home = _get_active_intellect_home()
    try:
        with _oauth_sessions_lock:
            sess = _oauth_sessions.get(session_id)
        if not sess:
            return

        interval = max(1, int(sess.get("interval") or 5))
        device_auth_id = str(sess.get("device_auth_id") or "")
        user_code = str(sess.get("user_code") or "")

        while True:
            with _oauth_sessions_lock:
                sess = dict(_oauth_sessions.get(session_id) or {})
            if sess.get("status") != "pending":
                return
            if float(sess.get("expires_at") or 0) <= time.time():
                _set_oauth_session_error(session_id, "Device code expired")
                return

            time.sleep(max(1, interval))

            with _oauth_sessions_lock:
                live = dict(_oauth_sessions.get(session_id) or {})
            if live.get("status") != "pending":
                return

            code_resp = _poll_codex_authorization(device_auth_id, user_code)
            if code_resp is None:
                continue
            authorization_code = str(code_resp.get("authorization_code") or "").strip()
            code_verifier = str(code_resp.get("code_verifier") or "").strip()
            if not authorization_code or not code_verifier:
                _set_oauth_session_error(session_id, "Device auth response missing fields")
                return
            tokens = _exchange_codex_authorization(authorization_code, code_verifier)
            _persist_codex_credentials(intellect_home, tokens)
            with _oauth_sessions_lock:
                sess = _oauth_sessions.get(session_id)
                if sess:
                    sess["status"] = "approved"
            logger.info("oauth/device: codex login completed (session=%s)", session_id)
            return
    except Exception as exc:
        logger.warning("Codex OAuth runtime worker failed: %s", exc)
        _set_oauth_session_error(session_id, str(exc))


def _poll_oauth_session(provider_id: str, session_id: str) -> dict[str, Any]:
    """Poll the status of an in-flight OAuth session."""
    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(session_id)
    if not sess or sess["provider"] != provider_id:
        raise KeyError(f"Session not found: {session_id}")
    return {
        "session_id": session_id,
        "status": sess.get("status", "error"),
        "error_message": sess.get("error_message"),
        "expires_at": sess.get("expires_at"),
    }


def _cancel_oauth_session(session_id: str) -> dict[str, Any]:
    """Cancel an in-flight OAuth session."""
    with _oauth_sessions_lock:
        sess = _oauth_sessions.pop(session_id, None)
        # Also check legacy flows dict for backward compat
        flow = _OAUTH_FLOWS.pop(session_id, None)
    if sess:
        sess["status"] = "cancelled"
    if flow:
        flow["status"] = "cancelled"
        _drop_sensitive_flow_fields(flow)
    return {"ok": True, "session_id": session_id}


# ── OAuth route dispatchers ─────────────────────────────────────────────────

def _start_oauth_login(provider_id: str) -> dict[str, Any]:
    """Start an OAuth login flow based on the provider's flow type."""
    _gc_oauth_sessions()
    catalog_entry = next((p for p in _OAUTH_PROVIDER_CATALOG if p["id"] == provider_id), None)
    if catalog_entry is None:
        raise ValueError(f"Unknown provider: {provider_id}")

    flow = catalog_entry["flow"]
    if flow == "external":
        raise ValueError(
            f"{provider_id} uses an external CLI. Run `{catalog_entry['cli_command']}` manually."
        )
    if flow == "pkce":
        if provider_id == "anthropic":
            return _start_anthropic_pkce()
        if provider_id == "xai-oauth":
            raise ValueError("xAI Grok OAuth in-browser flow is not yet supported. Use CLI.")
        raise ValueError(f"PKCE flow not implemented for {provider_id}")
    if flow == "device_code":
        if provider_id == "openai-codex":
            sid, _sess = _new_oauth_session("openai-codex", "device_code")
            # Start the device flow in the background; the worker writes user_code into the session.
            threading.Thread(
                target=_codex_oauth_runtime_worker,
                args=(sid,),
                daemon=True,
                name=f"oauth-codex-{sid[:6]}",
            ).start()
            # Populate session with initial device data from a blocking call.
            return _codex_device_start_only(sid)
        if provider_id == "minimax-oauth":
            return _start_minimax_device_flow()
        raise ValueError(f"Device code flow not supported for {provider_id}")
    raise ValueError(f"Unsupported flow type: {flow}")


# ── MiniMax OAuth provider ───────────────────────────────────────────────────

MINIMAX_OAUTH_CLIENT_ID = "78257093-7e40-4613-99e0-527b14b39113"
MINIMAX_OAUTH_GLOBAL_BASE = "https://api.minimax.io"
MINIMAX_OAUTH_GLOBAL_INFERENCE = "https://api.minimax.io/anthropic"
MINIMAX_OAUTH_SCOPE = "group_id profile model.completion"


def _start_minimax_device_flow() -> dict[str, Any]:
    """Start a MiniMax device-code OAuth flow."""
    try:
        from intellect_cli.auth import (
            _minimax_pkce_pair,
            _minimax_request_user_code,
        )
        import httpx
    except ImportError as e:
        raise RuntimeError(f"MiniMax OAuth requires intellect_cli.auth: {e}")

    verifier, challenge, state = _minimax_pkce_pair()
    portal_base_url = (
        os.environ.get("MINIMAX_PORTAL_BASE_URL") or MINIMAX_OAUTH_GLOBAL_BASE
    ).rstrip("/")
    client_id = os.environ.get("MINIMAX_OAUTH_CLIENT_ID") or MINIMAX_OAUTH_CLIENT_ID

    with httpx.Client(
        timeout=httpx.Timeout(15.0),
        headers={"Accept": "application/json"},
        follow_redirects=True,
    ) as client:
        device_data = _minimax_request_user_code(
            client=client,
            portal_base_url=portal_base_url,
            client_id=client_id,
            code_challenge=challenge,
            state=state,
        )

    sid, sess = _new_oauth_session("minimax-oauth", "device_code")
    sess["user_code"] = str(device_data["user_code"])
    sess["code_verifier"] = verifier
    sess["state"] = state
    sess["portal_base_url"] = portal_base_url
    sess["client_id"] = client_id
    verify_key = next(
        (k for k in ("verification_uri", "verification_url") if k in device_data), None
    )
    sess["verification_url"] = str(device_data[verify_key]) if verify_key else portal_base_url

    expired_in_raw = int(device_data["expired_in"])
    if expired_in_raw > 1_000_000_000_000:
        expires_at_ts = expired_in_raw / 1000.0
        expires_in_seconds = max(0, int(expires_at_ts - time.time()))
    else:
        expires_at_ts = time.time() + expired_in_raw
        expires_in_seconds = expired_in_raw
    sess["expires_at"] = expires_at_ts
    sess["expired_in_raw"] = expired_in_raw

    interval_raw = device_data.get("interval")
    sess["interval_ms"] = int(interval_raw) if interval_raw is not None else None

    threading.Thread(
        target=_run_minimax_poller,
        args=(sid,),
        daemon=True,
        name=f"oauth-minimax-{sid[:6]}",
    ).start()

    return {
        "session_id": sid,
        "flow": "device_code",
        "user_code": str(device_data["user_code"]),
        "verification_url": sess["verification_url"],
        "expires_in": expires_in_seconds,
        "poll_interval": max(2, (sess.get("interval_ms") or 2000) // 1000),
    }


def _run_minimax_poller(session_id: str) -> None:
    """Background poller that drives a MiniMax OAuth flow to completion."""
    try:
        from intellect_cli.auth import (
            _minimax_poll_token,
            _minimax_resolve_token_expiry_unix,
            _minimax_save_auth_state,
        )
        import httpx
    except ImportError:
        _set_oauth_session_error(session_id, "MiniMax poller requires intellect_cli.auth")
        return

    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(session_id)
    if not sess:
        return

    try:
        with httpx.Client(
            timeout=httpx.Timeout(15.0),
            headers={"Accept": "application/json"},
            follow_redirects=True,
        ) as client:
            token_data = _minimax_poll_token(
                client=client,
                portal_base_url=sess["portal_base_url"],
                client_id=sess["client_id"],
                user_code=sess["user_code"],
                code_verifier=sess["code_verifier"],
                expired_in=sess["expired_in_raw"],
                interval_ms=sess.get("interval_ms"),
            )

        now = datetime.now(timezone.utc)
        expires_at_ts = _minimax_resolve_token_expiry_unix(
            int(token_data["expired_in"]), now=now,
        )
        expires_in_s = max(0, int(expires_at_ts - now.timestamp()))
        auth_state = {
            "provider": "minimax-oauth",
            "region": "global",
            "portal_base_url": sess["portal_base_url"],
            "inference_base_url": MINIMAX_OAUTH_GLOBAL_INFERENCE,
            "client_id": sess["client_id"],
            "scope": MINIMAX_OAUTH_SCOPE,
            "token_type": token_data.get("token_type", "Bearer"),
            "access_token": token_data["access_token"],
            "refresh_token": token_data["refresh_token"],
            "resource_url": token_data.get("resource_url"),
            "obtained_at": now.isoformat(),
            "expires_at": datetime.fromtimestamp(expires_at_ts, tz=timezone.utc).isoformat(),
            "expires_in": expires_in_s,
        }
        _minimax_save_auth_state(auth_state)
        with _oauth_sessions_lock:
            sess["status"] = "approved"
        logger.info("oauth/device: minimax login completed (session=%s)", session_id)
    except Exception as e:
        logger.warning("minimax poller failed (session=%s): %s", session_id, e)
        _set_oauth_session_error(session_id, str(e))
