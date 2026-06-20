//! RustConnection + RustCursor — minimal DB-API compatible PyO3 classes.
//!
//! RustConnection shares an `Arc<Mutex<Connection>>` with the SQLiteBackend,
//! so callbacks inside `execute_write` operate on the same transaction.

use std::cell::RefCell;
use std::sync::{Arc, Mutex};

use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyTuple};
use rusqlite::Connection;

fn _map_err(e: rusqlite::Error) -> PyErr {
    pyo3::exceptions::PyRuntimeError::new_err(e.to_string())
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

    fn to_py(&self, py: Python<'_>) -> PyObject {
        match self {
            SqlValue::Null => py.None(),
            SqlValue::Integer(i) => i.to_object(py),
            SqlValue::Real(f) => f.to_object(py),
            SqlValue::Text(s) => s.to_object(py),
            SqlValue::Blob(b) => PyBytes::new_bound(py, &b).into(),
        }
    }
}

// ── RustCursor ──────────────────────────────────────────────────────────────

#[pyclass]
pub struct RustCursor {
    columns: Vec<String>,
    data: Vec<Vec<SqlValue>>,
    pos: RefCell<usize>,
    rowcount: i64,
    lastrowid_val: Option<i64>,
}

#[pymethods]
impl RustCursor {
    fn fetchone(&self, py: Python<'_>) -> PyResult<Option<PyObject>> {
        let mut pos = self.pos.borrow_mut();
        if *pos >= self.data.len() {
            return Ok(None);
        }
        let row = &self.data[*pos];
        *pos += 1;
        let dict = PyDict::new_bound(py);
        for (col, val) in self.columns.iter().zip(row.iter()) {
            dict.set_item(col.as_str(), val.to_py(py))?;
        }
        Ok(Some(dict.into()))
    }

    fn fetchall(&self, py: Python<'_>) -> PyResult<Vec<PyObject>> {
        let mut pos = self.pos.borrow_mut();
        let remaining = &self.data[*pos..];
        *pos = self.data.len();
        let mut out = Vec::with_capacity(remaining.len());
        for row in remaining {
            let dict = PyDict::new_bound(py);
            for (col, val) in self.columns.iter().zip(row.iter()) {
                dict.set_item(col.as_str(), val.to_py(py))?;
            }
            out.push(dict.into());
        }
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

// ── RustConnection ──────────────────────────────────────────────────────────

#[pyclass]
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
                pos: RefCell::new(0),
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
                pos: RefCell::new(0),
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
            pos: RefCell::new(0),
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
    if let Ok(tup) = p.downcast::<PyTuple>() {
        for item in tup.iter() {
            out.push(py_to_sql(&item)?);
        }
    } else if let Ok(list) = p.downcast::<PyList>() {
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
pub fn val_to_py(val: &SqlValue, py: Python<'_>) -> PyObject {
    match val {
        SqlValue::Null => py.None(),
        SqlValue::Integer(i) => i.to_object(py),
        SqlValue::Real(f) => f.to_object(py),
        SqlValue::Text(s) => s.to_object(py),
        SqlValue::Blob(b) => PyBytes::new_bound(py, b).into(),
    }
}

/// Convert a Python object to a rusqlite Value.
pub fn py_to_rusqlite_value(obj: &PyObject, py: Python<'_>) -> rusqlite::types::Value {
    let obj = obj.bind(py);
    py_to_sql(obj).unwrap_or(rusqlite::types::Value::Null)
}
