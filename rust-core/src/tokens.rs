//! Token estimation & model name utilities — pure computation.
//!
//! Phase 5: Rust implementations of functions from ``agent/model_metadata.py``.
//! M2 batch: grok allowlist fix, provider prefix strip, model name helpers.

use pyo3::prelude::*;
use regex::Regex;

use std::sync::LazyLock;

/// Extract the first positive integer found in `s` after skipping any
/// leading non-digit characters (spaces, colons, equals, etc.).
/// Only returns values in the reasonable context-length range [1024, 10_000_000].
fn extract_context_number(s: &str) -> Option<i64> {
    let digits: String = s.chars()
        .skip_while(|c| !c.is_ascii_digit())
        .take_while(|c| c.is_ascii_digit())
        .collect();
    digits.parse::<i64>().ok().filter(|&n| (1024..=10_000_000).contains(&n))
}

/// Extract the first positive integer found in `s` (no range check).
fn extract_first_number(s: &str) -> Option<i64> {
    let digits: String = s.chars()
        .skip_while(|c| !c.is_ascii_digit())
        .take_while(|c| c.is_ascii_digit())
        .collect();
    digits.parse::<i64>().ok().filter(|&n| n > 0)
}

// ── Provider prefix stripping ──────────────────────────────────────────────

/// Provider names that can appear as a "provider:" prefix before a model ID.
/// Sorted for binary_search — keep in lexicographic order.
const PROVIDER_PREFIXES: &[&str] = &[
    "alibaba", "aliyun", "anthropic", "arcee", "arcee-ai", "arceeai",
    "bytedance",
    "claude",
    "copilot", "copilot-acp", "custom",
    "dashscope", "deep-seek", "deepseek", "doubao", "doubao-agent",
    "doubao-coding",
    "gemini", "github", "github-copilot", "github-models", "glm",
    "gmi", "gmi-cloud", "gmicloud", "go", "google", "google-ai-studio",
    "google-gemini", "grok",
    "kilo", "kilocode", "kimi", "kimi-cn", "kimi-coding", "kimi-coding-cn",
    "local",
    "mimo", "minimax", "minimax-cn", "minimax-oauth", "moonshot", "moonshot-cn",
    "nemotron", "nim", "novita", "novita-ai", "novitaai", "nvidia", "nvidia-nim",
    "ollama", "ollama-cloud", "ontoweb",
    "openai-codex", "opencode", "opencode-go", "opencode-zen", "openrouter",
    "qwen", "qwen-oauth", "qwen-portal",
    "stepfun",
    "tencent", "tencent-cloud", "tencent-tokenhub", "tencentmaas", "tokenhub",
    "volc", "volc-agent", "volc-coding", "volcengine", "volcengine-agent",
    "volcengine-agent-plan", "volcengine-coding", "volcengine-coding-plan",
    "x-ai", "x.ai", "xai", "xiaomi", "xiaomi-mimo",
    "z-ai", "z.ai", "zai", "zen", "zhipu",
];

/// Regex-equivalent check for Ollama tag pattern (e.g. "7b", "latest", "q4_0").
fn looks_like_ollama_tag(suffix: &str) -> bool {
    let s = suffix.trim();
    if s.is_empty() {
        return false;
    }
    let first_word = s.split(|c: char| c.is_whitespace() || c == '-').next().unwrap_or(s);
    let lower = first_word.to_lowercase();
    // Numeric tags: "7b", "70b", "0.5b", etc.
    if lower.ends_with('b') {
        let num_part = &lower[..lower.len() - 1];
        if !num_part.is_empty() && num_part.chars().all(|c| c.is_ascii_digit() || c == '.') {
            return true;
        }
    }
    // FP/quant tags: "fp16", "fp8", "q4", "q4_0", "q8_0", "f16"
    if lower.starts_with("fp") || lower.starts_with("f") {
        let rest = if lower.starts_with("fp") { &lower[2..] } else { &lower[1..] };
        if rest.chars().all(|c| c.is_ascii_digit()) {
            return true;
        }
    }
    if lower.starts_with('q') {
        let rest = &lower[1..];
        if rest.chars().all(|c| c.is_ascii_digit() || c == '_') {
            return true;
        }
    }
    // Known tag words
    matches!(
        lower.as_str(),
        "latest" | "stable" | "instruct" | "chat" | "coder" | "vision" | "text"
    )
}

#[pyfunction]
pub fn strip_provider_prefix_rs(model: &str) -> String {
    if !model.contains(':') || model.starts_with("http") {
        return model.to_string();
    }
    let mut parts = model.splitn(2, ':');
    let prefix = parts.next().unwrap_or("");
    let suffix = parts.next().unwrap_or("");
    let prefix_lower = prefix.trim().to_lowercase();
    if PROVIDER_PREFIXES.binary_search(&prefix_lower.as_str()).is_ok() {
        // Don't strip if suffix looks like an Ollama tag
        if looks_like_ollama_tag(suffix) {
            return model.to_string();
        }
        return suffix.to_string();
    }
    model.to_string()
}

// ── Model name helpers ─────────────────────────────────────────────────────

#[pyfunction]
pub fn model_name_suggests_kimi_rs(model: &str) -> bool {
    let lower = model.to_lowercase();
    lower.starts_with("kimi") || lower.contains("moonshot")
}

#[pyfunction]
pub fn model_id_matches_rs(candidate_id: &str, lookup_model: &str) -> bool {
    if candidate_id == lookup_model {
        return true;
    }
    // Slug match: basename of candidate equals the lookup name
    if let Some(pos) = candidate_id.rfind('/') {
        if &candidate_id[pos + 1..] == lookup_model {
            return true;
        }
    }
    false
}

#[pyfunction]
pub fn normalize_model_version_rs(model: &str) -> String {
    model.replace('.', "-")
}

// ── Context probe tiers ────────────────────────────────────────────────────

/// Mirror of ``agent/model_metadata.py:CONTEXT_PROBE_TIERS``.
const CONTEXT_PROBE_TIERS: &[i64] = &[256_000, 128_000, 64_000, 32_000, 16_000, 8_000];

#[pyfunction]
pub fn get_next_probe_tier_rs(current_length: i64) -> Option<i64> {
    for &tier in CONTEXT_PROBE_TIERS {
        if tier < current_length {
            return Some(tier);
        }
    }
    None
}

// ── Token estimation ──────────────────────────────────────────────────────

/// Rough token estimate using character count (not byte count).
/// Matches Python's ``len(text)`` behavior for multi-byte Unicode.
#[pyfunction]
pub fn estimate_tokens_rough_rs(text: &str) -> i64 {
    let char_count = text.chars().count() as i64;
    if char_count == 0 { 0 } else { (char_count + 3) / 4 }
}

// ── Grok reasoning effort ─────────────────────────────────────────────────

/// Explicit allowlist of Grok model prefixes that support ``reasoning.effort``.
const GROK_EFFORT_CAPABLE_PREFIXES: &[&str] = &[
    "grok-3-mini",
    "grok-4.20-multi-agent",
    "grok-4.3",
];

#[pyfunction]
pub fn grok_supports_reasoning_effort_rs(model: &str) -> bool {
    let name = model.trim().to_lowercase();
    if name.is_empty() {
        return false;
    }
    // Strip common aggregator prefixes (x-ai/, openrouter/x-ai/, xai/, ...)
    let bare = if let Some(pos) = name.rfind('/') {
        &name[pos + 1..]
    } else {
        &name
    };
    GROK_EFFORT_CAPABLE_PREFIXES.iter().any(|prefix| bare.starts_with(prefix))
}

// ── Context limit parsing ─────────────────────────────────────────────────

/// Compiled regexes for context-limit-from-error parsing.
/// Mirrors the 5-pattern set from the old ``agent/model_metadata.py``.
fn context_limit_regexes() -> &'static [Regex] {
    static REGEXES: LazyLock<Vec<Regex>> = LazyLock::new(|| {
        vec![
            // 1. "max context length is N", "maximum is N", "limit: N"
            Regex::new(r"(?i)(?:max(?:imum)?|limit)\s*(?:context\s*)?(?:length|size|window)?\s*(?:is|of|:)?\s*(\d{4,})").unwrap(),
            // 2. "context length N", "context size: N", "context window of N"
            Regex::new(r"(?i)context\s*(?:length|size|window)\s*(?:is|of|:)?\s*(\d{4,})").unwrap(),
            // 3. "N tokens context", "N limit"
            Regex::new(r"(?i)(\d{4,})\s*(?:token)?\s*(?:context|limit)").unwrap(),
            // 4. "> N maximum", "> N limit", "> N token"
            Regex::new(r"(?i)>\s*(\d{4,})\s*(?:max|limit|token)").unwrap(),
            // 5. "N maximum" at word boundary
            Regex::new(r"(?i)(\d{4,})\s*(?:max(?:imum)?)\b").unwrap(),
        ]
    });
    &REGEXES
}

#[pyfunction]
pub fn parse_context_limit_from_error_rs(error_msg: &str) -> Option<i64> {
    for re in context_limit_regexes() {
        if let Some(caps) = re.captures(error_msg) {
            if let Some(m) = caps.get(1) {
                if let Ok(n) = m.as_str().parse::<i64>() {
                    if (1024..=10_000_000).contains(&n) {
                        return Some(n);
                    }
                }
            }
        }
    }
    None
}

// ── Available output tokens parsing ────────────────────────────────────────

/// Compiled regexes for available-output-tokens-from-error parsing.
/// Mirrors the 3-pattern set from the old ``agent/model_metadata.py``.
fn output_tokens_regexes() -> &'static [Regex] {
    static REGEXES: LazyLock<Vec<Regex>> = LazyLock::new(|| {
        vec![
            // 1. "available_tokens: N" (underscore, colon/space)
            Regex::new(r"(?i)available_tokens[:\s]+(\d+)").unwrap(),
            // 2. "available tokens: N" (space, colon/space)
            Regex::new(r"(?i)available\s+tokens[:\s]+(\d+)").unwrap(),
            // 3. "= N" at end of line (Anthropic format)
            Regex::new(r"(?i)=\s*(\d+)\s*$").unwrap(),
        ]
    });
    &REGEXES
}

#[pyfunction]
pub fn parse_available_output_tokens_from_error_rs(error_msg: &str) -> Option<i64> {
    let lower = error_msg.to_lowercase();
    // Must look like an output-cap error, not a prompt-length error.
    let is_output_cap = lower.contains("max_tokens")
        && (lower.contains("available_tokens") || lower.contains("available tokens"));
    if !is_output_cap {
        return None;
    }
    for re in output_tokens_regexes() {
        if let Some(caps) = re.captures(error_msg) {
            if let Some(m) = caps.get(1) {
                if let Ok(n) = m.as_str().parse::<i64>() {
                    if n >= 1 {
                        return Some(n);
                    }
                }
            }
        }
    }
    None
}

// ── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // ── strip_provider_prefix ──────────────────────────────────────────
    #[test] fn test_strip_known_prefix() { assert_eq!(strip_provider_prefix_rs("local:my-model"), "my-model"); }
    #[test] fn test_strip_ollama_tag_unchanged() { assert_eq!(strip_provider_prefix_rs("qwen3.5:27b"), "qwen3.5:27b"); }
    #[test] fn test_strip_deepseek_latest_unchanged() { assert_eq!(strip_provider_prefix_rs("deepseek:latest"), "deepseek:latest"); }
    #[test] fn test_strip_no_colon() { assert_eq!(strip_provider_prefix_rs("claude-opus-4"), "claude-opus-4"); }
    #[test] fn test_strip_http_unchanged() { assert_eq!(strip_provider_prefix_rs("http://localhost:11434"), "http://localhost:11434"); }
    #[test] fn test_strip_openai_codex() { assert_eq!(strip_provider_prefix_rs("openai-codex:gpt-4o"), "gpt-4o"); }
    #[test] fn test_strip_aliyun() { assert_eq!(strip_provider_prefix_rs("aliyun:qwen-72b"), "qwen-72b"); }

    // ── model_name_suggests_kimi ───────────────────────────────────────
    #[test] fn test_kimi_starts() { assert!(model_name_suggests_kimi_rs("kimi-k2.6")); }
    #[test] fn test_kimi_moonshot() { assert!(model_name_suggests_kimi_rs("moonshotai/Kimi-K2.6")); }
    #[test] fn test_kimi_false() { assert!(!model_name_suggests_kimi_rs("claude-opus-4")); }

    // ── model_id_matches ──────────────────────────────────────────────
    #[test] fn test_exact_match() { assert!(model_id_matches_rs("nvidia-nemotron-super-49b-v1", "nvidia-nemotron-super-49b-v1")); }
    #[test] fn test_slug_match() { assert!(model_id_matches_rs("nvidia/nvidia-nemotron-super-49b-v1", "nvidia-nemotron-super-49b-v1")); }
    #[test] fn test_no_match() { assert!(!model_id_matches_rs("other-model", "my-model")); }
    #[test] fn test_slash_no_match() { assert!(!model_id_matches_rs("publisher/other", "my-model")); }

    // ── normalize_model_version ────────────────────────────────────────
    #[test] fn test_version_dots_to_dashes() { assert_eq!(normalize_model_version_rs("claude-opus-4.6"), "claude-opus-4-6"); }
    #[test] fn test_version_no_dots() { assert_eq!(normalize_model_version_rs("claude-opus-4"), "claude-opus-4"); }

    // ── get_next_probe_tier ────────────────────────────────────────────
    #[test] fn test_probe_256k() { assert_eq!(get_next_probe_tier_rs(512_000), Some(256_000)); }
    #[test] fn test_probe_32k() { assert_eq!(get_next_probe_tier_rs(33_000), Some(32_000)); }
    #[test] fn test_probe_bottom() { assert_eq!(get_next_probe_tier_rs(8_000), None); }
    #[test] fn test_probe_below_min() { assert_eq!(get_next_probe_tier_rs(4_000), None); }

    // ── estimate_tokens_rough ──────────────────────────────────────────
    #[test] fn test_estimate_empty() { assert_eq!(estimate_tokens_rough_rs(""), 0); }
    #[test] fn test_estimate_short() { assert_eq!(estimate_tokens_rough_rs("hi"), 1); }
    #[test] fn test_estimate_medium() { assert_eq!(estimate_tokens_rough_rs("hello"), 2); }
    #[test] fn test_estimate_long() { assert_eq!(estimate_tokens_rough_rs("this is a longer text for testing"), 9); }
    #[test] fn test_estimate_unicode() { assert_eq!(estimate_tokens_rough_rs("你好世界"), 1); } // 4 chars → (4+3)/4 = 1

    // ── grok_supports_reasoning_effort ─────────────────────────────────
    #[test] fn test_grok_43() { assert!(grok_supports_reasoning_effort_rs("grok-4.3")); }
    #[test] fn test_grok_420_multi() { assert!(grok_supports_reasoning_effort_rs("grok-4.20-multi-agent")); }
    #[test] fn test_grok_3_mini() { assert!(grok_supports_reasoning_effort_rs("grok-3-mini")); }
    #[test] fn test_grok_xai_prefix() { assert!(grok_supports_reasoning_effort_rs("xai/grok-4.3")); }
    #[test] fn test_grok_openrouter_prefix() { assert!(grok_supports_reasoning_effort_rs("openrouter/x-ai/grok-4.3")); }
    #[test] fn test_grok_3() { assert!(!grok_supports_reasoning_effort_rs("grok-3")); }
    #[test] fn test_grok_1() { assert!(!grok_supports_reasoning_effort_rs("grok-1")); }
    #[test] fn test_grok_2() { assert!(!grok_supports_reasoning_effort_rs("grok-2")); }
    #[test] fn test_grok_not_grok() { assert!(!grok_supports_reasoning_effort_rs("claude-opus-4")); }
    #[test] fn test_grok_empty() { assert!(!grok_supports_reasoning_effort_rs("")); }

    // ── context limit parsing ──────────────────────────────────────────
    #[test] fn test_parse_context_limit_standard() {
        assert_eq!(parse_context_limit_from_error_rs("maximum context length is 32768 tokens"), Some(32768));
    }
    #[test] fn test_parse_context_limit_max_is() {
        assert_eq!(parse_context_limit_from_error_rs("maximum is 131072"), Some(131_072));
    }
    #[test] fn test_parse_context_limit_size() {
        assert_eq!(parse_context_limit_from_error_rs("Maximum context size 65536 exceeded"), Some(65536));
    }
    #[test] fn test_parse_context_limit_window_of() {
        assert_eq!(parse_context_limit_from_error_rs("Error: context window of 4096 tokens exceeded"), Some(4096));
    }
    #[test] fn test_parse_context_limit_anthropic_gt() {
        assert_eq!(parse_context_limit_from_error_rs("prompt is too long: 250000 tokens > 200000 maximum"), Some(200_000));
    }
    #[test] fn test_parse_context_limit_none() {
        assert_eq!(parse_context_limit_from_error_rs("something else"), None);
    }
    #[test] fn test_parse_context_limit_out_of_range() {
        assert_eq!(parse_context_limit_from_error_rs("maximum context length is 99999999999"), None);
    }
    #[test] fn test_parse_context_limit_too_small() {
        assert_eq!(parse_context_limit_from_error_rs("context length is 42 tokens"), None);
    }

    // ── output tokens parsing ──────────────────────────────────────────
    #[test] fn test_parse_output_limit_anthropic() {
        let msg = "max_tokens: 32768 > context_window: 200000 - input_tokens: 190000 = available_tokens: 10000";
        assert_eq!(parse_available_output_tokens_from_error_rs(msg), Some(10000));
    }
    #[test] fn test_parse_output_limit_underscore() {
        assert_eq!(parse_available_output_tokens_from_error_rs("max_tokens too large, available_tokens: 5000"), Some(5000));
    }
    #[test] fn test_parse_output_limit_space() {
        assert_eq!(parse_available_output_tokens_from_error_rs("max_tokens exceeded, available tokens: 2048"), Some(2048));
    }
    #[test] fn test_parse_output_limit_not_cap() {
        // "max_tokens" present but no "available_tokens" → not an output-cap error
        assert_eq!(parse_available_output_tokens_from_error_rs("max_tokens is set to 4096"), None);
    }
    #[test] fn test_parse_output_limit_none() {
        assert_eq!(parse_available_output_tokens_from_error_rs("no limit here"), None);
    }
}
