//! Intellect Agent core layer — Rust (PyO3) native extension.
//!
//! Stage 1c: SQLiteBackend with managed connection, write retry, WAL.
//! Includes RustConnection/RustCursor for Python callback compatibility.

pub mod backend;
pub mod compression;
pub mod connection;
pub mod counters;
pub mod crypto;
pub mod error_classifier;
pub mod fts;
pub mod gateway;
pub mod prompt_caching;
pub mod sandbox;
pub mod sanitize;
pub mod schema;
pub mod stream;
pub mod tokens;
pub mod tool_utils;
pub mod usage;

use pyo3::prelude::*;

/// Python module: `import intellect_community_core`
#[pymodule]
fn intellect_community_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // ── Standalone functions (Stage 1b compat — pass db_path) ────────────
    m.add_function(wrap_pyfunction!(fts::is_fts5_unavailable_error, m)?)?;
    m.add_function(wrap_pyfunction!(fts::drop_fts_triggers_rs, m)?)?;
    m.add_function(wrap_pyfunction!(fts::fts_trigger_count_rs, m)?)?;
    m.add_function(wrap_pyfunction!(fts::rebuild_fts_indexes_rs, m)?)?;
    m.add_function(wrap_pyfunction!(compression::get_compression_tip_rs, m)?)?;

    // ── Stage 1c: Managed backend ────────────────────────────────────────
    m.add_class::<backend::SQLiteBackend>()?;
    m.add_class::<connection::RustConnection>()?;
    m.add_class::<connection::RustCursor>()?;

    // ── Stage 2: Sandbox / security ─────────────────────────────────────
    m.add_function(wrap_pyfunction!(sandbox::detect_hardline_command_rs, m)?)?;
    m.add_function(wrap_pyfunction!(sandbox::detect_dangerous_command_rs, m)?)?;
    m.add_function(wrap_pyfunction!(sandbox::check_sudo_stdin_guard_rs, m)?)?;
    m.add_function(wrap_pyfunction!(sandbox::is_forbidden_path_rs, m)?)?;
    m.add_function(wrap_pyfunction!(sandbox::is_ip_blocked_rs, m)?)?;

    // ── Stage 3a/3b: Usage normalization + accumulation ─────────────────
    m.add_function(wrap_pyfunction!(usage::normalize_usage_rs, m)?)?;
    m.add_function(wrap_pyfunction!(usage::normalize_model_name_rs, m)?)?;
    m.add_class::<usage::TokenAccumulator>()?;

    // ── Phase 2: Display formatting ────────────────────────────────────
    m.add_function(wrap_pyfunction!(usage::format_duration_compact_rs, m)?)?;
    m.add_function(wrap_pyfunction!(usage::format_token_count_compact_rs, m)?)?;

    // ── Stage 3d: Stream delta accumulator ──────────────────────────────
    m.add_class::<stream::StreamAccumulator>()?;

    // ── Stage 5a/5e: Crypto — PKCE + secure random ──────────────────────
    m.add_function(wrap_pyfunction!(crypto::secure_random_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(crypto::secure_token_urlsafe, m)?)?;
    m.add_function(wrap_pyfunction!(crypto::secure_token_hex, m)?)?;
    m.add_function(wrap_pyfunction!(crypto::pkce_challenge, m)?)?;
    m.add_function(wrap_pyfunction!(crypto::pkce_challenge_from_verifier, m)?)?;

    // ── Stage 5b: Fernet encryption ────────────────────────────────────
    m.add_function(wrap_pyfunction!(crypto::fernet_encrypt, m)?)?;
    m.add_function(wrap_pyfunction!(crypto::fernet_decrypt, m)?)?;
    m.add_function(wrap_pyfunction!(crypto::generate_fernet_key, m)?)?;

    // ── Stage 5c: JWT claims decode ────────────────────────────────────
    m.add_function(wrap_pyfunction!(crypto::decode_jwt_claims_rs, m)?)?;

    // ── Phase 1: Counters — iteration budget + jittered backoff ────────
    m.add_class::<counters::IterationBudget>()?;
    m.add_function(wrap_pyfunction!(counters::jittered_backoff_rs, m)?)?;

    // ── Prompt caching ────────────────────────────────────────────────
    m.add_function(wrap_pyfunction!(prompt_caching::apply_anthropic_cache_control_rs, m)?)?;

    // ── Tool utilities ────────────────────────────────────────────────
    m.add_function(wrap_pyfunction!(tool_utils::file_mutation_result_landed_rs, m)?)?;
    m.add_function(wrap_pyfunction!(tool_utils::strip_yaml_frontmatter_rs, m)?)?;
    m.add_function(wrap_pyfunction!(tool_utils::truncate_content_rs, m)?)?;
    m.add_function(wrap_pyfunction!(tool_utils::paths_overlap_rs, m)?)?;
    m.add_function(wrap_pyfunction!(tool_utils::canonical_tool_args_rs, m)?)?;

    // ── Phase 3: Error classifier — API error taxonomy ─────────────────
    m.add_class::<error_classifier::FailoverReason>()?;
    m.add_class::<error_classifier::ClassifiedError>()?;
    m.add_function(wrap_pyfunction!(error_classifier::classify_api_error_rs, m)?)?;

    // ── Phase 4: Message sanitization (pure-string functions) ──────────
    m.add_function(wrap_pyfunction!(sanitize::sanitize_surrogates_rs, m)?)?;
    m.add_function(wrap_pyfunction!(sanitize::strip_non_ascii_rs, m)?)?;
    m.add_function(wrap_pyfunction!(sanitize::escape_invalid_chars_in_json_strings_rs, m)?)?;
    m.add_function(wrap_pyfunction!(sanitize::repair_tool_call_arguments_rs, m)?)?;

    // ── Phase 5: Token estimation ─────────────────────────────────────
    m.add_function(wrap_pyfunction!(tokens::estimate_tokens_rough_rs, m)?)?;
    m.add_function(wrap_pyfunction!(tokens::grok_supports_reasoning_effort_rs, m)?)?;
    m.add_function(wrap_pyfunction!(tokens::parse_context_limit_from_error_rs, m)?)?;
    m.add_function(wrap_pyfunction!(tokens::parse_available_output_tokens_from_error_rs, m)?)?;

    // ── M2: Model name helpers ────────────────────────────────────────
    m.add_function(wrap_pyfunction!(tokens::strip_provider_prefix_rs, m)?)?;
    m.add_function(wrap_pyfunction!(tokens::model_name_suggests_kimi_rs, m)?)?;
    m.add_function(wrap_pyfunction!(tokens::model_id_matches_rs, m)?)?;
    m.add_function(wrap_pyfunction!(tokens::normalize_model_version_rs, m)?)?;
    m.add_function(wrap_pyfunction!(tokens::get_next_probe_tier_rs, m)?)?;

    // ── Stage 4a-4e: Gateway utilities ─────────────────────────────────
    m.add_function(wrap_pyfunction!(gateway::build_session_key_rs, m)?)?;
    m.add_function(wrap_pyfunction!(gateway::evaluate_reset_policy_rs, m)?)?;
    m.add_function(wrap_pyfunction!(gateway::backoff_delay_rs, m)?)?;
    m.add_function(wrap_pyfunction!(gateway::check_session_expiry_batch_rs, m)?)?;
    m.add_function(wrap_pyfunction!(gateway::backoff_delay_batch_rs, m)?)?;
    m.add_class::<gateway::TokenBucket>()?;
    m.add_class::<gateway::PlatformRetryScheduler>()?;

    Ok(())
}
