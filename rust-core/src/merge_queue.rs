//! Write merge queue for SessionDB (HP-402).
//!
//! Batches multiple ``append_message`` calls into a single SQLite transaction,
//! coalescing ``UPDATE sessions SET message_count += N`` per session_id.

use pyo3::prelude::*;
use rusqlite::Connection;
use serde::Deserialize;
use std::sync::{Arc, Mutex};

use crate::backend::{_map_rusqlite_err, WRITE_MAX_RETRIES};

#[derive(Debug, Clone, Deserialize)]
struct MessageBatchEntry {
    session_id: String,
    role: String,
    #[serde(default)]
    content: Option<String>,
    #[serde(default)]
    tool_call_id: Option<String>,
    #[serde(default)]
    tool_calls_json: Option<String>,
    #[serde(default)]
    tool_name: Option<String>,
    #[serde(default)]
    timestamp: f64,
    #[serde(default)]
    token_count: Option<i64>,
    #[serde(default)]
    finish_reason: Option<String>,
    #[serde(default)]
    reasoning: Option<String>,
    #[serde(default)]
    reasoning_content: Option<String>,
    #[serde(default)]
    reasoning_details_json: Option<String>,
    #[serde(default)]
    codex_items_json: Option<String>,
    #[serde(default)]
    codex_message_items_json: Option<String>,
    #[serde(default)]
    platform_message_id: Option<String>,
    #[serde(default)]
    observed: bool,
    #[serde(default)]
    num_tool_calls: i64,
}

/// PyO3 wrapper: JSON array of message dicts → batch append → JSON array of row IDs.
#[pyfunction]
#[pyo3(signature = (entries_json, db_path))]
pub fn append_message_batch_rs(entries_json: &str, db_path: &str) -> PyResult<String> {
    let entries: Vec<MessageBatchEntry> =
        serde_json::from_str(entries_json).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("Invalid JSON: {}", e))
        })?;

    if entries.is_empty() {
        return Ok("[]".to_string());
    }

    // NOTE: opens a new connection per call. For single-threaded agent-loop
    // usage this is fine — each _flush_messages_to_session_db call is
    // serialized per session.  If concurrent batch writes become common
    // (gateway multiplex), switch to a shared connection pool keyed by db_path.
    let conn = Connection::open(db_path).map_err(_map_rusqlite_err)?;
    conn.execute_batch("PRAGMA journal_mode=WAL").ok();
    let conn_ref = Arc::new(Mutex::new(conn));

    let mut last_err: Option<PyErr> = None;

    for _attempt in 0..WRITE_MAX_RETRIES {
        match _append_batch_inner(&conn_ref, &entries) {
            Ok(ids) => return Ok(serde_json::to_string(&ids).unwrap_or_else(|_| "[]".to_string())),
            Err(e) => {
                let msg = e.to_string();
                if msg.contains("database is locked") || msg.contains("database is busy") {
                    last_err = Some(pyo3::exceptions::PyRuntimeError::new_err(msg));
                    std::thread::sleep(std::time::Duration::from_millis(
                        (20 + (rand::random::<u64>() % 130)) as u64,
                    ));
                    continue;
                }
                return Err(_map_rusqlite_err(e));
            }
        }
    }

    Err(last_err.unwrap_or_else(|| {
        pyo3::exceptions::PyRuntimeError::new_err("batch write failed after retries")
    }))
}

fn _append_batch_inner(
    conn_ref: &Arc<Mutex<Connection>>,
    entries: &[MessageBatchEntry],
) -> Result<Vec<i64>, rusqlite::Error> {
    let c = conn_ref.lock().unwrap();
    c.execute_batch("BEGIN IMMEDIATE")?;

    let mut row_ids: Vec<i64> = Vec::with_capacity(entries.len());

    for entry in entries {
        c.execute(
            "INSERT INTO messages (session_id, role, content, tool_call_id, \
             tool_calls, tool_name, timestamp, token_count, finish_reason, \
             reasoning, reasoning_content, reasoning_details, codex_reasoning_items, \
             codex_message_items, platform_message_id, observed) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16)",
            rusqlite::params![
                entry.session_id, entry.role, entry.content, entry.tool_call_id,
                entry.tool_calls_json, entry.tool_name, entry.timestamp, entry.token_count,
                entry.finish_reason, entry.reasoning, entry.reasoning_content,
                entry.reasoning_details_json, entry.codex_items_json,
                entry.codex_message_items_json, entry.platform_message_id,
                entry.observed as i64,
            ],
        )?;
        row_ids.push(c.last_insert_rowid());
    }

    // Coalesce session counters
    use std::collections::HashMap;
    let mut counts: HashMap<&str, (i64, i64)> = HashMap::new();
    for entry in entries {
        let (mc, tc) = counts.entry(entry.session_id.as_str()).or_insert((0, 0));
        *mc += 1;
        *tc += entry.num_tool_calls;
    }
    for (sid, (mc, tc)) in &counts {
        if *tc > 0 {
            c.execute(
                "UPDATE sessions SET message_count = message_count + ?1, \
                 tool_call_count = tool_call_count + ?2 WHERE id = ?3",
                rusqlite::params![mc, tc, sid],
            )?;
        } else {
            c.execute(
                "UPDATE sessions SET message_count = message_count + ?1 WHERE id = ?2",
                rusqlite::params![mc, sid],
            )?;
        }
    }

    c.execute_batch("COMMIT")?;
    Ok(row_ids)
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    fn setup_db() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_call_id TEXT,
                tool_calls TEXT,
                tool_name TEXT,
                timestamp REAL,
                token_count INTEGER,
                finish_reason TEXT,
                reasoning TEXT,
                reasoning_content TEXT,
                reasoning_details TEXT,
                codex_reasoning_items TEXT,
                codex_message_items TEXT,
                platform_message_id TEXT,
                observed INTEGER DEFAULT 0
            );
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                message_count INTEGER DEFAULT 0,
                tool_call_count INTEGER DEFAULT 0
            );
            INSERT INTO sessions (id, message_count) VALUES ('sess1', 0);
            INSERT INTO sessions (id, message_count) VALUES ('sess2', 0);"
        ).unwrap();
        conn
    }

    fn make_entry(session_id: &str, role: &str, content: &str) -> MessageBatchEntry {
        MessageBatchEntry {
            session_id: session_id.to_string(),
            role: role.to_string(),
            content: Some(content.to_string()),
            tool_call_id: None,
            tool_calls_json: None,
            tool_name: None,
            timestamp: 1000.0,
            token_count: None,
            finish_reason: None,
            reasoning: None,
            reasoning_content: None,
            reasoning_details_json: None,
            codex_items_json: None,
            codex_message_items_json: None,
            platform_message_id: None,
            observed: false,
            num_tool_calls: 0,
        }
    }

    #[test]
    fn test_batch_insert_multiple_messages() {
        let conn = setup_db();
        let conn_ref = Arc::new(Mutex::new(conn));
        let entries = vec![
            make_entry("sess1", "user", "hello"),
            make_entry("sess1", "assistant", "hi there"),
            make_entry("sess2", "user", "test"),
        ];
        let ids = _append_batch_inner(&conn_ref, &entries).unwrap();
        assert_eq!(ids.len(), 3);
        assert!(ids[0] > 0);
        assert!(ids[1] > ids[0]); // row IDs increase
    }

    #[test]
    fn test_batch_coalesces_session_counters() {
        let conn = setup_db();
        let conn_ref = Arc::new(Mutex::new(conn));
        let entries = vec![
            make_entry("sess1", "user", "a"),
            make_entry("sess1", "user", "b"),
            make_entry("sess1", "assistant", "c"),
        ];
        _append_batch_inner(&conn_ref, &entries).unwrap();

        let c = conn_ref.lock().unwrap();
        let count: i64 = c
            .query_row(
                "SELECT message_count FROM sessions WHERE id = 'sess1'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(count, 3, "session counter should be incremented by 3");
    }

    #[test]
    fn test_batch_empty_returns_empty() {
        let conn = setup_db();
        let conn_ref = Arc::new(Mutex::new(conn));
        let ids = _append_batch_inner(&conn_ref, &[]).unwrap();
        assert!(ids.is_empty());
    }

    #[test]
    fn test_batch_multiple_sessions_independent_counters() {
        let conn = setup_db();
        let conn_ref = Arc::new(Mutex::new(conn));
        let entries = vec![
            make_entry("sess1", "user", "a"),
            make_entry("sess2", "user", "b"),
            make_entry("sess2", "assistant", "c"),
        ];
        _append_batch_inner(&conn_ref, &entries).unwrap();

        let c = conn_ref.lock().unwrap();
        let c1: i64 = c.query_row("SELECT message_count FROM sessions WHERE id='sess1'", [], |r| r.get(0)).unwrap();
        let c2: i64 = c.query_row("SELECT message_count FROM sessions WHERE id='sess2'", [], |r| r.get(0)).unwrap();
        assert_eq!(c1, 1);
        assert_eq!(c2, 2);
    }
}
