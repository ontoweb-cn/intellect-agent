//! Tool & prompt utility functions — pure computation ported from Python.
//!
//! Task 2: ``file_mutation_result_landed`` from ``agent/tool_result_classification.py``.
//! Task 3: ``_strip_yaml_frontmatter``, ``_truncate_content`` from ``agent/prompt_builder.py``.
//! Task 5: ``_paths_overlap`` from ``agent/tool_dispatch_helpers.py``.
//! Task 6: ``canonical_tool_args`` from ``agent/tool_guardrails.py``.

use pyo3::prelude::*;

// ── Task 2: File mutation result classification ────────────────────────────

/// Return True when a file mutation result proves the write landed.
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

// ── Task 3: Prompt builder string helpers ───────────────────────────────────

/// Strip YAML frontmatter (``---`` delimited) from content.
#[pyfunction]
pub fn strip_yaml_frontmatter_rs(content: &str) -> String {
    if content.starts_with("---") {
        if let Some(end) = content[3..].find("\n---") {
            let body = &content[end + 7..]; // skip "\n---" (4 chars) + the original 3
            let trimmed = body.trim_start_matches('\n');
            if !trimmed.is_empty() {
                return trimmed.to_string();
            }
        }
    }
    content.to_string()
}

/// Truncate content to max_chars, adding a truncation note if cut.
#[pyfunction]
pub fn truncate_content_rs(content: &str, max_chars: usize) -> String {
    if content.len() <= max_chars {
        return content.to_string();
    }
    let truncated = &content[..max_chars];
    format!("{truncated}\n\n[... content truncated, {remaining} more characters ...]",
        remaining = content.len() - max_chars)
}

// ── Task 5: Tool dispatch helpers ───────────────────────────────────────────

/// Check if two paths overlap (one is a prefix of the other).
#[pyfunction]
pub fn paths_overlap_rs(a: &str, b: &str) -> bool {
    let a = a.trim_end_matches('/');
    let b = b.trim_end_matches('/');
    a == b || a.starts_with(&format!("{b}/")) || b.starts_with(&format!("{a}/"))
}

// ── Task 6: Tool guardrail helpers ──────────────────────────────────────────

/// Build a canonical sorted-compact-JSON representation of tool args.
#[pyfunction]
pub fn canonical_tool_args_rs(name: &str, args: &Bound<'_, PyAny>) -> PyResult<String> {
    let py = args.py();
    let json_mod = py.import_bound("json")?;
    let dumped: String = json_mod
        .call_method1("dumps", (args,))?
        .extract()?;
    // Re-parse to sort keys
    let mut val: serde_json::Value = serde_json::from_str(&dumped).unwrap_or(serde_json::Value::Null);
    if let Some(obj) = val.as_object_mut() {
        let sorted: serde_json::Map<String, serde_json::Value> = {
            let mut keys: Vec<&String> = obj.keys().collect();
            keys.sort();
            keys.into_iter().map(|k| (k.clone(), obj[k].clone())).collect()
        };
        *obj = sorted;
    }
    let canonical = serde_json::to_string(&val).unwrap_or_default();
    Ok(format!("{name}:{canonical}"))
}

// ── Tests ───────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test] fn test_strip_yaml_empty() { assert_eq!(strip_yaml_frontmatter_rs("hello"), "hello"); }
    #[test] fn test_strip_yaml_front() {
        assert_eq!(strip_yaml_frontmatter_rs("---\ntitle: test\n---\nbody"), "body");
    }
    #[test] fn test_truncate_short() { assert_eq!(truncate_content_rs("hi", 100), "hi"); }
    #[test] fn test_truncate_long() { assert!(truncate_content_rs("hello world", 5).contains("truncated")); }
    #[test] fn test_overlap_same() { assert!(paths_overlap_rs("/a/b", "/a/b/")); }
    #[test] fn test_overlap_parent() { assert!(paths_overlap_rs("/a", "/a/b")); }
    #[test] fn test_overlap_no() { assert!(!paths_overlap_rs("/a", "/b")); }
}
