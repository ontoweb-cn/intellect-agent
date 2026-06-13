//! Token usage normalization + accumulation — port of `agent/usage_pricing.py`.
//!
//! Stage 3a: normalize_usage_rs — 3 API shapes into canonical buckets.
//! Stage 3b: TokenAccumulator — atomic per-session token counter.

use std::sync::atomic::{AtomicI64, Ordering};

use pyo3::prelude::*;

/// Normalize raw API usage fields into canonical (input, output, cache_read,
/// cache_write, reasoning) token counts.
///
/// Python handles getattr extraction from the response object; Rust handles
/// the mode-specific arithmetic.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn normalize_usage_rs(
    api_mode: &str,
    provider_name: &str,
    input_tokens: i64,
    output_tokens: i64,
    prompt_tokens: i64,
    completion_tokens: i64,
    cache_read_input_tokens: i64,
    cache_creation_input_tokens: i64,
    cached_tokens_detail: i64,
    cache_write_tokens_detail: i64,
    reasoning_tokens_detail: i64,
) -> (i64, i64, i64, i64, i64) {
    let mode = api_mode.trim().to_lowercase();
    let provider = provider_name.trim().to_lowercase();

    let (input, output, cache_read, cache_write) = if mode == "anthropic_messages" || provider == "anthropic" {
        // Anthropic: input_tokens/output_tokens/cache_read_input_tokens/cache_creation_input_tokens
        (input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens)
    } else if mode == "codex_responses" {
        // Codex: input_tokens includes cache tokens; details.cached_tokens separates them
        let input = std::cmp::max(0, input_tokens - cached_tokens_detail - cache_write_tokens_detail);
        (input, output_tokens, cached_tokens_detail, cache_write_tokens_detail)
    } else {
        // OpenAI / generic: prompt_tokens includes cache tokens; details breaks them out
        let cache_read = if cached_tokens_detail > 0 {
            cached_tokens_detail
        } else {
            cache_read_input_tokens
        };
        let cache_write = if cache_write_tokens_detail > 0 {
            cache_write_tokens_detail
        } else {
            cache_creation_input_tokens
        };
        let input = std::cmp::max(0, prompt_tokens - cache_read - cache_write);
        (input, completion_tokens, cache_read, cache_write)
    };

    (input, output, cache_read, cache_write, reasoning_tokens_detail)
}

// ── Model name normalization ─────────────────────────────────────────────────

/// Normalize a model name for API calls.
///
/// - Strips 'anthropic/' prefix (OpenRouter format)
/// - Converts dots to hyphens in Claude/Anthropic version numbers
///   (claude-opus-4.6 → claude-opus-4-6)
/// - Preserves Bedrock model IDs (anthropic.claude-opus-4-7)
/// - Preserves non-Anthropic model names (gpt-5.4, gemini-2.5)
#[pyfunction]
pub fn normalize_model_name_rs(model: &str, preserve_dots: bool) -> String {
    normalize_model_name_impl(model, preserve_dots)
}

fn normalize_model_name_impl(model: &str, preserve_dots: bool) -> String {
    let lower = model.to_lowercase();

    // Strip 'anthropic/' prefix (OpenRouter format)
    let stripped = if lower.starts_with("anthropic/") {
        &model[10..]
    } else {
        model
    };

    if !preserve_dots {
        // Bedrock model IDs use dots as namespace separators
        // (e.g. "anthropic.claude-opus-4-7", "us.anthropic.claude-*")
        if is_bedrock_model_id(stripped) {
            return stripped.to_string();
        }

        // Only convert dots to hyphens for Anthropic/Claude models
        let stripped_lower = stripped.to_lowercase();
        if stripped_lower.starts_with("claude-") || stripped_lower.starts_with("anthropic/") {
            return stripped.replace('.', "-");
        }
    }

    stripped.to_string()
}

fn is_bedrock_model_id(model: &str) -> bool {
    // Bedrock IDs have at least two dots: "anthropic.claude-*" or "us.anthropic.claude-*"
    let lower = model.to_lowercase();
    if lower.starts_with("anthropic.") {
        return true;
    }
    // Regional prefix: "us.anthropic.", "eu.anthropic.", etc.
    if let Some(pos) = lower.find('.') {
        let rest = &lower[pos + 1..];
        if rest.starts_with("anthropic.") {
            return true;
        }
    }
    false
}

// ── Rust unit tests ─────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn call(
        api_mode: &str,
        provider: &str,
        input_tokens: i64,
        output_tokens: i64,
        prompt_tokens: i64,
        completion_tokens: i64,
        cache_read: i64,
        cache_create: i64,
        cached_detail: i64,
        cache_write_detail: i64,
        reasoning_detail: i64,
    ) -> (i64, i64, i64, i64, i64) {
        normalize_usage_rs(
            api_mode, provider, input_tokens, output_tokens, prompt_tokens,
            completion_tokens, cache_read, cache_create, cached_detail,
            cache_write_detail, reasoning_detail,
        )
    }

    #[test]
    fn test_anthropic_mode() {
        let (input, output, cr, cw, _) = call(
            "anthropic_messages", "anthropic",
            1000, 500, 0, 0,   // input/output
            200, 100,          // cache_read_input, cache_creation_input
            0, 0, 50,          // details fields, reasoning
        );
        assert_eq!(input, 1000);
        assert_eq!(output, 500);
        assert_eq!(cr, 200);
        assert_eq!(cw, 100);
    }

    #[test]
    fn test_anthropic_via_provider_name() {
        // Recognized by provider_name even when api_mode is empty
        let (input, output, cr, cw, _) = call(
            "", "anthropic",
            800, 300, 0, 0,
            50, 25, 0, 0, 0,
        );
        assert_eq!(input, 800);
        assert_eq!(output, 300);
        assert_eq!(cr, 50);
        assert_eq!(cw, 25);
    }

    #[test]
    fn test_codex_mode() {
        // input_tokens=1200 includes 200 cached + 100 cache_creation
        let (input, output, cr, cw, _) = call(
            "codex_responses", "",
            1200, 400, 0, 0,  // input_total=1200, output=400
            0, 0,              // top-level cache fields (unused in codex)
            200, 100, 30,      // cached_detail=200, cache_write_detail=100, reasoning=30
        );
        assert_eq!(input, 900);   // 1200 - 200 - 100
        assert_eq!(output, 400);
        assert_eq!(cr, 200);
        assert_eq!(cw, 100);
    }

    #[test]
    fn test_codex_input_non_negative() {
        // Edge case: cache tokens > input_total → clamp to 0
        let (input, _, _, _, _) = call(
            "codex_responses", "",
            100, 50, 0, 0,
            0, 0,
            200, 100, 0,
        );
        assert_eq!(input, 0);
    }

    #[test]
    fn test_openai_generic_default() {
        // prompt_tokens=1500, includes 300 cached + 50 cache_write
        let (input, output, cr, cw, _) = call(
            "", "",
            0, 500,            // input/output (ignored in default mode)
            1500, 500,          // prompt_tokens=1500, completion_tokens=500
            0, 0,               // top-level fallback fields
            300, 50, 75,        // cached_detail=300, cache_write_detail=50, reasoning=75
        );
        assert_eq!(input, 1150);  // 1500 - 300 - 50
        assert_eq!(output, 500);
        assert_eq!(cr, 300);
        assert_eq!(cw, 50);
    }

    #[test]
    fn test_openai_fallback_to_top_level_fields() {
        // When details fields are 0, fall back to top-level cache fields
        let (input, output, cr, cw, _) = call(
            "", "",
            0, 600,
            2000, 600,
            100, 50,            // cache_read_input_tokens=100, cache_creation=50
            0, 0, 0,            // details fields all zero → use top-level
        );
        assert_eq!(input, 1850);  // 2000 - 100 - 50
        assert_eq!(output, 600);
        assert_eq!(cr, 100);
        assert_eq!(cw, 50);
    }

    #[test]
    fn test_reasoning_tokens_passthrough() {
        let (_, _, _, _, reasoning) = call(
            "", "", 0, 0, 100, 50, 0, 0, 0, 0, 42,
        );
        assert_eq!(reasoning, 42);
    }
}

// ── TokenAccumulator (Stage 3b) ─────────────────────────────────────────────

/// Thread-safe per-session token counter backed by AtomicI64 fields.
/// Replaces the Python `session_input_tokens += ...` pattern.
#[pyclass]
pub struct TokenAccumulator {
    input_tokens: AtomicI64,
    output_tokens: AtomicI64,
    cache_read_tokens: AtomicI64,
    cache_write_tokens: AtomicI64,
    reasoning_tokens: AtomicI64,
    api_calls: AtomicI64,
    estimated_cost_usd: AtomicI64,  // stored as micro-dollars (×1e6)
}

#[pymethods]
impl TokenAccumulator {
    #[new]
    fn new() -> Self {
        TokenAccumulator {
            input_tokens: AtomicI64::new(0),
            output_tokens: AtomicI64::new(0),
            cache_read_tokens: AtomicI64::new(0),
            cache_write_tokens: AtomicI64::new(0),
            reasoning_tokens: AtomicI64::new(0),
            api_calls: AtomicI64::new(0),
            estimated_cost_usd: AtomicI64::new(0),
        }
    }

    /// Add a usage delta. `cost_micro_usd` is cost in millionths of a dollar
    /// (multiply float USD by 1_000_000 and truncate to i64).
    #[pyo3(signature = (input_tokens, output_tokens, cache_read_tokens,
                         cache_write_tokens, reasoning_tokens, api_calls=1,
                         cost_micro_usd=0))]
    fn add(
        &self,
        input_tokens: i64,
        output_tokens: i64,
        cache_read_tokens: i64,
        cache_write_tokens: i64,
        reasoning_tokens: i64,
        api_calls: i64,
        cost_micro_usd: i64,
    ) {
        self.input_tokens.fetch_add(input_tokens, Ordering::Relaxed);
        self.output_tokens.fetch_add(output_tokens, Ordering::Relaxed);
        self.cache_read_tokens.fetch_add(cache_read_tokens, Ordering::Relaxed);
        self.cache_write_tokens.fetch_add(cache_write_tokens, Ordering::Relaxed);
        self.reasoning_tokens.fetch_add(reasoning_tokens, Ordering::Relaxed);
        self.api_calls.fetch_add(api_calls, Ordering::Relaxed);
        if cost_micro_usd != 0 {
            self.estimated_cost_usd.fetch_add(cost_micro_usd, Ordering::Relaxed);
        }
    }

    /// Reset all counters to zero (for session reset).
    fn reset(&self) {
        self.input_tokens.store(0, Ordering::Relaxed);
        self.output_tokens.store(0, Ordering::Relaxed);
        self.cache_read_tokens.store(0, Ordering::Relaxed);
        self.cache_write_tokens.store(0, Ordering::Relaxed);
        self.reasoning_tokens.store(0, Ordering::Relaxed);
        self.api_calls.store(0, Ordering::Relaxed);
        self.estimated_cost_usd.store(0, Ordering::Relaxed);
    }

    // ── Getters ─────────────────────────────────────────────────────────

    #[getter]
    fn input_tokens(&self) -> i64 { self.input_tokens.load(Ordering::Relaxed) }

    #[getter]
    fn output_tokens(&self) -> i64 { self.output_tokens.load(Ordering::Relaxed) }

    #[getter]
    fn cache_read_tokens(&self) -> i64 { self.cache_read_tokens.load(Ordering::Relaxed) }

    #[getter]
    fn cache_write_tokens(&self) -> i64 { self.cache_write_tokens.load(Ordering::Relaxed) }

    #[getter]
    fn reasoning_tokens(&self) -> i64 { self.reasoning_tokens.load(Ordering::Relaxed) }

    #[getter]
    fn api_calls(&self) -> i64 { self.api_calls.load(Ordering::Relaxed) }

    /// Return cost as float USD (convert from micro-dollars).
    #[getter]
    fn estimated_cost_usd(&self) -> f64 {
        self.estimated_cost_usd.load(Ordering::Relaxed) as f64 / 1_000_000.0
    }

    /// Return all counters as a (input, output, cache_read, cache_write,
    /// reasoning, api_calls, cost_usd) tuple.
    fn snapshot(&self) -> (i64, i64, i64, i64, i64, i64, f64) {
        (
            self.input_tokens(),
            self.output_tokens(),
            self.cache_read_tokens(),
            self.cache_write_tokens(),
            self.reasoning_tokens(),
            self.api_calls(),
            self.estimated_cost_usd(),
        )
    }
}

#[cfg(test)]
mod acc_tests {
    use super::*;

    #[test]
    fn test_accumulator_add_and_read() {
        let acc = TokenAccumulator::new();
        acc.add(100, 50, 20, 10, 5, 1, 3141592);  // $3.141592
        assert_eq!(acc.input_tokens(), 100);
        assert_eq!(acc.output_tokens(), 50);
        assert_eq!(acc.cache_read_tokens(), 20);
        assert_eq!(acc.cache_write_tokens(), 10);
        assert_eq!(acc.reasoning_tokens(), 5);
        assert_eq!(acc.api_calls(), 1);
        assert!((acc.estimated_cost_usd() - 3.141592).abs() < 0.000001);
    }

    #[test]
    fn test_accumulator_multiple_adds() {
        let acc = TokenAccumulator::new();
        acc.add(10, 5, 0, 0, 0, 1, 0);
        acc.add(20, 10, 0, 0, 0, 1, 0);
        assert_eq!(acc.input_tokens(), 30);
        assert_eq!(acc.output_tokens(), 15);
        assert_eq!(acc.api_calls(), 2);
    }

    #[test]
    fn test_accumulator_reset() {
        let acc = TokenAccumulator::new();
        acc.add(100, 50, 0, 0, 0, 1, 0);
        acc.reset();
        assert_eq!(acc.input_tokens(), 0);
        assert_eq!(acc.output_tokens(), 0);
        assert_eq!(acc.api_calls(), 0);
    }

    #[test]
    fn test_normalize_model_name_strip_anthropic_prefix() {
        assert_eq!(normalize_model_name_impl("anthropic/claude-opus-4", false), "claude-opus-4");
        assert_eq!(normalize_model_name_impl("ANTHROPIC/claude-sonnet-4", false), "claude-sonnet-4");
    }

    #[test]
    fn test_normalize_model_name_dots_to_hyphens() {
        assert_eq!(normalize_model_name_impl("claude-opus-4.6", false), "claude-opus-4-6");
        assert_eq!(normalize_model_name_impl("claude-sonnet-4.5", false), "claude-sonnet-4-5");
    }

    #[test]
    fn test_normalize_model_name_preserve_dots() {
        assert_eq!(normalize_model_name_impl("claude-opus-4.6", true), "claude-opus-4.6");
        assert_eq!(normalize_model_name_impl("qwen3.5-plus", true), "qwen3.5-plus");
    }

    #[test]
    fn test_normalize_model_name_bedrock() {
        // Bedrock IDs should not have dots converted
        assert_eq!(normalize_model_name_impl("anthropic.claude-opus-4-7", false), "anthropic.claude-opus-4-7");
        assert_eq!(normalize_model_name_impl("us.anthropic.claude-opus-4-7", false), "us.anthropic.claude-opus-4-7");
    }

    #[test]
    fn test_normalize_model_name_non_anthropic() {
        // Non-Anthropic models should keep dots
        assert_eq!(normalize_model_name_impl("gpt-5.4", false), "gpt-5.4");
        assert_eq!(normalize_model_name_impl("gemini-2.5", false), "gemini-2.5");
    }
}
