//! Intellect Agent core layer — Rust (PyO3) native extension.
//!
//! Stage 1c: SQLiteBackend with managed connection, write retry, WAL.
//! Includes RustConnection/RustCursor for Python callback compatibility.

pub mod backend;
pub mod compression;
pub mod connection;
pub mod crypto;
pub mod fts;
pub mod sandbox;
pub mod schema;
pub mod stream;
pub mod usage;

use pyo3::prelude::*;

/// Python module: `import intellect_core`
#[pymodule]
fn intellect_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
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

    // ── Stage 3a/3b: Usage normalization + accumulation ─────────────────
    m.add_function(wrap_pyfunction!(usage::normalize_usage_rs, m)?)?;
    m.add_class::<usage::TokenAccumulator>()?;

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

    Ok(())
}
