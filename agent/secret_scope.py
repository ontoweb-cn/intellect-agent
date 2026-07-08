"""Profile-scoped credential resolution for multi-profile gateway multiplexing.

The multiplexing gateway serves many profiles from one process. Each profile
has its own ``.env`` with its own provider keys and platform tokens, so we
**cannot** union them into the process-global ``os.environ`` (that would leak
profile A's keys to profile B's turns, and to every subprocess spawned with
``env=dict(os.environ)``).

This module provides a fail-closed, context-local secret scope:

- ``set_secret_scope(mapping)`` installs the active profile's secrets for the
  current task (a contextvar, so it propagates into the agent's worker thread
  via ``copy_context()`` exactly like the INTELLECT_HOME override).
- ``get_secret(name)`` reads from that scope. When multiplexing is **active**
  and no scope is set, it RAISES rather than silently falling back to
  ``os.environ`` — an un-migrated or newly-added call site fails loud at that
  exact line instead of leaking another profile's value. When multiplexing is
  **off** (the default), it transparently reads ``os.environ`` so the
  single-profile gateway and every non-gateway caller behave exactly as before.
"""
from __future__ import annotations

import os
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Dict, Mapping, Optional


# ── multiplex-active flag ────────────────────────────────────────────────
# Process-global: set once at gateway startup when gateway.multiplex_profiles
# is true. Governs whether get_secret() fails closed on an unscoped read.
_MULTIPLEX_ACTIVE: bool = False


def set_multiplex_active(active: bool) -> None:
    """Mark whether the process is running as a profile multiplexer."""
    global _MULTIPLEX_ACTIVE
    _MULTIPLEX_ACTIVE = bool(active)


def is_multiplex_active() -> bool:
    """Return whether the process is running as a profile multiplexer."""
    return _MULTIPLEX_ACTIVE


# ── the secret scope contextvar ──────────────────────────────────────────
_SECRET_SCOPE: ContextVar[Optional[Mapping[str, str]]] = ContextVar(
    "_SECRET_SCOPE", default=None
)


class UnscopedSecretError(RuntimeError):
    """Raised when a secret is read in multiplex mode with no scope installed."""


def set_secret_scope(secrets: Optional[Mapping[str, str]]) -> Token:
    """Install the active profile's secret mapping for the current context."""
    return _SECRET_SCOPE.set(secrets)


def reset_secret_scope(token: Token) -> None:
    """Restore the previous secret scope."""
    _SECRET_SCOPE.reset(token)


def current_secret_scope() -> Optional[Mapping[str, str]]:
    """Return the active secret mapping, or None when no scope is installed."""
    return _SECRET_SCOPE.get()


# ── genuinely-global env vars (NOT per-profile secrets) ──────────────────
_GLOBAL_ENV_EXACT = frozenset({
    # Intellect runtime / deployment
    "INTELLECT_HOME", "intellect_PROFILE", "intellect_CONFIG", "intellect_ENV",
    "intellect_MAX_ITERATIONS", "intellect_MAX_TOKENS", "intellect_API_TIMEOUT",
    "intellect_REDACT_SECRETS", "intellect_NOUS_TIMEOUT_SECONDS",
    "intellect_CRON_TIMEOUT", "intellect_CRON_AUTO_DELIVER_PLATFORM",
    "intellect_CRON_AUTO_DELIVER_CHAT_ID", "intellect_CRON_AUTO_DELIVER_THREAD_ID",
    "intellect_MODEL", "intellect_PREFILL_MESSAGES_FILE",
    "_INTELLECT_GATEWAY",
    # OS / interpreter
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ", "PWD", "SHELL", "TMPDIR",
    "VIRTUAL_ENV", "PYTHONPATH", "SSL_CERT_FILE",
    # Kanban paths (per-board, not per-profile-secret)
    "INTELLECT_KANBAN_DB", "INTELLECT_KANBAN_WORKSPACES_ROOT", "INTELLECT_KANBAN_BOARD",
})
_GLOBAL_ENV_PREFIXES = (
    "INTELLECT_KANBAN_",
    "intellect_TELEGRAM_",   # tuning knobs — NOT the bot token
    "TERMINAL_",             # terminal/sandbox backend settings
)


def _is_global_env(name: str) -> bool:
    """Return True for genuinely process-global (non-profile-secret) env vars."""
    if name in _GLOBAL_ENV_EXACT:
        return True
    return any(name.startswith(p) for p in _GLOBAL_ENV_PREFIXES)


def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    """Resolve a credential by env-var name, honoring the active profile scope."""
    if _is_global_env(name):
        val = os.environ.get(name)
        return val if val is not None else default

    scope = _SECRET_SCOPE.get()
    if scope is not None:
        val = scope.get(name)
        return val if val is not None else default

    if _MULTIPLEX_ACTIVE:
        raise UnscopedSecretError(
            f"get_secret({name!r}) called with no profile secret scope active "
            f"while multiplexing is on. Wrap the call path in "
            f"set_secret_scope(...) so credentials do not leak across profiles."
        )

    val = os.environ.get(name)
    return val if val is not None else default


def load_env_file(env_path: Path) -> Dict[str, str]:
    """Parse a ``.env`` file into a plain dict WITHOUT touching ``os.environ``."""
    secrets: Dict[str, str] = {}
    try:
        text = env_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return secrets

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        secrets[key] = value

    return secrets


def build_profile_secret_scope(intellect_home: Path) -> Dict[str, str]:
    """Build a profile's secret mapping from its ``<home>/.env``."""
    return load_env_file(Path(intellect_home) / ".env")
