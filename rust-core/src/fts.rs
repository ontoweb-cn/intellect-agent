//! FTS5 utility functions — equivalent to Python's `state/fts.py`.
//!
//! Stage 1b: uses rusqlite (bundled SQLite) for direct database access.
//! Each function opens its own short-lived connection to `db_path`.

use pyo3::prelude::*;
use pyo3::types::PyAnyMethods;
use rusqlite::Connection;

use crate::schema;

// ── Pure-Rust implementations (testable without PyO3) ───────────────────────

/// Drop all known FTS triggers (idempotent).
pub fn drop_fts_triggers_impl(conn: &Connection) -> Result<(), rusqlite::Error> {
    for trigger in schema::FTS_TRIGGERS {
        let _ = schema::validate_fts_identifier(trigger, schema::FTS_TRIGGERS);
        let sql = format!("DROP TRIGGER IF EXISTS {trigger}");
        let _ = conn.execute(&sql, []);
    }
    Ok(())
}

/// Count how many of the expected FTS triggers exist.
pub fn fts_trigger_count_impl(conn: &Connection) -> Result<usize, rusqlite::Error> {
    let placeholders = schema::FTS_TRIGGERS
        .iter()
        .map(|_| "?")
        .collect::<Vec<_>>()
        .join(",");
    let sql = format!(
        "SELECT COUNT(*) FROM sqlite_master \
         WHERE type = 'trigger' AND name IN ({placeholders})"
    );
    conn.query_row(
        &sql,
        rusqlite::params_from_iter(schema::FTS_TRIGGERS.iter()),
        |row| row.get(0),
    )
}

/// Delete and re-populate the messages_fts index from messages table.
pub fn rebuild_fts_indexes_impl(conn: &Connection) -> Result<(), rusqlite::Error> {
    schema::validate_fts_identifier("messages_fts", schema::FTS_TABLES)
        .map_err(|e| rusqlite::Error::InvalidParameterName(e))?;
    conn.execute("DELETE FROM messages_fts", [])?;
    conn.execute(
        "INSERT INTO messages_fts(rowid, content) \
         SELECT id, \
         COALESCE(content, '') || ' ' || \
         COALESCE(tool_name, '') || ' ' || \
         COALESCE(tool_calls, '') \
         FROM messages",
        [],
    )?;
    Ok(())
}

// ── PyO3 wrappers ───────────────────────────────────────────────────────────

fn _map_rusqlite_err(e: rusqlite::Error) -> PyErr {
    pyo3::exceptions::PyRuntimeError::new_err(e.to_string())
}

/// Return True when the error indicates FTS5 module is missing.
/// (No DB access needed — inspects the Python exception string.)
#[pyfunction]
pub fn is_fts5_unavailable_error(exc: &Bound<'_, PyAny>) -> PyResult<bool> {
    let msg: String = exc
        .str()
        .map(|s| s.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    Ok(msg.contains("no such module") && msg.contains("fts5"))
}

/// Drop all known FTS triggers (idempotent).
#[pyfunction]
pub fn drop_fts_triggers_rs(db_path: &str) -> PyResult<()> {
    let conn = Connection::open(db_path).map_err(_map_rusqlite_err)?;
    drop_fts_triggers_impl(&conn).map_err(_map_rusqlite_err)
}

/// Count how many of the expected FTS triggers exist.
#[pyfunction]
pub fn fts_trigger_count_rs(db_path: &str) -> PyResult<usize> {
    let conn = Connection::open(db_path).map_err(_map_rusqlite_err)?;
    fts_trigger_count_impl(&conn).map_err(_map_rusqlite_err)
}

/// Delete and re-populate the messages_fts index from messages table.
#[pyfunction]
pub fn rebuild_fts_indexes_rs(db_path: &str) -> PyResult<()> {
    let conn = Connection::open(db_path).map_err(_map_rusqlite_err)?;
    rebuild_fts_indexes_impl(&conn).map_err(_map_rusqlite_err)
}

// ── Rust unit tests ─────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    fn fresh_db() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE VIRTUAL TABLE messages_fts USING fts5(content, tokenize='trigram');
             CREATE TABLE messages (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 content TEXT, tool_name TEXT, tool_calls TEXT
             );
             CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages BEGIN
                 INSERT INTO messages_fts(rowid, content) VALUES (
                     new.id, COALESCE(new.content,'')||' '||COALESCE(new.tool_name,'')||' '||COALESCE(new.tool_calls,'')
                 );
             END;
             CREATE TRIGGER messages_fts_delete AFTER DELETE ON messages BEGIN
                 DELETE FROM messages_fts WHERE rowid = old.id;
             END;
             CREATE TRIGGER messages_fts_update AFTER UPDATE ON messages BEGIN
                 DELETE FROM messages_fts WHERE rowid = old.id;
                 INSERT INTO messages_fts(rowid, content) VALUES (
                     new.id, COALESCE(new.content,'')||' '||COALESCE(new.tool_name,'')||' '||COALESCE(new.tool_calls,'')
                 );
             END;",
        )
        .unwrap();
        conn
    }

    #[test]
    fn test_drop_fts_triggers() {
        let conn = fresh_db();
        // All 3 triggers exist initially
        assert_eq!(fts_trigger_count_impl(&conn).unwrap(), 3);
        // Drop them
        drop_fts_triggers_impl(&conn).unwrap();
        assert_eq!(fts_trigger_count_impl(&conn).unwrap(), 0);
        // Idempotent
        drop_fts_triggers_impl(&conn).unwrap();
        assert_eq!(fts_trigger_count_impl(&conn).unwrap(), 0);
    }

    #[test]
    fn test_fts_trigger_count_empty_db() {
        let conn = Connection::open_in_memory().unwrap();
        assert_eq!(fts_trigger_count_impl(&conn).unwrap(), 0);
    }

    #[test]
    fn test_rebuild_fts_indexes() {
        let conn = fresh_db();
        conn.execute(
            "INSERT INTO messages (id, content, tool_name, tool_calls) VALUES (1, 'hello world', 'search', 'tool1')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO messages (id, content, tool_name, tool_calls) VALUES (2, 'foo bar', NULL, NULL)",
            [],
        )
        .unwrap();

        rebuild_fts_indexes_impl(&conn).unwrap();

        let count: usize = conn
            .query_row("SELECT COUNT(*) FROM messages_fts", [], |row| row.get(0))
            .unwrap();
        assert_eq!(count, 2);
    }

    #[test]
    fn test_rebuild_fts_indexes_idempotent() {
        let conn = fresh_db();
        conn.execute(
            "INSERT INTO messages (id, content, tool_name, tool_calls) VALUES (1, 'test', NULL, NULL)",
            [],
        )
        .unwrap();

        rebuild_fts_indexes_impl(&conn).unwrap();
        rebuild_fts_indexes_impl(&conn).unwrap();

        let count: usize = conn
            .query_row("SELECT COUNT(*) FROM messages_fts", [], |row| row.get(0))
            .unwrap();
        assert_eq!(count, 1);
    }
}
