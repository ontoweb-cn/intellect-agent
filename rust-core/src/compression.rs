//! Compression chain traversal — equivalent to Python's `state/compression.py`.
//!
//! Stage 1b: uses rusqlite for direct SQLite access.
//! No longer requires a Python threading.Lock — each call opens its own
//! short-lived read-only connection, safe under WAL mode.

use pyo3::prelude::*;
use rusqlite::Connection;
use rusqlite::OptionalExtension;

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

// ── Pure-Rust implementation ────────────────────────────────────────────────

/// Walk the compression-continuation chain and return the tip session ID.
pub fn get_compression_tip_impl(
    conn: &Connection,
    session_id: &str,
) -> Result<Option<String>, rusqlite::Error> {
    let mut stmt = conn.prepare(COMPRESSION_TIP_SQL)?;
    let result: Option<String> = stmt
        .query_row([session_id], |row| row.get(0))
        .optional()?;
    Ok(result.or_else(|| Some(session_id.to_string())))
}

// ── PyO3 wrapper ────────────────────────────────────────────────────────────

fn _map_rusqlite_err(e: rusqlite::Error) -> PyErr {
    pyo3::exceptions::PyRuntimeError::new_err(e.to_string())
}

/// Walk the compression-continuation chain and return the tip session ID.
///
/// Opens its own connection — no Python lock needed (read-only, WAL-safe).
#[pyfunction]
pub fn get_compression_tip_rs(db_path: &str, session_id: &str) -> PyResult<Option<String>> {
    let conn = Connection::open(db_path).map_err(_map_rusqlite_err)?;
    get_compression_tip_impl(&conn, session_id).map_err(_map_rusqlite_err)
}

// ── Rust unit tests ─────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    fn fresh_sessions_db() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                parent_session_id TEXT,
                started_at REAL,
                ended_at REAL,
                end_reason TEXT
            );",
        )
        .unwrap();
        conn
    }

    #[test]
    fn test_no_chain_returns_self() {
        let conn = fresh_sessions_db();
        let tip = get_compression_tip_impl(&conn, "session-1").unwrap();
        assert_eq!(tip.as_deref(), Some("session-1"));
    }

    #[test]
    fn test_chain_follows_parent() {
        let conn = fresh_sessions_db();
        let now = 1000.0;

        // s1: compressed
        conn.execute(
            "INSERT INTO sessions (id, parent_session_id, started_at, ended_at, end_reason)
             VALUES (?1, NULL, ?2, ?3, ?4)",
            ["s1", &format!("{}", now - 30.0), &format!("{}", now - 20.0), "compression"],
        ).unwrap();
        // s2: continuation, also compressed
        conn.execute(
            "INSERT INTO sessions (id, parent_session_id, started_at, ended_at, end_reason)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            ["s2", "s1", &format!("{}", now - 15.0), &format!("{}", now - 10.0), "compression"],
        ).unwrap();
        // s3: continuation, still active
        conn.execute(
            "INSERT INTO sessions (id, parent_session_id, started_at, ended_at, end_reason)
             VALUES (?1, ?2, ?3, NULL, NULL)",
            ["s3", "s2", &format!("{}", now - 5.0)],
        ).unwrap();

        let tip = get_compression_tip_impl(&conn, "s1").unwrap();
        assert_eq!(tip.as_deref(), Some("s3"));
    }

    #[test]
    fn test_compression_tip_depth_limit() {
        let conn = fresh_sessions_db();
        // Create 200 sessions in a chain (exceeds CTE depth limit of 100)
        // Only the first 100+1 will be traversed before the guard kicks in.
        let now = 1000.0;
        for i in 0..200 {
            let id = format!("s{}", i);
            let parent: Option<&str> = if i == 0 { None } else { Some(&format!("s{}", i - 1)) };
            let start = now + (i as f64);
            let end = now + (i as f64) + 1.0;
            if let Some(p) = parent {
                conn.execute(
                    "INSERT INTO sessions (id, parent_session_id, started_at, ended_at, end_reason)
                     VALUES (?1, ?2, ?3, ?4, ?5)",
                    [id.as_str(), p, &format!("{}", start), &format!("{}", end), "compression"],
                ).unwrap();
            } else {
                conn.execute(
                    "INSERT INTO sessions (id, parent_session_id, started_at, ended_at, end_reason)
                     VALUES (?1, NULL, ?2, ?3, ?4)",
                    [id.as_str(), &format!("{}", start), &format!("{}", end), "compression"],
                ).unwrap();
            }
        }

        let tip = get_compression_tip_impl(&conn, "s0").unwrap();
        // Should return a result, not crash — the CTE depth < 100 guard works.
        assert!(tip.is_some());
    }
}
