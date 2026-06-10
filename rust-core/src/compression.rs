//! Compression chain traversal — equivalent to Python's `state/compression.py`.

use pyo3::prelude::*;
use pyo3::types::PyAnyMethods;

/// SQL for walking compression-continuation chains.
const COMPRESSION_TIP_SQL: &str = "\
WITH RECURSIVE chain AS (
    SELECT id, parent_session_id, started_at, 0 AS depth
    FROM sessions WHERE id = ?
    UNION ALL
    SELECT s.id, s.parent_session_id, s.started_at, c.depth + 1
    FROM sessions s
    JOIN chain c ON s.parent_session_id = c.id
    WHERE s.started_at >= (
        SELECT ended_at FROM sessions
        WHERE id = c.id AND end_reason = 'compression'
    )
    AND c.depth < 100
)
SELECT id FROM chain ORDER BY depth DESC LIMIT 1";

/// Walk the compression-continuation chain and return the tip session ID.
///
/// Python wrapper — takes a sqlite3 Connection, threading.Lock, and session_id.
/// Mirrors Python's `get_compression_tip()`.
#[pyfunction]
pub fn get_compression_tip_py(
    conn: &Bound<'_, PyAny>,
    lock: &Bound<'_, PyAny>,
    session_id: &str,
) -> PyResult<Option<String>> {
    // Acquire the Python lock (lock.__enter__())
    let _guard = lock.call_method0("__enter__")?;

    let cursor = conn.call_method("execute", (COMPRESSION_TIP_SQL, (session_id,)), None::<&Bound<'_, pyo3::types::PyDict>>)?;
    let row: Option<Bound<'_, PyAny>> = cursor.call_method0("fetchone")?.extract()?;

    // Release lock
    let _ = lock.call_method0("__exit__");

    match row {
        Some(r) => {
            let id: String = r.get_item("id")?.extract()?;
            Ok(Some(id))
        }
        None => Ok(Some(session_id.to_string())),
    }
}
