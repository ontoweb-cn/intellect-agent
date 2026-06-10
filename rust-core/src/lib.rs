//! Intellect Agent core layer — Rust (PyO3) native extension.
//!
//! Stage 1: Storage engine — FTS5 utilities, compression, schema constants.
//! Uses PyO3 thin wrappers around Python sqlite3 for initial migration;
//! later stages will add rusqlite for direct SQLite access.

pub mod compression;
pub mod fts;
pub mod schema;

use pyo3::prelude::*;

/// Python module: `import intellect_core`
#[pymodule]
fn intellect_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // FTS utility functions
    m.add_function(wrap_pyfunction!(fts::is_fts5_unavailable_error, m)?)?;
    m.add_function(wrap_pyfunction!(fts::drop_fts_triggers_py, m)?)?;
    m.add_function(wrap_pyfunction!(fts::fts_trigger_count_py, m)?)?;
    m.add_function(wrap_pyfunction!(fts::rebuild_fts_indexes_py, m)?)?;

    // Compression
    m.add_function(wrap_pyfunction!(compression::get_compression_tip_py, m)?)?;

    Ok(())
}
