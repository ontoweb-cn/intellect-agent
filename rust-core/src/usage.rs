//! Token usage normalization — port of `agent/usage_pricing.py:normalize_usage`.
//!
//! Normalizes 3 API response shapes (Anthropic, Codex, OpenAI/generic)
//! into canonical token buckets.  Pure computation — no I/O, no side effects.

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
