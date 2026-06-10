//! FTS5 utility functions — equivalent to Python's `state/fts.py`.
//!
//! All functions are designed to be called from Python via PyO3,
//! operating on a raw SQLite connection handle.

use pyo3::prelude::*;
use pyo3::types::PyAnyMethods;

use crate::schema;

/// Return True when the error indicates FTS5 module is missing.
/// Mirrors Python's `is_fts5_unavailable_error()`.
#[pyfunction]
pub fn is_fts5_unavailable_error(exc: &Bound<'_, PyAny>) -> PyResult<bool> {
    let msg: String = exc
        .str()
        .map(|s| s.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    Ok(msg.contains("no such module") && msg.contains("fts5"))
}

/// Drop all known FTS triggers (idempotent).
/// Python wrapper for `drop_fts_triggers`.
#[pyfunction]
pub fn drop_fts_triggers_py(cursor: &Bound<'_, PyAny>) -> PyResult<()> {
    for trigger in schema::FTS_TRIGGERS {
        let _ = schema::validate_fts_identifier(trigger, schema::FTS_TRIGGERS);
        let sql = format!("DROP TRIGGER IF EXISTS {trigger}");
        // Best-effort: OperationalError is silently ignored.
        let _ = cursor.call_method1("execute", (sql.as_str(),));
    }
    Ok(())
}

/// Count how many of the expected FTS triggers exist.
/// Python wrapper for `fts_trigger_count`.
#[pyfunction]
pub fn fts_trigger_count_py(cursor: &Bound<'_, PyAny>) -> PyResult<usize> {
    let placeholders = schema::FTS_TRIGGERS
        .iter()
        .map(|_| "?")
        .collect::<Vec<_>>()
        .join(",");
    let sql = format!(
        "SELECT COUNT(*) FROM sqlite_master \
         WHERE type = 'trigger' AND name IN ({placeholders})"
    );
    let params: Vec<&str> = schema::FTS_TRIGGERS.to_vec();
    let row = cursor.call_method("execute", (sql.as_str(), params), None::<&Bound<'_, pyo3::types::PyDict>>)?;
    let result: Bound<'_, PyAny> = row.call_method0("fetchone")?;
    // Extract first column as int
    let count: usize = result
        .call_method1("__getitem__", (0,))?
        .extract()?;
    Ok(count)
}

/// Delete and re-populate the messages_fts index from messages table.
/// Python wrapper for `rebuild_fts_indexes`.
#[pyfunction]
pub fn rebuild_fts_indexes_py(cursor: &Bound<'_, PyAny>) -> PyResult<()> {
    schema::validate_fts_identifier("messages_fts", schema::FTS_TABLES)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e))?;

    cursor.call_method1("execute", ("DELETE FROM messages_fts",))?;
    cursor.call_method1(
        "execute",
        (
            "INSERT INTO messages_fts(rowid, content) \
             SELECT id, \
             COALESCE(content, '') || ' ' || \
             COALESCE(tool_name, '') || ' ' || \
             COALESCE(tool_calls, '') \
             FROM messages",
        ),
    )?;
    Ok(())
}
