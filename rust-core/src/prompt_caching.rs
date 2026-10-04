//! Anthropic prompt caching strategy — pure dict manipulation.
//!
//! Port of ``agent/prompt_caching.py``.  The ``system_and_3`` layout places
//! up to 4 cache_control breakpoints on the system prompt + last 3 non-system
//! messages.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

fn apply_cache_marker_to_msg(msg: &Bound<'_, PyDict>, marker: &Bound<'_, PyDict>, native_anthropic: bool) -> PyResult<()> {
    let role: String = msg.get_item("role")?
        .and_then(|v| v.extract().ok())
        .unwrap_or_default();

    if role == "tool" {
        if native_anthropic {
            msg.set_item("cache_control", marker)?;
        }
        return Ok(());
    }

    let content = msg.get_item("content")?;
    let is_empty = content.as_ref().map_or(true, |c| c.is_none() || (c.extract::<String>().ok().map_or(false, |s| s.is_empty())));

    if is_empty {
        msg.set_item("cache_control", marker)?;
        return Ok(());
    }

    // String content → wrap in content-parts list with marker on the text part
    if let Some(c) = &content {
        if let Ok(text) = c.extract::<String>() {
            let py = msg.py();
            let text_part = PyDict::new(py);
            text_part.set_item("type", "text")?;
            text_part.set_item("text", text)?;
            text_part.set_item("cache_control", marker)?;
            // `PyList::new` became fallible in pyo3 0.23 (element conversion
            // can now fail); with a `Bound<PyDict>` element it cannot.
            let parts = PyList::new(py, [text_part])?;
            msg.set_item("content", parts)?;
            return Ok(());
        }
    }

    // List content → add marker to last element
    if let Some(c) = &content {
        if let Ok(list) = c.cast::<PyList>() {
            let len = list.len();
            if len > 0 {
                if let Ok(last) = list.get_item(len - 1) {
                    if let Ok(last_dict) = last.cast::<PyDict>() {
                        last_dict.set_item("cache_control", marker)?;
                    }
                }
            }
        }
    }

    Ok(())
}

#[pyfunction]
#[pyo3(signature = (api_messages, cache_ttl="5m", native_anthropic=false))]
pub fn apply_anthropic_cache_control_rs(
    api_messages: &Bound<'_, PyList>,
    cache_ttl: &str,
    native_anthropic: bool,
) -> PyResult<Py<PyList>> {
    let py = api_messages.py();

    // Build the cache marker
    let marker = PyDict::new(py);
    marker.set_item("type", "ephemeral")?;
    if cache_ttl == "1h" {
        marker.set_item("ttl", "1h")?;
    }

    // Deep copy via Python's copy.deepcopy
    let copy_mod = py.import("copy")?;
    let messages: Bound<'_, PyList> = copy_mod
        .call_method1("deepcopy", (api_messages,))?
        .cast_into::<PyList>()?;
    let len = messages.len();
    if len == 0 {
        return Ok(messages.unbind());
    }

    let mut breakpoints_used = 0u32;

    // System message gets first breakpoint
    if let Ok(first) = messages.get_item(0) {
        if let Ok(first_dict) = first.cast::<PyDict>() {
            let role: String = first_dict.get_item("role")?
                .and_then(|v| v.extract().ok())
                .unwrap_or_default();
            if role == "system" {
                apply_cache_marker_to_msg(&first_dict, &marker, native_anthropic)?;
                breakpoints_used += 1;
            }
        }
    }

    // Find indices of non-system messages, take last (4 - breakpoints_used)
    let remaining = 4usize.saturating_sub(breakpoints_used as usize);
    if remaining > 0 {
        let mut non_sys: Vec<usize> = Vec::new();
        for i in 0..len {
            if let Ok(msg) = messages.get_item(i) {
                if let Ok(d) = msg.cast::<PyDict>() {
                    let role: String = d.get_item("role")?
                        .and_then(|v| v.extract().ok())
                        .unwrap_or_default();
                    if role != "system" {
                        non_sys.push(i);
                    }
                }
            }
        }
        let start = non_sys.len().saturating_sub(remaining);
        for &idx in &non_sys[start..] {
            if let Ok(msg) = messages.get_item(idx) {
                if let Ok(d) = msg.cast::<PyDict>() {
                    apply_cache_marker_to_msg(&d, &marker, native_anthropic)?;
                }
            }
        }
    }

    Ok(messages.unbind())
}

// ── Tests ───────────────────────────────────────────────────────────────────
//
// These exercise the real Python-object path (PyDict inputs, copy.deepcopy),
// so they need an initialized interpreter — the same constraint that makes
// rust-ci run `cargo test -- --test-threads=1`.

#[cfg(test)]
mod tests {
    use super::*;
    use pyo3::types::PyString;

    fn msg<'py>(py: Python<'py>, role: &str, content: Bound<'py, PyAny>) -> Bound<'py, PyDict> {
        let d = PyDict::new(py);
        d.set_item("role", role).unwrap();
        d.set_item("content", content).unwrap();
        d
    }

    fn text_msg<'py>(py: Python<'py>, role: &str, text: &str) -> Bound<'py, PyDict> {
        msg(py, role, PyString::new(py, text).into_any())
    }

    fn list_of<'py>(py: Python<'py>, msgs: Vec<Bound<'py, PyDict>>) -> Bound<'py, PyList> {
        PyList::new(py, msgs).unwrap()
    }

    /// Owned dict at list index (cast borrows a temporary otherwise).
    fn dict_at<'py>(list: &Bound<'py, PyList>, i: usize) -> Bound<'py, PyDict> {
        let item = list.get_item(i).unwrap();
        item.cast::<PyDict>().unwrap().clone()
    }

    /// Owned sub-dict at dict key.
    fn sub_dict<'py>(d: &Bound<'py, PyDict>, key: &str) -> Bound<'py, PyDict> {
        d.get_item(key).unwrap().expect(key).cast::<PyDict>().unwrap().clone()
    }

    /// A message "has a breakpoint" when the marker landed anywhere the
    /// production code puts it: top level (tool/empty-content) or on the
    /// last content part (string-wrap / list forms).
    fn has_breakpoint(m: &Bound<'_, PyDict>) -> bool {
        if m.contains("cache_control").unwrap_or(false) {
            return true;
        }
        if let Ok(Some(c)) = m.get_item("content") {
            if let Ok(list) = c.cast::<PyList>() {
                if list.len() > 0 {
                    if let Ok(last) = list.get_item(list.len() - 1) {
                        if let Ok(d) = last.cast::<PyDict>() {
                            return d.contains("cache_control").unwrap_or(false);
                        }
                    }
                }
            }
        }
        false
    }

    fn breakpoint_flags(out: &Bound<'_, PyList>) -> Vec<bool> {
        (0..out.len()).map(|i| has_breakpoint(&dict_at(out, i))).collect()
    }

    #[test]
    fn test_system_plus_last_three() {
        // Interpreter init is explicit here: the crate builds without
        // pyo3's auto-initialize feature, so tests that touch Python must
        // initialize it first (idempotent; suite runs --test-threads=1).
        Python::initialize();
        Python::attach(|py| {
            let input = list_of(py, vec![
                text_msg(py, "system", "sys"),
                text_msg(py, "user", "u1"),
                text_msg(py, "assistant", "a1"),
                text_msg(py, "user", "u2"),
                text_msg(py, "assistant", "a2"),
                text_msg(py, "user", "u3"),
            ]);
            let out = apply_anthropic_cache_control_rs(&input, "5m", false).unwrap();
            let out = out.bind(py);
            assert_eq!(out.len(), 6);
            // system + last 3 non-system (indices 3,4,5); u1/a1 untouched.
            assert_eq!(breakpoint_flags(out), vec![true, false, false, true, true, true]);
        });
    }

    #[test]
    fn test_no_system_last_four() {
        // Interpreter init is explicit here: the crate builds without
        // pyo3's auto-initialize feature, so tests that touch Python must
        // initialize it first (idempotent; suite runs --test-threads=1).
        Python::initialize();
        Python::attach(|py| {
            let input = list_of(py, vec![
                text_msg(py, "user", "u1"),
                text_msg(py, "assistant", "a1"),
                text_msg(py, "user", "u2"),
                text_msg(py, "assistant", "a2"),
                text_msg(py, "user", "u3"),
                text_msg(py, "assistant", "a3"),
            ]);
            let out = apply_anthropic_cache_control_rs(&input, "5m", false).unwrap();
            let out = out.bind(py);
            // No system → 4 breakpoints land on the last 4 messages.
            assert_eq!(breakpoint_flags(out), vec![false, false, true, true, true, true]);
        });
    }

    #[test]
    fn test_fewer_than_four_get_all() {
        // Interpreter init is explicit here: the crate builds without
        // pyo3's auto-initialize feature, so tests that touch Python must
        // initialize it first (idempotent; suite runs --test-threads=1).
        Python::initialize();
        Python::attach(|py| {
            let input = list_of(py, vec![
                text_msg(py, "system", "sys"),
                text_msg(py, "user", "u1"),
                text_msg(py, "assistant", "a1"),
            ]);
            let out = apply_anthropic_cache_control_rs(&input, "5m", false).unwrap();
            let out = out.bind(py);
            assert_eq!(breakpoint_flags(out), vec![true, true, true]);
        });
    }

    #[test]
    fn test_empty_list_passthrough() {
        // Interpreter init is explicit here: the crate builds without
        // pyo3's auto-initialize feature, so tests that touch Python must
        // initialize it first (idempotent; suite runs --test-threads=1).
        Python::initialize();
        Python::attach(|py| {
            let input = PyList::empty(py);
            let out = apply_anthropic_cache_control_rs(&input, "5m", false).unwrap();
            assert_eq!(out.bind(py).len(), 0);
        });
    }

    #[test]
    fn test_input_not_mutated() {
        // Interpreter init is explicit here: the crate builds without
        // pyo3's auto-initialize feature, so tests that touch Python must
        // initialize it first (idempotent; suite runs --test-threads=1).
        Python::initialize();
        Python::attach(|py| {
            let input = list_of(py, vec![
                text_msg(py, "system", "sys"),
                text_msg(py, "user", "u1"),
            ]);
            apply_anthropic_cache_control_rs(&input, "5m", false).unwrap();
            // Deep-copied: original stays a plain str with no marker.
            let orig = dict_at(&input, 0);
            let content = orig.get_item("content").unwrap().expect("content");
            assert!(content.cast::<PyString>().is_ok());
            assert!(!orig.contains("cache_control").unwrap());
        });
    }

    #[test]
    fn test_string_content_wrapped_into_parts() {
        // Interpreter init is explicit here: the crate builds without
        // pyo3's auto-initialize feature, so tests that touch Python must
        // initialize it first (idempotent; suite runs --test-threads=1).
        Python::initialize();
        Python::attach(|py| {
            let input = list_of(py, vec![text_msg(py, "user", "hello")]);
            let out = apply_anthropic_cache_control_rs(&input, "5m", false).unwrap();
            let m = dict_at(out.bind(py), 0);
            let content = m.get_item("content").unwrap().expect("content");
            let list = content.cast::<PyList>().unwrap().clone();
            assert_eq!(list.len(), 1);
            let part = dict_at(&list, 0);
            assert_eq!(
                part.get_item("type").unwrap().expect("type").extract::<String>().unwrap(),
                "text"
            );
            assert_eq!(
                part.get_item("text").unwrap().expect("text").extract::<String>().unwrap(),
                "hello"
            );
            let cc = sub_dict(&part, "cache_control");
            assert_eq!(
                cc.get_item("type").unwrap().expect("type").extract::<String>().unwrap(),
                "ephemeral"
            );
            assert!(cc.get_item("ttl").unwrap().is_none()); // 5m default → no ttl field
        });
    }

    #[test]
    fn test_ttl_1h_marker() {
        // Interpreter init is explicit here: the crate builds without
        // pyo3's auto-initialize feature, so tests that touch Python must
        // initialize it first (idempotent; suite runs --test-threads=1).
        Python::initialize();
        Python::attach(|py| {
            let input = list_of(py, vec![text_msg(py, "user", "hello")]);
            let out = apply_anthropic_cache_control_rs(&input, "1h", false).unwrap();
            let m = dict_at(out.bind(py), 0);
            let content = m.get_item("content").unwrap().expect("content");
            let list = content.cast::<PyList>().unwrap().clone();
            let part = dict_at(&list, 0);
            let cc = sub_dict(&part, "cache_control");
            assert_eq!(
                cc.get_item("ttl").unwrap().expect("ttl").extract::<String>().unwrap(),
                "1h"
            );
        });
    }

    #[test]
    fn test_tool_role_native_vs_not() {
        // Interpreter init is explicit here: the crate builds without
        // pyo3's auto-initialize feature, so tests that touch Python must
        // initialize it first (idempotent; suite runs --test-threads=1).
        Python::initialize();
        Python::attach(|py| {
            let input = list_of(py, vec![text_msg(py, "tool", "result")]);
            // native_anthropic=true → top-level marker on tool message
            let out = apply_anthropic_cache_control_rs(&input, "5m", true).unwrap();
            let m = dict_at(out.bind(py), 0);
            assert!(m.contains("cache_control").unwrap());
            // native_anthropic=false → no marker anywhere on tool message
            let out2 = apply_anthropic_cache_control_rs(&input, "5m", false).unwrap();
            let m2 = dict_at(out2.bind(py), 0);
            assert!(!has_breakpoint(&m2));
        });
    }

    #[test]
    fn test_empty_content_top_level_marker() {
        // Interpreter init is explicit here: the crate builds without
        // pyo3's auto-initialize feature, so tests that touch Python must
        // initialize it first (idempotent; suite runs --test-threads=1).
        Python::initialize();
        Python::attach(|py| {
            let d = PyDict::new(py);
            d.set_item("role", "user").unwrap();
            d.set_item("content", py.None()).unwrap();
            let input = list_of(py, vec![d]);
            let out = apply_anthropic_cache_control_rs(&input, "5m", false).unwrap();
            let m = dict_at(out.bind(py), 0);
            assert!(m.contains("cache_control").unwrap());
        });
    }

    #[test]
    fn test_list_content_marker_on_last_part() {
        // Interpreter init is explicit here: the crate builds without
        // pyo3's auto-initialize feature, so tests that touch Python must
        // initialize it first (idempotent; suite runs --test-threads=1).
        Python::initialize();
        Python::attach(|py| {
            let p1 = PyDict::new(py);
            p1.set_item("type", "text").unwrap();
            p1.set_item("text", "one").unwrap();
            let p2 = PyDict::new(py);
            p2.set_item("type", "text").unwrap();
            p2.set_item("text", "two").unwrap();
            let parts = PyList::new(py, [p1, p2]).unwrap();
            let input = list_of(py, vec![msg(py, "user", parts.into_any())]);
            let out = apply_anthropic_cache_control_rs(&input, "5m", false).unwrap();
            let m = dict_at(out.bind(py), 0);
            let content = m.get_item("content").unwrap().expect("content");
            let list = content.cast::<PyList>().unwrap().clone();
            assert_eq!(list.len(), 2);
            assert!(!dict_at(&list, 0).contains("cache_control").unwrap());
            assert!(dict_at(&list, 1).contains("cache_control").unwrap());
        });
    }
}
