//! Token estimation — pure computation.
//!
//! Phase 5: Rust implementation of token estimation functions from
//! ``agent/model_metadata.py``.

use pyo3::prelude::*;

/// Extract the first positive integer found in `s` after skipping any
/// leading non-digit characters (spaces, colons, equals, etc.).
fn extract_first_number(s: &str) -> Option<i64> {
    let digits: String = s.chars()
        .skip_while(|c| !c.is_ascii_digit())
        .take_while(|c| c.is_ascii_digit())
        .collect();
    digits.parse::<i64>().ok().filter(|&n| n > 0)
}

// ── Token estimation ──────────────────────────────────────────────────────

#[pyfunction]
pub fn estimate_tokens_rough_rs(text: &str) -> i64 {
    if text.is_empty() { 0 } else { ((text.len() as i64) + 3) / 4 }
}

#[pyfunction]
pub fn grok_supports_reasoning_effort_rs(model: &str) -> bool {
    let lower = model.to_lowercase();
    lower.contains("grok") && !lower.contains("grok-3")
}

#[pyfunction]
pub fn parse_context_limit_from_error_rs(error_msg: &str) -> Option<i64> {
    let lower = error_msg.to_lowercase();
    for pattern in &["maximum context length is", "maximum is", "max_tokens:", "context_length:"] {
        if let Some(pos) = lower.find(pattern) {
            let rest = &lower[pos + pattern.len()..];
            if let Some(n) = extract_first_number(rest) {
                return Some(n);
            }
        }
    }
    None
}

#[pyfunction]
pub fn parse_available_output_tokens_from_error_rs(error_msg: &str) -> Option<i64> {
    let lower = error_msg.to_lowercase();
    for pattern in &["maximum output tokens is", "available output tokens:", "max_completion_tokens:"] {
        if let Some(pos) = lower.find(pattern) {
            let rest = &lower[pos + pattern.len()..];
            if let Some(n) = extract_first_number(rest) {
                return Some(n);
            }
        }
    }
    None
}

// ── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test] fn test_estimate_empty() { assert_eq!(estimate_tokens_rough_rs(""), 0); }
    #[test] fn test_estimate_short() { assert_eq!(estimate_tokens_rough_rs("hi"), 1); }
    #[test] fn test_estimate_medium() { assert_eq!(estimate_tokens_rough_rs("hello"), 2); }
    #[test] fn test_estimate_long() { assert_eq!(estimate_tokens_rough_rs("this is a longer text for testing"), 9); }
    #[test] fn test_grok() {
        assert!(grok_supports_reasoning_effort_rs("grok-4"));
        assert!(grok_supports_reasoning_effort_rs("xai/grok-4-desktop"));
        assert!(!grok_supports_reasoning_effort_rs("grok-3"));
        assert!(!grok_supports_reasoning_effort_rs("claude-opus-4"));
    }
}
