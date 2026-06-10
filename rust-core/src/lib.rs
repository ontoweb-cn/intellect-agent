//! Intellect Agent core layer — Rust (PyO3) native extension.
//!
//! Stage 1b: Storage engine — FTS5 utilities, compression, schema constants.
//! Uses rusqlite (bundled SQLite) for direct database access.

pub mod compression;
pub mod fts;
pub mod schema;

use pyo3::prelude::*;

/// Python module: `import intellect_core`
#[pymodule]
fn intellect_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // FTS5 utilities (rusqlite-backed)
    m.add_function(wrap_pyfunction!(fts::is_fts5_unavailable_error, m)?)?;
    m.add_function(wrap_pyfunction!(fts::drop_fts_triggers_rs, m)?)?;
    m.add_function(wrap_pyfunction!(fts::fts_trigger_count_rs, m)?)?;
    m.add_function(wrap_pyfunction!(fts::rebuild_fts_indexes_rs, m)?)?;

    // Compression (rusqlite-backed — no lock needed)
    m.add_function(wrap_pyfunction!(compression::get_compression_tip_rs, m)?)?;

    Ok(())
}
