"""Centralised Rust extension imports and availability flags.

Instead of scattering ``try: from intellect_core import ...`` blocks
across every module, import flags from here::

    from intellect_rust import HAS_SANDBOX, rust_detect_dangerous

All imports are evaluated once at module load time.  When the native
extension is not installed, every flag is ``False`` and every function
alias is ``None``.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

# ── Try the single import that gates everything ─────────────────────────────

try:
    import intellect_core as _core  # type: ignore[import-not-found]
    _CORE = _core
except ImportError:
    _CORE = None


def _has() -> bool:
    return _CORE is not None


# ── Storage ──────────────────────────────────────────────────────────────────

HAS_BACKEND: bool = _has()
HAS_FTS: bool = _has()

# ── Sandbox (Stage 2) ──────────────────────────────────────────────────────

HAS_SANDBOX: bool = _has()
rust_detect_hardline: Optional[Callable] = (
    _CORE.detect_hardline_command_rs if _has() else None
)
rust_detect_dangerous: Optional[Callable] = (
    _CORE.detect_dangerous_command_rs if _has() else None
)
rust_check_sudo_stdin: Optional[Callable] = (
    _CORE.check_sudo_stdin_guard_rs if _has() else None
)

# ── Usage (Stage 3a/3b) ────────────────────────────────────────────────────

HAS_USAGE: bool = _has()
rust_normalize_usage: Optional[Callable] = (
    _CORE.normalize_usage_rs if _has() else None
)
TokenAccumulator: Any = _CORE.TokenAccumulator if _has() else None
HAS_TOKEN_ACC: bool = _has()

# ── Stream (Stage 3d) ──────────────────────────────────────────────────────

HAS_STREAM: bool = _has()
StreamAccumulator: Any = _CORE.StreamAccumulator if _has() else None

# ── Crypto (Stage 5) ───────────────────────────────────────────────────────

HAS_CRYPTO: bool = _has()
rust_pkce_challenge: Optional[Callable] = (
    _CORE.pkce_challenge if _has() else None
)
rust_pkce_from_verifier: Optional[Callable] = (
    _CORE.pkce_challenge_from_verifier if _has() else None
)
rust_secure_hex: Optional[Callable] = (
    _CORE.secure_token_hex if _has() else None
)
rust_fernet_encrypt: Optional[Callable] = (
    _CORE.fernet_encrypt if _has() else None
)
rust_fernet_decrypt: Optional[Callable] = (
    _CORE.fernet_decrypt if _has() else None
)
rust_generate_fernet_key: Optional[Callable] = (
    _CORE.generate_fernet_key if _has() else None
)
HAS_FERNET: bool = _has()

# ── Gateway (Stage 4a/4b) ──────────────────────────────────────────────────

HAS_GATEWAY: bool = _has()
rust_build_session_key: Optional[Callable] = (
    _CORE.build_session_key_rs if _has() else None
)
rust_evaluate_reset_policy: Optional[Callable] = (
    _CORE.evaluate_reset_policy_rs if _has() else None
)

# ── SQLiteBackend class ────────────────────────────────────────────────────

SQLiteBackend: Any = _CORE.SQLiteBackend if _has() else None
