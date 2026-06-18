"""Centralised Rust extension imports and availability flags.

Instead of scattering ``try: from intellect_community_core import ...`` blocks
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
    import intellect_community_core as _core  # type: ignore[import-not-found]
    _CORE = _core
except ImportError:
    _CORE = None


def _has() -> bool:
    return _CORE is not None


def ensure_rust_available() -> None:
    """Raise at startup if the Rust extension is missing.

    Call this once during application initialization to fail fast with a
    clear error message instead of silently falling back to Python.
    """
    if not _has():
        raise RuntimeError(
            "The intellect_community_core Rust extension is not installed. "
            "Build it with: cd rust-core && maturin develop --release"
        )


def _warn_once() -> None:
    """Emit a one-time warning when the Rust extension is absent."""
    if _has() or getattr(_warn_once, "_done", False):
        return
    _warn_once._done = True  # type: ignore[attr-defined]
    import logging
    _log = logging.getLogger(__name__)
    _log.warning(
        "intellect_community_core Rust extension is not installed. "
        "Storage, sandbox, crypto, and stream acceleration will use "
        "pure-Python fallbacks. For full performance, build it with: "
        "cd rust-core && maturin develop --release"
    )


# Emit the warning at import time (once per process).
_warn_once()


# ── Storage ──────────────────────────────────────────────────────────────────

HAS_BACKEND: bool = _has()
HAS_FTS: bool = _has()

# ── Sandbox (Stage 2) ──────────────────────────────────────────────────────

HAS_SANDBOX: bool = _has()
rust_detect_hardline: Callable = (
    _CORE.detect_hardline_command_rs if _has() else None
)
rust_detect_dangerous: Callable = (
    _CORE.detect_dangerous_command_rs if _has() else None
)
rust_check_sudo_stdin: Callable = (
    _CORE.check_sudo_stdin_guard_rs if _has() else None
)

# ── Usage (Stage 3a/3b) ────────────────────────────────────────────────────

HAS_USAGE: bool = _has()
rust_normalize_usage: Callable = (
    _CORE.normalize_usage_rs if _has() else None
)
TokenAccumulator: Any = _CORE.TokenAccumulator if _has() else None
HAS_TOKEN_ACC: bool = _has()

# ── Stream (Stage 3d) ──────────────────────────────────────────────────────

HAS_STREAM: bool = _has()
StreamAccumulator: Any = _CORE.StreamAccumulator if _has() else None

# ── Crypto (Stage 5) ───────────────────────────────────────────────────────

HAS_CRYPTO: bool = _has()
rust_pkce_challenge: Callable = (
    _CORE.pkce_challenge if _has() else None
)
rust_pkce_from_verifier: Callable = (
    _CORE.pkce_challenge_from_verifier if _has() else None
)
rust_secure_hex: Callable = (
    _CORE.secure_token_hex if _has() else None
)
rust_fernet_encrypt: Callable = (
    _CORE.fernet_encrypt if _has() else None
)
rust_fernet_decrypt: Callable = (
    _CORE.fernet_decrypt if _has() else None
)
rust_generate_fernet_key: Callable = (
    _CORE.generate_fernet_key if _has() else None
)
HAS_FERNET: bool = _has()

# ── Gateway (Stage 4a/4b) ──────────────────────────────────────────────────

HAS_GATEWAY: bool = _has()
rust_build_session_key: Callable = (
    _CORE.build_session_key_rs if _has() else None
)
rust_evaluate_reset_policy: Callable = (
    _CORE.evaluate_reset_policy_rs if _has() else None
)
rust_check_expiry_batch: Callable = (
    _CORE.check_session_expiry_batch_rs if _has() else None
)
HAS_BATCH_EXPIRY: bool = _has()
PlatformRetryScheduler: Any = (
    _CORE.PlatformRetryScheduler if _has() else None
)
HAS_RETRY_SCHEDULER: bool = _has()

# ── Compression ────────────────────────────────────────────────────────────

rust_get_compression_tip: Callable = (
    _CORE.get_compression_tip_rs if _has() else None
)

# ── FTS ────────────────────────────────────────────────────────────────────

rust_is_fts5_unavailable_error: Callable = (
    _CORE.is_fts5_unavailable_error if _has() else None
)
rust_drop_fts_triggers: Callable = (
    _CORE.drop_fts_triggers_rs if _has() else None
)
rust_fts_trigger_count: Callable = (
    _CORE.fts_trigger_count_rs if _has() else None
)
rust_rebuild_fts_indexes: Callable = (
    _CORE.rebuild_fts_indexes_rs if _has() else None
)

# ── Path safety ────────────────────────────────────────────────────────────

rust_is_forbidden_path: Callable = (
    _CORE.is_forbidden_path_rs if _has() else None
)

# ── URL / IP safety ────────────────────────────────────────────────────────

rust_is_ip_blocked: Callable = (
    _CORE.is_ip_blocked_rs if _has() else None
)

# ── Model normalization ───────────────────────────────────────────────────

rust_normalize_model_name: Callable = (
    _CORE.normalize_model_name_rs if _has() else None
)

# ── SQLiteBackend class ────────────────────────────────────────────────────

SQLiteBackend: Any = _CORE.SQLiteBackend if _has() else None


# ── Safe no-op fallbacks when the Rust extension is not installed ────────────
# When _CORE is None, every exported function/class above resolves to None.
# Callers that invoke None directly crash with "TypeError: 'NoneType' object
# is not callable".  Post-process the module globals to replace None-valued
# callables with safe no-op wrappers so the pure-Python fallback paths work
# transparently.
#
# Two fallback strategies:
#   identity(v, /)   — returns the first positional argument unchanged.
#                       Used for normalizers (rust_normalize_usage, etc.).
#   _NoneType()       — calling type(None)() returns None.
#                       Used for optional accelerator classes (StreamAccumulator,
#                       TokenAccumulator) where callers already guard with
#                       ``if obj is not None:`` on the result.
#   _missing()        — raises NotImplementedError.
#                       Used for crypto / FTS / sandbox where a missing Rust
#                       extension means the feature is genuinely unavailable.

def _identity(v, /, *args, **kwargs):
    """Return *v* unchanged; ignore all other arguments."""
    return v


class _NoneType:
    """Callable that returns ``None`` — a safe no-op for optional accelerators."""

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        return None


def _missing(*args: Any, **kwargs: Any) -> None:
    """Stub that surfaces a missing Rust extension clearly."""
    raise NotImplementedError(
        "This feature requires the intellect_community_core Rust extension. "
        "Build it with: cd rust-core && maturin develop --release"
    )


def _normalize_usage_fallback(
    mode: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_input_tokens: int,
    cache_creation_input_tokens: int,
    cached_detail: int,
    cache_write_detail: int,
    reasoning_tokens: int,
    *args: Any,
    **kwargs: Any,
) -> tuple[int, int, int, int, int]:
    """Pure-Python fallback for rust_normalize_usage.

    When the Rust extension is unavailable, return token counts as-is
    without provider-specific normalization.  The canonical format is
    (input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
    reasoning_tokens).
    """
    return (
        input_tokens,
        output_tokens,
        cache_read_input_tokens or cached_detail,
        cache_write_detail or cache_creation_input_tokens,
        reasoning_tokens,
    )


# Map of name → fallback.  Only applied when the current value is None.
_FALLBACKS: dict[str, Any] = {
    # Normalizers
    "rust_normalize_usage": _normalize_usage_fallback,
    "rust_normalize_model_name": _identity,  # name → unchanged
    # Optional accelerators (callers guard on result is not None)
    "StreamAccumulator": _NoneType(),
    "TokenAccumulator": _NoneType(),
    "SQLiteBackend": _NoneType(),
    "PlatformRetryScheduler": _NoneType(),
    # Everything else: raise NotImplementedError (crypto, sandbox, FTS, etc.)
}

for _name, _fallback in _FALLBACKS.items():
    if _name in globals() and globals()[_name] is None:
        globals()[_name] = _fallback
