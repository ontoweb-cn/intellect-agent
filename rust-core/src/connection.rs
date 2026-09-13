//! RustConnection + RustCursor — minimal DB-API compatible PyO3 classes.
//!
//! RustConnection shares an `Arc<Mutex<Connection>>` with the SQLiteBackend,
//! so callbacks inside `execute_write` operate on the same transaction.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList, PyTuple};
use rusqlite::Connection;

fn _map_err(e: rusqlite::Error) -> PyErr {
    let msg = e.to_string();
    // Match Python sqlite3: SQL-level errors (no such table, constraint
    // violation, …) surface as sqlite3.OperationalError, which Python-side code
    // catches via `except sqlite3.OperationalError`.  A bare RuntimeError here
    // broke those handlers.
    Python::attach(|py| {
        if let Ok(sqlite3) = py.import("sqlite3") {
            if let Ok(exc_type) = sqlite3.getattr("OperationalError") {
                if let Ok(instance) = exc_type.call1((msg.clone(),)) {
                    return PyErr::from_value(instance);
                }
            }
        }
        pyo3::exceptions::PyRuntimeError::new_err(msg)
    })
}

// ── SQL value storage (GIL-free during query execution) ─────────────────────

#[derive(Clone, Debug)]
pub enum SqlValue {
    Null,
    Integer(i64),
    Real(f64),
    Text(String),
    Blob(Vec<u8>),
}

impl SqlValue {
    pub fn from_row(row: &rusqlite::Row, idx: usize) -> Result<Self, rusqlite::Error> {
        use rusqlite::types::ValueRef;
        match row.get_ref_unwrap(idx) {
            ValueRef::Null => Ok(SqlValue::Null),
            ValueRef::Integer(i) => Ok(SqlValue::Integer(i)),
            ValueRef::Real(f) => Ok(SqlValue::Real(f)),
            ValueRef::Text(t) => Ok(SqlValue::Text(
                std::str::from_utf8(t).unwrap_or_default().to_string(),
            )),
            ValueRef::Blob(b) => Ok(SqlValue::Blob(b.to_vec())),
        }
    }

    // `unwrap` is safe for all four arms: numbers and strings convert with
    // `Error = Infallible`.  These functions keep their `-> Py<PyAny>` (not
    // `PyResult`) signature from the 0.21 code, so `?` is not available here.
    fn to_py(&self, py: Python<'_>) -> Py<PyAny> {
        match self {
            SqlValue::Null => py.None(),
            SqlValue::Integer(i) => i.into_pyobject(py).unwrap().into_any().unbind(),
            SqlValue::Real(f) => f.into_pyobject(py).unwrap().into_any().unbind(),
            SqlValue::Text(s) => s.into_pyobject(py).unwrap().into_any().unbind(),
            SqlValue::Blob(b) => PyBytes::new(py, &b).into_any().unbind(),
        }
    }
}

// ── RustCursor ──────────────────────────────────────────────────────────────

#[pyclass]
pub struct RustCursor {
    columns: Vec<String>,
    data: Vec<Vec<SqlValue>>,
    // pyo3 0.23+ requires `#[pyclass]` types to be `Sync`, which rules out
    // `RefCell`/`Cell`.  A cursor is only ever driven from one thread in
    // practice — the atomic satisfies the bound, it is not here for concurrent
    // access.
    pos: AtomicUsize,
    rowcount: i64,
    lastrowid_val: Option<i64>,
}

#[pymethods]
impl RustCursor {
    fn fetchone(&self) -> PyResult<Option<RustRow>> {
        let pos = self.pos.fetch_add(1, Ordering::Relaxed);
        if pos >= self.data.len() {
            return Ok(None);
        }
        let row = self.data[pos].clone();
        Ok(Some(RustRow::new(self.columns.clone(), row)))
    }

    fn fetchall(&self) -> PyResult<Vec<RustRow>> {
        // `fetchone` uses `fetch_add`, so after reading past the end `pos` can
        // sit above `data.len()` — e.g. five `fetchone()` calls on a three-row
        // cursor leave it at 5.  The `min` is load-bearing: without it the
        // slice below panics instead of returning an empty list.
        let start = self.pos.swap(self.data.len(), Ordering::Relaxed);
        let remaining = &self.data[start.min(self.data.len())..];
        let out: Vec<RustRow> = remaining
            .iter()
            .map(|row| RustRow::new(self.columns.clone(), row.clone()))
            .collect();
        Ok(out)
    }

    #[getter]
    fn lastrowid(&self) -> Option<i64> {
        self.lastrowid_val
    }

    #[getter]
    fn rowcount(&self) -> i64 {
        self.rowcount
    }
}

// ── RustRow ─────────────────────────────────────────────────────────────────
// sqlite3.Row-compatible row: supports int and str indexing, keys(), iteration
// over values, and dict(row) via keys()+__getitem__.

// `skip_from_py_object`: rows only travel Rust → Python.
#[pyclass(skip_from_py_object)]
#[derive(Clone)]
pub struct RustRow {
    columns: Vec<String>,
    values: Vec<SqlValue>,
}

impl RustRow {
    fn new(columns: Vec<String>, values: Vec<SqlValue>) -> Self {
        RustRow { columns, values }
    }
}

#[pymethods]
impl RustRow {
    fn __getitem__(&self, py: Python<'_>, key: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        // Integer index (0-based; negative supported like sqlite3.Row).
        if let Ok(idx) = key.extract::<isize>() {
            let n = self.values.len() as isize;
            let i = if idx < 0 { n + idx } else { idx };
            if i >= 0 && (i as usize) < self.values.len() {
                return Ok(self.values[i as usize].to_py(py));
            }
            return Err(pyo3::exceptions::PyIndexError::new_err(
                "row index out of range",
            ));
        }
        // String index (column name).
        if let Ok(name) = key.extract::<String>() {
            if let Some(pos) = self.columns.iter().position(|c| c == &name) {
                return Ok(self.values[pos].to_py(py));
            }
            return Err(pyo3::exceptions::PyKeyError::new_err(name));
        }
        Err(pyo3::exceptions::PyTypeError::new_err(
            "row indices must be integers or strings",
        ))
    }

    fn keys(&self) -> Vec<String> {
        self.columns.clone()
    }

    fn __iter__(&self, py: Python<'_>) -> Vec<Py<PyAny>> {
        self.values.iter().map(|v| v.to_py(py)).collect()
    }

    fn __len__(&self) -> usize {
        self.values.len()
    }

    fn __repr__(&self) -> String {
        format!("<RustRow ({})>", self.columns.join(", "))
    }
}

// ── RustConnection ──────────────────────────────────────────────────────────

// `skip_from_py_object`: `RustConnection` is only ever handed to the Python
// write callback, never extracted back out.
#[pyclass(skip_from_py_object)]
#[derive(Clone)]
pub struct RustConnection {
    conn: Arc<Mutex<Connection>>,
}

impl RustConnection {
    pub fn from_arc(conn: Arc<Mutex<Connection>>) -> Self {
        RustConnection { conn }
    }

    pub fn conn_arc(&self) -> Arc<Mutex<Connection>> {
        Arc::clone(&self.conn)
    }
}

#[pymethods]
impl RustConnection {
    #[pyo3(signature = (sql, params=None))]
    fn execute(
        &self,
        _py: Python<'_>,
        sql: &str,
        params: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<RustCursor> {
        let conn = self.conn.lock().unwrap();

        let stmt = conn.prepare(sql).map_err(_map_err)?;
        let columns: Vec<String> = stmt.column_names().iter().map(|c| c.to_string()).collect();
        let mut stmt = stmt;

        // If the statement returns columns, it's a query (SELECT/PRAGMA).
        // Otherwise it's a mutation (INSERT/UPDATE/DELETE) — use execute()
        // and get rowcount from conn.changes().
        if columns.is_empty() {
            let affected = match params {
                None => {
                    stmt.execute([]).map_err(_map_err)?;
                    conn.changes() as i64
                }
                Some(p) => {
                    let param_vals = python_to_params(p)?;
                    stmt.execute(rusqlite::params_from_iter(param_vals.iter()))
                        .map_err(_map_err)?;
                    conn.changes() as i64
                }
            };
            let lastrowid_val = Some(conn.last_insert_rowid());
            Ok(RustCursor {
                columns,
                data: Vec::new(),
                pos: AtomicUsize::new(0),
                rowcount: affected,
                lastrowid_val,
            })
        } else {
            let data: Vec<Vec<SqlValue>> = match params {
                None => {
                    let rows_iter = stmt
                        .query_map([], |row| {
                            (0..columns.len())
                                .map(|i| SqlValue::from_row(row, i))
                                .collect::<Result<Vec<_>, _>>()
                        })
                        .map_err(_map_err)?;
                    collect_mapped_rows(rows_iter)
                }
                Some(p) => {
                    let param_vals = python_to_params(p)?;
                    let rows_iter = stmt
                        .query_map(rusqlite::params_from_iter(param_vals.iter()), |row| {
                            (0..columns.len())
                                .map(|i| SqlValue::from_row(row, i))
                                .collect::<Result<Vec<_>, _>>()
                        })
                        .map_err(_map_err)?;
                    collect_mapped_rows(rows_iter)
                }
            };

            let rowcount = data.len() as i64;
            let lastrowid_val = Some(conn.last_insert_rowid());

            Ok(RustCursor {
                columns,
                data,
                pos: AtomicUsize::new(0),
                rowcount,
                lastrowid_val,
            })
        }
    }

    fn executescript(&self, ddl: &str) -> PyResult<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute_batch(ddl).map_err(_map_err)
    }

    fn commit(&self) -> PyResult<()> {
        Ok(())
    }

    fn rollback(&self) -> PyResult<()> {
        Ok(())
    }

    /// Return a new empty cursor (sqlite3.Connection.cursor() equivalent).
    fn cursor(&self) -> PyResult<RustCursor> {
        Ok(RustCursor {
            columns: Vec::new(),
            data: Vec::new(),
            pos: AtomicUsize::new(0),
            rowcount: 0,
            lastrowid_val: None,
        })
    }
}

// ── Helpers ─────────────────────────────────────────────────────────────────

fn collect_mapped_rows<F>(iter: rusqlite::MappedRows<'_, F>) -> Vec<Vec<SqlValue>>
where
    F: FnMut(&rusqlite::Row<'_>) -> rusqlite::Result<Vec<SqlValue>>,
{
    let mut rows = Vec::new();
    for row in iter {
        if let Ok(r) = row {
            rows.push(r);
        }
    }
    rows
}

fn python_to_params(p: &Bound<'_, PyAny>) -> PyResult<Vec<rusqlite::types::Value>> {
    let mut out = Vec::new();
    if let Ok(tup) = p.cast::<PyTuple>() {
        for item in tup.iter() {
            out.push(py_to_sql(&item)?);
        }
    } else if let Ok(list) = p.cast::<PyList>() {
        for item in list.iter() {
            out.push(py_to_sql(&item)?);
        }
    } else {
        out.push(py_to_sql(p)?);
    }
    Ok(out)
}

fn py_to_sql(obj: &Bound<'_, PyAny>) -> PyResult<rusqlite::types::Value> {
    use rusqlite::types::Value;
    if obj.is_none() {
        return Ok(Value::Null);
    }
    if let Ok(i) = obj.extract::<i64>() {
        return Ok(Value::Integer(i));
    }
    if let Ok(f) = obj.extract::<f64>() {
        return Ok(Value::Real(f));
    }
    if let Ok(s) = obj.extract::<String>() {
        return Ok(Value::Text(s));
    }
    if let Ok(b) = obj.extract::<Vec<u8>>() {
        return Ok(Value::Blob(b));
    }
    let s: String = obj.str()?.to_string_lossy().into_owned();
    Ok(Value::Text(s))
}

// ── Shared helpers (used by backend.rs) ─────────────────────────────────────

/// Convert a SqlValue to a Python object.
// Same `unwrap`-rather-than-`?` situation as `SqlValue::to_py` above.
pub fn val_to_py(val: &SqlValue, py: Python<'_>) -> Py<PyAny> {
    match val {
        SqlValue::Null => py.None(),
        SqlValue::Integer(i) => i.into_pyobject(py).unwrap().into_any().unbind(),
        SqlValue::Real(f) => f.into_pyobject(py).unwrap().into_any().unbind(),
        SqlValue::Text(s) => s.into_pyobject(py).unwrap().into_any().unbind(),
        SqlValue::Blob(b) => PyBytes::new(py, b).into_any().unbind(),
    }
}

/// Convert a Python object to a rusqlite Value.
pub fn py_to_rusqlite_value(obj: &Py<PyAny>, py: Python<'_>) -> rusqlite::types::Value {
    let obj = obj.bind(py);
    py_to_sql(obj).unwrap_or(rusqlite::types::Value::Null)
}

#[cfg(test)]
mod tests {
    use super::*;

    // Cursor position tracking moved from `RefCell<usize>` to `AtomicUsize`
    // for the pyo3 0.23+ `Sync` requirement, which also changed `fetchone`
    // from "stop at the end" to `fetch_add` semantics.  These tests pin the
    // resulting behaviour down; none of them touch the interpreter.
    fn cursor_with(rows: usize) -> RustCursor {
        let data: Vec<Vec<SqlValue>> = (0..rows)
            .map(|i| vec![SqlValue::Integer(i as i64)])
            .collect();
        RustCursor {
            columns: vec!["n".to_string()],
            data,
            pos: AtomicUsize::new(0),
            rowcount: rows as i64,
            lastrowid_val: None,
        }
    }

    fn int_at(row: &RustRow, idx: usize) -> i64 {
        match &row.values[idx] {
            SqlValue::Integer(i) => *i,
            other => panic!("expected Integer, got {:?}", other),
        }
    }

    #[test]
    fn fetchone_walks_rows_then_returns_none() {
        let cur = cursor_with(3);
        for expected in 0..3 {
            let row = cur.fetchone().unwrap().expect("row should exist");
            assert_eq!(int_at(&row, 0), expected);
        }
        assert!(cur.fetchone().unwrap().is_none());
    }

    #[test]
    fn fetchall_returns_rows_remaining_after_fetchone() {
        let cur = cursor_with(3);
        let _ = cur.fetchone().unwrap().unwrap();
        let rest = cur.fetchall().unwrap();
        assert_eq!(rest.len(), 2);
        assert_eq!(int_at(&rest[0], 0), 1);
        // A second fetchall is empty — the cursor is exhausted.
        assert!(cur.fetchall().unwrap().is_empty());
    }

    #[test]
    fn fetchall_after_reading_past_end_is_empty_not_panic() {
        // Four `fetchone` calls on a three-row cursor leave `pos` at 4, past
        // `data.len()`.  Without the `min` in `fetchall` the slice
        // `&data[pos..]` would panic — this is the regression guard.
        let cur = cursor_with(3);
        for _ in 0..4 {
            let _ = cur.fetchone().unwrap();
        }
        assert!(cur.fetchall().unwrap().is_empty());
    }

    #[test]
    fn fetchall_on_empty_cursor_is_empty() {
        let cur = cursor_with(0);
        assert!(cur.fetchall().unwrap().is_empty());
    }
}
