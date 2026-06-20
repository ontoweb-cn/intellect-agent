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
            let text_part = PyDict::new_bound(py);
            text_part.set_item("type", "text")?;
            text_part.set_item("text", text)?;
            text_part.set_item("cache_control", marker)?;
            let parts = PyList::new_bound(py, [text_part]);
            msg.set_item("content", parts)?;
            return Ok(());
        }
    }

    // List content → add marker to last element
    if let Some(c) = &content {
        if let Ok(list) = c.downcast::<PyList>() {
            let len = list.len();
            if len > 0 {
                if let Ok(last) = list.get_item(len - 1) {
                    if let Ok(last_dict) = last.downcast::<PyDict>() {
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
    let marker = PyDict::new_bound(py);
    marker.set_item("type", "ephemeral")?;
    if cache_ttl == "1h" {
        marker.set_item("ttl", "1h")?;
    }

    // Deep copy via Python's copy.deepcopy
    let copy_mod = py.import_bound("copy")?;
    let messages: Bound<'_, PyList> = copy_mod
        .call_method1("deepcopy", (api_messages,))?
        .downcast_into::<PyList>()?;
    let len = messages.len();
    if len == 0 {
        return Ok(messages.unbind());
    }

    let mut breakpoints_used = 0u32;

    // System message gets first breakpoint
    if let Ok(first) = messages.get_item(0) {
        if let Ok(first_dict) = first.downcast::<PyDict>() {
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
                if let Ok(d) = msg.downcast::<PyDict>() {
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
                if let Ok(d) = msg.downcast::<PyDict>() {
                    apply_cache_marker_to_msg(&d, &marker, native_anthropic)?;
                }
            }
        }
    }

    Ok(messages.unbind())
}
