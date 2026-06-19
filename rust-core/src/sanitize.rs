//! Message sanitization — pure string functions.
//!
//! Phase 4: Rust implementation of the pure-string functions from
//! ``agent/message_sanitization.py``. In-place list/dict mutation functions
//! remain in Python due to PyO3 complexity.

use pyo3::prelude::*;

fn replace_surrogates(s: &str) -> String {
    let mut r = String::with_capacity(s.len());
    for c in s.chars() {
        let u = c as u32;
        if (0xD800..=0xDFFF).contains(&u) { r.push('\u{FFFD}'); }
        else { r.push(c); }
    }
    r
}

fn strip_ascii(s: &str) -> String {
    s.chars().filter(|c| c.is_ascii()).collect()
}

// ── Pure string functions ─────────────────────────────────────────────────

#[pyfunction]
pub fn sanitize_surrogates_rs(text: &str) -> String {
    replace_surrogates(text)
}

#[pyfunction]
pub fn strip_non_ascii_rs(text: &str) -> String {
    strip_ascii(text)
}

#[pyfunction]
pub fn escape_invalid_chars_in_json_strings_rs(raw: &str) -> String {
    let mut result = String::with_capacity(raw.len());
    let chars: Vec<char> = raw.chars().collect();
    let mut i = 0;
    let mut in_string = false;
    let mut escaping = false;
    while i < chars.len() {
        let c = chars[i];
        if escaping { result.push(c); escaping = false; i += 1; continue; }
        if c == '"' { in_string = !in_string; result.push(c); }
        else if c == '\\' && in_string { result.push(c); escaping = true; }
        else if in_string && (c as u32) < 0x20 { result.push_str(&format!("\\u{:04x}", c as u32)); }
        else { result.push(c); }
        i += 1;
    }
    result
}

#[pyfunction]
pub fn repair_tool_call_arguments_rs(raw_args: &str, _tool_name: &str) -> String {
    let t = raw_args.trim();
    if t.is_empty() || t.eq_ignore_ascii_case("none") { return "{}".into(); }
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(t) { return v.to_string(); }
    // Strip trailing comma before closing brace/bracket
    let mut s = t.to_string();
    if s.ends_with(",}") || s.ends_with(",]") {
        s.pop(); // remove ]
        s.pop(); // remove ,
        s.push_str(if s.ends_with('[') { "]" } else { "}" });
    } else {
        s = s.trim_end_matches(',').to_string();
    }
    // Close unclosed braces
    let ob = s.matches('{').count().saturating_sub(s.matches('}').count());
    let obr = s.matches('[').count().saturating_sub(s.matches(']').count());
    for _ in 0..obr { s.push(']'); }
    for _ in 0..ob { s.push('}'); }
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(&s) { return v.to_string(); }
    let mut c = t.to_string();
    for _ in 0..50 {
        if c.matches('}').count() <= c.matches('{').count() { break; }
        if let Some(p) = c.rfind('}') { c.remove(p); }
    }
    c = escape_invalid_chars_in_json_strings_rs(&c);
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(&c) { return v.to_string(); }
    "{}".into()
}

// ── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test] fn test_sanitize_surrogates() {
        assert_eq!(sanitize_surrogates_rs("hello\u{FFFD}world"), "hello\u{FFFD}world");
        assert_eq!(sanitize_surrogates_rs("hello world"), "hello world");
    }
    #[test] fn test_strip_non_ascii() {
        assert_eq!(strip_non_ascii_rs("hello"), "hello");
        assert_eq!(strip_non_ascii_rs("héllo 世界"), "hllo ");
    }
    #[test] fn test_repair_empty() {
        assert_eq!(repair_tool_call_arguments_rs("", "?"), "{}");
        assert_eq!(repair_tool_call_arguments_rs("None", "?"), "{}");
    }
    #[test] fn test_repair_valid() {
        let r = repair_tool_call_arguments_rs(r#"{"a":1}"#, "?");
        let v: serde_json::Value = serde_json::from_str(&r).unwrap();
        assert_eq!(v["a"], 1);
    }
    #[test] fn test_repair_trailing_comma() {
        let r = repair_tool_call_arguments_rs(r#"{"a":1,}"#, "?");
        let v: serde_json::Value = serde_json::from_str(&r).unwrap();
        assert_eq!(v["a"], 1);
    }
}
