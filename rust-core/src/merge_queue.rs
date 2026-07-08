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
