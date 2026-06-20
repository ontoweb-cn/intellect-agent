//! Tool utility functions — pure computation ported from Python.
//!
//! Task 2: ``file_mutation_result_landed`` from ``agent/tool_result_classification.py``.
//! Task 6: guardrail helpers from ``agent/tool_guardrails.py``.

use pyo3::prelude::*;

/// Return True when a file mutation result proves the write landed.
/// Port of ``agent/tool_result_classification.py:file_mutation_result_landed``.
#[pyfunction]
pub fn file_mutation_result_landed_rs(tool_name: &str, result: &Bound<'_, PyAny>) -> PyResult<bool> {
    if tool_name != "write_file" && tool_name != "patch" {
        return Ok(false);
    }
    let result_str: String = match result.extract() {
        Ok(s) => s,
        Err(_) => return Ok(false),
    };
    let data: serde_json::Value = match serde_json::from_str(result_str.trim()) {
        Ok(v) => v,
        Err(_) => return Ok(false),
    };
    let obj = match data.as_object() {
        Some(o) => o,
        None => return Ok(false),
    };
    if obj.contains_key("error") {
        return Ok(false);
    }
    if tool_name == "write_file" {
        return Ok(obj.contains_key("bytes_written"));
    }
    if tool_name == "patch" {
        return Ok(obj.get("success").and_then(|v| v.as_bool()) == Some(true));
    }
    Ok(false)
}
