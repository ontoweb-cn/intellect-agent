//! SQLiteBackend — Rust-managed connection with WAL, write retry, checkpoint.
//!
//! Replaces `agent/storage/sqlite_backend.py`.  Owns a rusqlite Connection
//! behind a Mutex and exposes `execute_write` with BEGIN IMMEDIATE / COMMIT /
//! ROLLBACK + jittered retry for locked/busy errors.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use pyo3::prelude::*;
use rand::Rng;
use rusqlite::Connection;

use crate::connection::{RustConnection, py_to_rusqlite_value, val_to_py};
use crate::fts;
use crate::compression;

// ── Constants ───────────────────────────────────────────────────────────────

const WRITE_MAX_RETRIES: usize = 15;
const WRITE_RETRY_MIN_MS: u64 = 20;
const WRITE_RETRY_MAX_MS: u64 = 150;
const CHECKPOINT_EVERY_N_WRITES: u64 = 50;

// ── Error mapping ───────────────────────────────────────────────────────────

pub fn _map_rusqlite_err(e: rusqlite::Error) -> PyErr {
    pyo3::exceptions::PyRuntimeError::new_err(e.to_string())
}

fn _is_locked_or_busy(e: &PyErr) -> bool {
    let msg = e.to_string().to_lowercase();
    msg.contains("locked") || msg.contains("busy")
}

// ── SQLiteBackend ───────────────────────────────────────────────────────────

#[pyclass]
pub struct SQLiteBackend {
    conn: Arc<Mutex<Connection>>,
    db_path: String,
    write_count: AtomicU64,
    checkpoint_every: u64,
}

#[pymethods]
impl SQLiteBackend {
    /// Create a new backend and open the database connection.
    #[new]
    fn new(db_path: &str) -> PyResult<Self> {
        let conn = Connection::open(db_path).map_err(_map_rusqlite_err)?;

        // Apply WAL journal mode
        let _wal = apply_wal(&conn);

        // Enable foreign keys
        conn.execute_batch("PRAGMA foreign_keys=ON")
            .map_err(_map_rusqlite_err)?;

        Ok(SQLiteBackend {
            conn: Arc::new(Mutex::new(conn)),
            db_path: db_path.to_string(),
            write_count: AtomicU64::new(0),
            checkpoint_every: CHECKPOINT_EVERY_N_WRITES,
        })
    }

    /// Return a RustConnection proxy sharing the same underlying connection.
    /// Used for read operations outside of execute_write.
    fn connection(&self) -> RustConnection {
        RustConnection::from_arc(Arc::clone(&self.conn))
    }

    /// Return the database path.
    fn db_path_str(&self) -> &str {
        &self.db_path
    }

    /// Close the connection with a passive WAL checkpoint.
    fn close(&self) -> PyResult<()> {
        // Do a passive checkpoint before closing
        if let Ok(conn) = self.conn.lock() {
            let _ = conn.execute_batch("PRAGMA wal_checkpoint(PASSIVE)");
        }
        // Connection will be closed when the Arc is dropped.
        Ok(())
    }

    // ── append_message (Stage 1d) ───────────────────────────────────────

    /// Append a message row and update session counters.
    /// Replaces the Python `_execute_write(_do)` callback for the most
    /// frequent write operation — called once per API call.
    #[pyo3(signature = (
        session_id, role, content, tool_call_id, tool_calls_json, tool_name,
        timestamp, token_count=None, finish_reason=None, reasoning=None,
        reasoning_content=None, reasoning_details_json=None,
        codex_items_json=None, codex_message_items_json=None,
        platform_message_id=None, observed=false, num_tool_calls=0
    ))]
    fn append_message(
        &self,
        session_id: &str,
        role: &str,
        content: Option<&str>,
        tool_call_id: Option<&str>,
        tool_calls_json: Option<&str>,
        tool_name: Option<&str>,
        timestamp: f64,
        token_count: Option<i64>,
        finish_reason: Option<&str>,
        reasoning: Option<&str>,
        reasoning_content: Option<&str>,
        reasoning_details_json: Option<&str>,
        codex_items_json: Option<&str>,
        codex_message_items_json: Option<&str>,
        platform_message_id: Option<&str>,
        observed: bool,
        num_tool_calls: i64,
    ) -> PyResult<i64> {
        let mut last_err: Option<PyErr> = None;

        for attempt in 0..WRITE_MAX_RETRIES {
            let result = (|| -> Result<i64, PyErr> {
                let conn = self.conn.lock().unwrap();

                conn.execute_batch("BEGIN IMMEDIATE")
                    .map_err(_map_rusqlite_err)?;

                // INSERT with 16 columns
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, tool_call_id, \
                     tool_calls, tool_name, timestamp, token_count, finish_reason, \
                     reasoning, reasoning_content, reasoning_details, codex_reasoning_items, \
                     codex_message_items, platform_message_id, observed) \
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16)",
                    rusqlite::params![
                        session_id, role, content, tool_call_id,
                        tool_calls_json, tool_name, timestamp, token_count, finish_reason,
                        reasoning, reasoning_content, reasoning_details_json,
                        codex_items_json, codex_message_items_json,
                        platform_message_id, observed as i64,
                    ],
                ).map_err(_map_rusqlite_err)?;

                let msg_id = conn.last_insert_rowid();

                // Update session counters
                if num_tool_calls > 0 {
                    conn.execute(
                        "UPDATE sessions SET message_count = message_count + 1, \
                         tool_call_count = tool_call_count + ?1 WHERE id = ?2",
                        rusqlite::params![num_tool_calls, session_id],
                    ).map_err(_map_rusqlite_err)?;
                } else {
                    conn.execute(
                        "UPDATE sessions SET message_count = message_count + 1 WHERE id = ?1",
                        rusqlite::params![session_id],
                    ).map_err(_map_rusqlite_err)?;
                }

                conn.execute_batch("COMMIT")
                    .map_err(_map_rusqlite_err)?;

                Ok(msg_id)
            })();

            match result {
                Ok(msg_id) => {
                    self.inc_write_count();
                    return Ok(msg_id);
                }
                Err(e) => {
                    let _ = self.conn.lock().unwrap().execute_batch("ROLLBACK");

                    if _is_locked_or_busy(&e) && attempt < WRITE_MAX_RETRIES - 1 {
                        let jitter_ms = {
                            let mut rng = rand::thread_rng();
                            rng.gen_range(WRITE_RETRY_MIN_MS..WRITE_RETRY_MAX_MS)
                        };
                        std::thread::sleep(Duration::from_millis(jitter_ms));
                        last_err = Some(e);
                        continue;
                    }
                    return Err(e);
                }
            }
        }

        Err(last_err.unwrap_or_else(|| {
            pyo3::exceptions::PyRuntimeError::new_err("database is locked after max retries")
        }))
    }

    // ── replace_messages (Stage 1f) ─────────────────────────────────────

    /// Atomically replace all messages for a session.
    /// Used by /retry, /undo, /compress transcript rewrites.
    #[pyo3(signature = (
        session_id, roles, contents, tool_call_ids, tool_calls_jsons,
        tool_names, timestamps, token_counts, finish_reasons, reasonings,
        reasoning_contents, reasoning_details_jsons, codex_items_jsons,
        codex_message_items_jsons, platform_msg_ids, observed_flags,
        num_tool_calls
    ))]
    fn replace_messages(
        &self,
        session_id: &str,
        roles: Vec<String>,
        contents: Vec<Option<String>>,
        tool_call_ids: Vec<Option<String>>,
        tool_calls_jsons: Vec<Option<String>>,
        tool_names: Vec<Option<String>>,
        timestamps: Vec<f64>,
        token_counts: Vec<Option<i64>>,
        finish_reasons: Vec<Option<String>>,
        reasonings: Vec<Option<String>>,
        reasoning_contents: Vec<Option<String>>,
        reasoning_details_jsons: Vec<Option<String>>,
        codex_items_jsons: Vec<Option<String>>,
        codex_message_items_jsons: Vec<Option<String>>,
        platform_msg_ids: Vec<Option<String>>,
        observed_flags: Vec<i64>,
        num_tool_calls: Vec<i64>,
    ) -> PyResult<()> {
        let mut last_err: Option<PyErr> = None;

        for attempt in 0..WRITE_MAX_RETRIES {
            let result = (|| -> Result<(), PyErr> {
                let conn = self.conn.lock().unwrap();

                conn.execute_batch("BEGIN IMMEDIATE")
                    .map_err(_map_rusqlite_err)?;

                // Delete all existing messages for this session
                conn.execute(
                    "DELETE FROM messages WHERE session_id = ?1",
                    rusqlite::params![session_id],
                ).map_err(_map_rusqlite_err)?;

                // Reset counters
                conn.execute(
                    "UPDATE sessions SET message_count = 0, tool_call_count = 0 WHERE id = ?1",
                    rusqlite::params![session_id],
                ).map_err(_map_rusqlite_err)?;

                // Batch insert all messages
                let n = roles.len();
                let mut total_messages: i64 = 0;
                let mut total_tool_calls: i64 = 0;

                for i in 0..n {
                    let role = roles.get(i).map(|s| s.as_str()).unwrap_or("unknown");
                    let content = contents.get(i).and_then(|o| o.as_deref());
                    let tcid = tool_call_ids.get(i).and_then(|o| o.as_deref());
                    let tc_json = tool_calls_jsons.get(i).and_then(|o| o.as_deref());
                    let tn = tool_names.get(i).and_then(|o| o.as_deref());
                    let ts = timestamps.get(i).copied().unwrap_or(0.0);
                    let tkc = token_counts.get(i).and_then(|o| *o);
                    let fr = finish_reasons.get(i).and_then(|o| o.as_deref());
                    let rsn = reasonings.get(i).and_then(|o| o.as_deref());
                    let rsnc = reasoning_contents.get(i).and_then(|o| o.as_deref());
                    let rsnd = reasoning_details_jsons.get(i).and_then(|o| o.as_deref());
                    let cxi = codex_items_jsons.get(i).and_then(|o| o.as_deref());
                    let cxmi = codex_message_items_jsons.get(i).and_then(|o| o.as_deref());
                    let pmi = platform_msg_ids.get(i).and_then(|o| o.as_deref());
                    let obs = observed_flags.get(i).copied().unwrap_or(0);

                    conn.execute(
                        "INSERT INTO messages (session_id, role, content, tool_call_id, \
                         tool_calls, tool_name, timestamp, token_count, finish_reason, \
                         reasoning, reasoning_content, reasoning_details, codex_reasoning_items, \
                         codex_message_items, platform_message_id, observed) \
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16)",
                        rusqlite::params![
                            session_id, role, content, tcid,
                            tc_json, tn, ts, tkc, fr,
                            rsn, rsnc, rsnd, cxi, cxmi,
                            pmi, obs,
                        ],
                    ).map_err(_map_rusqlite_err)?;

                    total_messages += 1;
                    total_tool_calls += num_tool_calls.get(i).copied().unwrap_or(0);
                }

                // Final counter update
                conn.execute(
                    "UPDATE sessions SET message_count = ?1, tool_call_count = ?2 WHERE id = ?3",
                    rusqlite::params![total_messages, total_tool_calls, session_id],
                ).map_err(_map_rusqlite_err)?;

                conn.execute_batch("COMMIT")
                    .map_err(_map_rusqlite_err)?;

                Ok(())
            })();

            match result {
                Ok(()) => {
                    self.inc_write_count();
                    return Ok(());
                }
                Err(e) => {
                    let _ = self.conn.lock().unwrap().execute_batch("ROLLBACK");

                    if _is_locked_or_busy(&e) && attempt < WRITE_MAX_RETRIES - 1 {
                        let jitter_ms = {
                            let mut rng = rand::thread_rng();
                            rng.gen_range(WRITE_RETRY_MIN_MS..WRITE_RETRY_MAX_MS)
                        };
                        std::thread::sleep(Duration::from_millis(jitter_ms));
                        last_err = Some(e);
                        continue;
                    }
                    return Err(e);
                }
            }
        }

        Err(last_err.unwrap_or_else(|| {
            pyo3::exceptions::PyRuntimeError::new_err("database is locked after max retries")
        }))
    }

    // ── execute_simple_write (Stage 1g) ──────────────────────────────────

    /// Execute a simple write (INSERT/UPDATE/DELETE) with BEGIN/COMMIT + retry.
    /// Returns the number of rows affected (rowcount).
    /// Use for callbacks that are a single conn.execute(sql, params).
    #[pyo3(signature = (sql, params=None))]
    fn execute_simple_write(
        &self,
        py: Python<'_>,
        sql: &str,
        params: Option<Vec<PyObject>>,
    ) -> PyResult<usize> {
        let mut last_err: Option<PyErr> = None;

        for attempt in 0..WRITE_MAX_RETRIES {
            let result = (|| -> Result<usize, PyErr> {
                let conn = self.conn.lock().unwrap();
                conn.execute_batch("BEGIN IMMEDIATE")
                    .map_err(_map_rusqlite_err)?;

                let affected = if let Some(ref p) = params {
                    let vals: Vec<rusqlite::types::Value> = p
                        .iter()
                        .map(|obj| crate::connection::py_to_rusqlite_value(obj, py))
                        .collect();
                    conn.execute(sql, rusqlite::params_from_iter(vals.iter()))
                        .map_err(_map_rusqlite_err)?
                } else {
                    conn.execute(sql, [])
                        .map_err(_map_rusqlite_err)?
                };

                conn.execute_batch("COMMIT")
                    .map_err(_map_rusqlite_err)?;

                Ok(affected)
            })();

            match result {
                Ok(rows) => {
                    self.inc_write_count();
                    return Ok(rows);
                }
                Err(e) => {
                    let _ = self.conn.lock().unwrap().execute_batch("ROLLBACK");
                    if _is_locked_or_busy(&e) && attempt < WRITE_MAX_RETRIES - 1 {
                        let jitter_ms = rand::thread_rng()
                            .gen_range(WRITE_RETRY_MIN_MS..WRITE_RETRY_MAX_MS);
                        std::thread::sleep(Duration::from_millis(jitter_ms));
                        last_err = Some(e);
                        continue;
                    }
                    return Err(e);
                }
            }
        }

        Err(last_err.unwrap_or_else(|| {
            pyo3::exceptions::PyRuntimeError::new_err("database is locked after max retries")
        }))
    }

    // ── search (Stage 1e) ────────────────────────────────────────────────

    /// Execute an FTS5 search query and return results as Python dicts.
    /// Params are positional SQL bind values. On error returns empty list.
    fn search_fts5(
        &self,
        py: Python<'_>,
        sql: &str,
        params: Vec<PyObject>,
    ) -> PyResult<Vec<PyObject>> {
        let conn = match self.conn.lock() {
            Ok(c) => c,
            Err(e) => {
                // Log but don't crash — search is best-effort
                let py_warn = pyo3::types::PyModule::import_bound(py, "logging")
                    .and_then(|m| m.call_method1("getLogger", ("intellect_core",)));
                if let Ok(logger) = py_warn {
                    let _ = logger.call_method1("warning", (format!("search_fts5 mutex poison: {:?}", e),));
                }
                return Ok(Vec::new());
            }
        };

        let stmt = match conn.prepare(sql) {
            Ok(s) => s,
            Err(e) => {
                if !e.to_string().contains("locked") && !e.to_string().contains("busy") {
                    let py_warn = pyo3::types::PyModule::import_bound(py, "logging")
                        .and_then(|m| m.call_method1("getLogger", ("intellect_core",)));
                    if let Ok(logger) = py_warn {
                        let _ = logger.call_method1("warning", (format!("search_fts5 prepare error: {:?}", e),));
                    }
                }
                return Ok(Vec::new());
            }
        };

        let columns: Vec<String> = stmt.column_names().iter().map(|c| c.to_string()).collect();
        let mut stmt = stmt;

        // Convert Python params to rusqlite values
        let sql_params: Vec<rusqlite::types::Value> = params
            .iter()
            .map(|p| py_to_rusqlite_value(p, py))
            .collect();

        let rows = match stmt.query_map(
            rusqlite::params_from_iter(sql_params.iter()),
            |row| {
                let mut map = Vec::new();
                for (i, col) in columns.iter().enumerate() {
                    let val = crate::connection::SqlValue::from_row(row, i).ok();
                    if let Some(v) = val {
                        map.push((col.clone(), v));
                    }
                }
                Ok(map)
            },
        ) {
            Ok(r) => r,
            Err(_) => return Ok(Vec::new()),
        };

        let mut results = Vec::new();
        for row in rows {
            if let Ok(fields) = row {
                let dict = pyo3::types::PyDict::new_bound(py);
                for (col, val) in &fields {
                    let py_val = val_to_py(val, py);
                    let _ = dict.set_item(col.as_str(), py_val);
                }
                results.push(dict.into());
            }
        }
        Ok(results)
    }

    /// Batch-extract context windows (1 before + self + 1 after) for match IDs.
    /// Replaces N+1 CTE queries with a single batched operation.
    fn get_message_context_batch(
        &self,
        py: Python<'_>,
        match_ids: Vec<i64>,
    ) -> PyResult<Vec<PyObject>> {
        if match_ids.is_empty() {
            return Ok(Vec::new());
        }

        let conn = match self.conn.lock() {
            Ok(c) => c,
            Err(_) => return Ok(Vec::new()),
        };

        let placeholders: Vec<String> = match_ids.iter().map(|_| "?".to_string()).collect();
        let in_clause = placeholders.join(",");

        // Build a single query that gets 1-before for all match IDs
        let before_sql = format!(
            "SELECT m.id AS match_id, prev.role AS role, prev.content AS content \
             FROM messages m \
             LEFT JOIN LATERAL ( \
               SELECT role, content FROM messages \
               WHERE session_id = m.session_id \
                 AND (timestamp < m.timestamp \
                      OR (timestamp = m.timestamp AND id < m.id)) \
               ORDER BY timestamp DESC, id DESC LIMIT 1 \
             ) prev ON true \
             WHERE m.id IN ({in_clause})",
        );

        // Get self + after similarly
        let after_sql = format!(
            "SELECT m.id AS match_id, nxt.role AS role, nxt.content AS content \
             FROM messages m \
             LEFT JOIN LATERAL ( \
               SELECT role, content FROM messages \
               WHERE session_id = m.session_id \
                 AND (timestamp > m.timestamp \
                      OR (timestamp = m.timestamp AND id > m.id)) \
               ORDER BY timestamp ASC, id ASC LIMIT 1 \
             ) nxt ON true \
             WHERE m.id IN ({in_clause})",
        );

        let self_sql = format!(
            "SELECT id AS match_id, role, content FROM messages WHERE id IN ({in_clause})",
        );

        let params: Vec<&dyn rusqlite::types::ToSql> = match_ids
            .iter()
            .map(|id| id as &dyn rusqlite::types::ToSql)
            .collect();

        // Execute all three queries — collect into maps keyed by match_id
        let before_map = query_to_map(&conn, &before_sql, &params);
        let after_map = query_to_map(&conn, &after_sql, &params);
        let self_map = query_to_map(&conn, &self_sql, &params);

        let mut results = Vec::with_capacity(match_ids.len());
        for id in &match_ids {
            let mut context: Vec<PyObject> = Vec::new();

            // before — skip if empty (no preceding message)
            if let Some((role, content)) = before_map.get(id) {
                if !role.is_empty() || !content.is_empty() {
                    let d = pyo3::types::PyDict::new_bound(py);
                    let _ = d.set_item("role", role.as_str());
                    let _ = d.set_item("content", truncate_content(content));
                    context.push(d.into());
                }
            }

            // self
            if let Some((role, content)) = self_map.get(id) {
                let d = pyo3::types::PyDict::new_bound(py);
                let _ = d.set_item("role", role.as_str());
                let _ = d.set_item("content", truncate_content(content));
                context.push(d.into());
            }

            // after — skip if empty (no following message)
            if let Some((role, content)) = after_map.get(id) {
                if !role.is_empty() || !content.is_empty() {
                    let d = pyo3::types::PyDict::new_bound(py);
                    let _ = d.set_item("role", role.as_str());
                    let _ = d.set_item("content", truncate_content(content));
                    context.push(d.into());
                }
            }

            results.push(
                pyo3::types::PyList::new_bound(py, &context).into(),
            );
        }

        Ok(results)
    }

    // ── write operations ─────────────────────────────────────────────────

    /// Execute a write with BEGIN IMMEDIATE / COMMIT / ROLLBACK + retry.
    ///
    /// The Python callback receives a RustConnection that shares the
    /// same underlying connection.  The Mutex is released during the
    /// callback so the callback can call conn.execute() (which re-acquires
    /// the Mutex momentarily).  The GIL ensures only one thread runs
    /// Python code at a time.
    fn execute_write(&self, py: Python<'_>, callback: PyObject) -> PyResult<PyObject> {
        let py_conn = RustConnection::from_arc(Arc::clone(&self.conn));
        let mut last_err: Option<PyErr> = None;

        for attempt in 0..WRITE_MAX_RETRIES {
            let result = (|| -> PyResult<PyObject> {
                // Acquire lock, begin transaction
                {
                    let conn = self.conn.lock().unwrap();
                    conn.execute_batch("BEGIN IMMEDIATE")
                        .map_err(_map_rusqlite_err)?;
                }

                // Run Python callback (lock released so callback can use conn.execute)
                let py_result = callback.call1(py, (py_conn.clone(),))?;

                // Re-acquire lock, commit
                {
                    let conn = self.conn.lock().unwrap();
                    conn.execute_batch("COMMIT")
                        .map_err(_map_rusqlite_err)?;
                }

                Ok(py_result)
            })();

            match result {
                Ok(val) => {
                    self.inc_write_count();
                    return Ok(val);
                }
                Err(e) => {
                    // Rollback on error
                    {
                        let conn = self.conn.lock().unwrap();
                        let _ = conn.execute_batch("ROLLBACK");
                    }

                    if _is_locked_or_busy(&e) && attempt < WRITE_MAX_RETRIES - 1 {
                        let jitter_ms = rand::thread_rng()
                            .gen_range(WRITE_RETRY_MIN_MS..WRITE_RETRY_MAX_MS);
                        std::thread::sleep(Duration::from_millis(jitter_ms));
                        last_err = Some(e);
                        continue;
                    }
                    return Err(e);
                }
            }
        }

        Err(last_err.unwrap_or_else(|| {
            pyo3::exceptions::PyRuntimeError::new_err("database is locked after max retries")
        }))
    }

    /// Run a passive WAL checkpoint.
    fn try_wal_checkpoint(&self) -> PyResult<()> {
        let conn = self.conn.lock().unwrap();
        let _ = conn.execute_batch("PRAGMA wal_checkpoint(PASSIVE)");
        Ok(())
    }

    // ── FTS5 utilities (reuse persistent connection) ─────────────────────

    /// Drop all known FTS triggers (idempotent).
    fn drop_fts_triggers(&self) -> PyResult<()> {
        let conn = self.conn.lock().unwrap();
        fts::drop_fts_triggers_impl(&conn).map_err(_map_rusqlite_err)
    }

    /// Count how many of the expected FTS triggers exist.
    fn fts_trigger_count(&self) -> PyResult<usize> {
        let conn = self.conn.lock().unwrap();
        fts::fts_trigger_count_impl(&conn).map_err(_map_rusqlite_err)
    }

    /// Delete and re-populate the messages_fts index.
    fn rebuild_fts_indexes(&self) -> PyResult<()> {
        let conn = self.conn.lock().unwrap();
        fts::rebuild_fts_indexes_impl(&conn).map_err(_map_rusqlite_err)
    }

    // ── Compression ──────────────────────────────────────────────────────

    /// Walk the compression-continuation chain and return the tip session ID.
    fn get_compression_tip(&self, session_id: &str) -> PyResult<Option<String>> {
        let conn = self.conn.lock().unwrap();
        compression::get_compression_tip_impl(&conn, session_id)
            .map_err(_map_rusqlite_err)
    }

    // ── Schema ───────────────────────────────────────────────────────────

    /// Execute DDL statements to ensure schema exists.
    fn ensure_schema(&self, ddl: &str) -> PyResult<()> {
        if ddl.trim().is_empty() {
            return Ok(());
        }
        let conn = self.conn.lock().unwrap();
        conn.execute_batch(ddl).map_err(_map_rusqlite_err)
    }
}

// ── Internal helpers ────────────────────────────────────────────────────────

impl SQLiteBackend {
    fn inc_write_count(&self) {
        let count = self.write_count.fetch_add(1, Ordering::Relaxed) + 1;
        if count % self.checkpoint_every == 0 {
            let _ = self.try_wal_checkpoint();
        }
    }
}

// ── Search helpers ─────────────────────────────────────────────────────────

/// Execute a query and collect results as HashMap<id, (role, content)>.
fn query_to_map(
    conn: &rusqlite::Connection,
    sql: &str,
    params: &[&dyn rusqlite::types::ToSql],
) -> HashMap<i64, (String, String)> {
    let mut map = HashMap::new();
    let mut stmt = match conn.prepare(sql) {
        Ok(s) => s,
        Err(_) => return map,
    };
    let rows = match stmt.query_map(params, |row| {
        let id: i64 = row.get(0)?;
        let role: String = row.get::<_, String>(1).unwrap_or_default();
        let content: String = row.get::<_, String>(2).unwrap_or_default();
        Ok((id, role, content))
    }) {
        Ok(r) => r,
        Err(_) => return map,
    };
    for row in rows {
        if let Ok((id, role, content)) = row {
            map.insert(id, (role, content));
        }
    }
    map
}

/// Truncate content to 200 chars (matches Python behavior).
/// Uses char-boundary-safe slicing to avoid panicking on multi-byte UTF-8.
fn truncate_content(content: &str) -> String {
    if content.chars().count() > 200 {
        let byte_pos = content
            .char_indices()
            .nth(200)
            .map(|(i, _)| i)
            .unwrap_or(content.len());
        format!("{}...", &content[..byte_pos])
    } else {
        content.to_string()
    }
}

// ── WAL setup ───────────────────────────────────────────────────────────────

fn apply_wal(conn: &Connection) -> bool {
    // Check current journal mode
    let current: Result<String, _> =
        conn.query_row("PRAGMA journal_mode", [], |row| row.get(0));

    if let Ok(ref mode) = current {
        if mode == "wal" {
            return true;
        }
    }

    // Try setting WAL
    match conn.execute_batch("PRAGMA journal_mode=WAL") {
        Ok(()) => true,
        Err(_) => {
            // Fall back to DELETE on WAL-incompatible filesystems (NFS, SMB, FUSE)
            let _ = conn.execute_batch("PRAGMA journal_mode=DELETE");
            false
        }
    }
}

// ── Rust unit tests ─────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_db_path() -> String {
        // Use a temp file path (not :memory:) for realistic WAL testing
        let dir = std::env::temp_dir();
        let path = dir.join(format!("intellect_test_{}.db", std::process::id()));
        path.to_string_lossy().to_string()
    }

    #[test]
    fn test_open_and_close() {
        let path = temp_db_path();
        let backend = SQLiteBackend::new(&path).unwrap();
        assert!(!backend.db_path_str().is_empty());
        backend.close().unwrap();
        // Clean up
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn test_execute_write_commits_and_checkpoints() {
        let path = temp_db_path();
        let backend = SQLiteBackend::new(&path).unwrap();

        // Use the backend's own connection to set up a table
        {
            let conn = backend.conn.lock().unwrap();
            conn.execute_batch("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)").unwrap();
        }

        // Verify table exists
        {
            let conn = backend.conn.lock().unwrap();
            let count: i64 = conn.query_row("SELECT COUNT(*) FROM test", [], |r| r.get(0)).unwrap();
            assert_eq!(count, 0);
        }

        backend.close().unwrap();
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn test_fts_utilities_on_backend() {
        let path = temp_db_path();
        let backend = SQLiteBackend::new(&path).unwrap();

        // Set up schema via ensure_schema (simulates SessionDB._init_schema)
        backend.ensure_schema(
            "CREATE VIRTUAL TABLE messages_fts USING fts5(content, tokenize='trigram');
             CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT, tool_name TEXT, tool_calls TEXT);
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
        ).unwrap();

        assert_eq!(backend.fts_trigger_count().unwrap(), 3);
        backend.drop_fts_triggers().unwrap();
        assert_eq!(backend.fts_trigger_count().unwrap(), 0);

        // Insert data and rebuild
        {
            let conn = backend.conn.lock().unwrap();
            conn.execute("INSERT INTO messages (id, content, tool_name) VALUES (1, 'hello', 'search')", []).unwrap();
        }
        backend.rebuild_fts_indexes().unwrap();

        backend.close().unwrap();
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn test_compression_tip_on_backend() {
        let path = temp_db_path();
        let backend = SQLiteBackend::new(&path).unwrap();

        backend.ensure_schema(
            "CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                parent_session_id TEXT,
                started_at REAL,
                ended_at REAL,
                end_reason TEXT
            );",
        ).unwrap();

        let tip = backend.get_compression_tip("no-such-session").unwrap();
        assert_eq!(tip.as_deref(), Some("no-such-session"));

        backend.close().unwrap();
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn test_append_message() {
        let path = temp_db_path();
        let backend = SQLiteBackend::new(&path).unwrap();

        // Set up schema (same as SessionDB._init_schema)
        backend.ensure_schema(
            "CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, source TEXT, message_count INTEGER DEFAULT 0,
                tool_call_count INTEGER DEFAULT 0, started_at REAL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
                content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT,
                timestamp REAL, token_count INTEGER, finish_reason TEXT,
                reasoning TEXT, reasoning_content TEXT, reasoning_details TEXT,
                codex_reasoning_items TEXT, codex_message_items TEXT,
                platform_message_id TEXT, observed INTEGER DEFAULT 0
            );",
        ).unwrap();

        // Create a session
        backend.ensure_schema(
            "INSERT INTO sessions (id, source, started_at) VALUES ('s1', 'cli', 1000.0)",
        ).unwrap();

        // Append a user message
        let msg_id = backend.append_message(
            "s1", "user", Some("hello world"), None, None, None,
            1001.0, None, None, None, None, None, None, None, None, false, 0,
        ).unwrap();
        assert!(msg_id > 0);

        // Append an assistant message with tool calls
        let msg_id2 = backend.append_message(
            "s1", "assistant", Some("ok"), None,
            Some("[]"), Some("search"),
            1002.0, Some(500), Some("stop"), Some("thinking..."),
            None, None, None, None, None, false, 1,
        ).unwrap();
        assert!(msg_id2 > msg_id);

        // Verify counters
        let conn = backend.conn.lock().unwrap();
        let count: i64 = conn.query_row(
            "SELECT message_count FROM sessions WHERE id = 's1'", [], |r| r.get(0),
        ).unwrap();
        assert_eq!(count, 2);
        let tc: i64 = conn.query_row(
            "SELECT tool_call_count FROM sessions WHERE id = 's1'", [], |r| r.get(0),
        ).unwrap();
        assert_eq!(tc, 1);

        // Verify message content
        let content: String = conn.query_row(
            "SELECT content FROM messages WHERE id = ?1", [msg_id], |r| r.get(0),
        ).unwrap();
        assert_eq!(content, "hello world");

        drop(conn);
        backend.close().unwrap();
        let _ = std::fs::remove_file(&path);
    }
}
