"""Centralised Rust extension imports and availability flags.

Instead of scattering ``try: from intellect_community_core import ...`` blocks
across every module, import flags from here::

    from intellect_rust import HAS_SANDBOX, rust_detect_dangerous

All imports are evaluated once at module load time.  When the native
extension is not installed, every flag is ``False`` and every function
alias is ``None``.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Optional

# ── Try the single import that gates everything ─────────────────────────────

def _load_core():
    """Import the native extension, or None if unavailable."""
    try:
        import intellect_community_core as core  # type: ignore[import-not-found]
        getattr(core, "detect_hardline_command_rs")
        return core
    except (ImportError, AttributeError, ModuleNotFoundError):
        return None


def _purge_core_modules() -> None:
    for key in list(sys.modules):
        if key == "intellect_community_core" or key.startswith(
            "intellect_community_core."
        ):
            sys.modules.pop(key, None)


def _import_core():
    core = _load_core()
    if core is not None:
        return core

    # maturin installs the compiled module into site-packages, but the repo
    # also ships a stub at ./intellect_community_core/ (see pyproject.toml
    # [tool.maturin] python-source).  When cwd is the checkout root,
    # sys.path[0] == "" resolves that stub before site-packages.  Retry
    # without the implicit cwd entry — same effect as ``python -P``.
    if not sys.path or sys.path[0] != "":
        return None

    _purge_core_modules()
    saved_path = sys.path
    try:
        sys.path = sys.path[1:]
        return _load_core()
    finally:
        sys.path = saved_path


_CORE = _import_core()


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
        "This is a required dependency since v0.6.2. "
        "Build it with: cd rust-core && maturin develop --release"
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

# ── Display formatting (Phase 2) ─────────────────────────────────────────────

HAS_FORMAT: bool = _has()
rust_format_duration_compact: Callable = (
    _CORE.format_duration_compact_rs if _has() else None
)
rust_format_token_count_compact: Callable = (
    _CORE.format_token_count_compact_rs if _has() else None
)

# ── Token estimation (Phase 5) ──────────────────────────────────────────────

HAS_TOKENS: bool = _has()
rust_estimate_tokens_rough: Callable = _CORE.estimate_tokens_rough_rs if _has() else None
rust_grok_supports_re: Callable = _CORE.grok_supports_reasoning_effort_rs if _has() else None
rust_parse_context_limit: Callable = _CORE.parse_context_limit_from_error_rs if _has() else None
rust_parse_output_limit: Callable = _CORE.parse_available_output_tokens_from_error_rs if _has() else None
rust_strip_provider_prefix: Callable = _CORE.strip_provider_prefix_rs if _has() else None
rust_model_name_suggests_kimi: Callable = _CORE.model_name_suggests_kimi_rs if _has() else None
rust_model_id_matches: Callable = _CORE.model_id_matches_rs if _has() else None
rust_normalize_model_version: Callable = _CORE.normalize_model_version_rs if _has() else None
rust_get_next_probe_tier: Callable = _CORE.get_next_probe_tier_rs if _has() else None

# ── Sanitization (Phase 4) ──────────────────────────────────────────────────

HAS_SANITIZE: bool = _has()
rust_sanitize_surrogates: Callable = _CORE.sanitize_surrogates_rs if _has() else None
rust_strip_non_ascii: Callable = _CORE.strip_non_ascii_rs if _has() else None
rust_escape_json_chars: Callable = _CORE.escape_invalid_chars_in_json_strings_rs if _has() else None
rust_repair_tool_args: Callable = _CORE.repair_tool_call_arguments_rs if _has() else None

# ── Error classifier (Phase 3) — API error taxonomy ────────────────────────

HAS_ERROR_CLASSIFIER: bool = _has()
rust_classify_api_error: Callable = (
    _CORE.classify_api_error_rs if _has() else None
)
RustFailoverReason: Any = _CORE.FailoverReason if _has() else None
RustClassifiedError: Any = _CORE.ClassifiedError if _has() else None

# ── Counters (Phase 1) — iteration budget + jittered backoff ───────────────

HAS_COUNTERS: bool = _has()
IterationBudget: Any = _CORE.IterationBudget if _has() else None
rust_jittered_backoff: Callable = (
    _CORE.jittered_backoff_rs if _has() else None
)

# ── Prompt caching ──────────────────────────────────────────────────────

rust_apply_cache_control: Callable = (
    _CORE.apply_anthropic_cache_control_rs if _has() else None
)

# ── Tool utilities ──────────────────────────────────────────────────────

rust_file_mutation_landed: Callable = (
    _CORE.file_mutation_result_landed_rs if _has() else None
)
rust_strip_yaml_frontmatter: Callable = (
    _CORE.strip_yaml_frontmatter_rs if _has() else None
)
rust_truncate_content: Callable = (
    _CORE.truncate_content_rs if _has() else None
)
rust_paths_overlap: Callable = (
    _CORE.paths_overlap_rs if _has() else None
)
rust_canonical_tool_args: Callable = (
    _CORE.canonical_tool_args_rs if _has() else None
)

# ── Model normalization ───────────────────────────────────────────────────

rust_normalize_model_name: Callable = (
    _CORE.normalize_model_name_rs if _has() else None
)

# ── SQLiteBackend class ────────────────────────────────────────────────────

SQLiteBackend: Any = _CORE.SQLiteBackend if _has() else None


# Rust extension is mandatory since v0.6.2 — no fallbacks needed.
# ensure_rust_available() is called at startup and will raise RuntimeError
# if the native extension is missing, failing fast with a clear message.
