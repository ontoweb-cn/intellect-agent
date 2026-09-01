"""A3-2 (G-11): /usage per-model breakdown rendering."""

from cli import IntellectCLI


def test_renders_only_for_multi_model_sessions():
    assert IntellectCLI._render_per_model_usage_lines({"only": {}}) == []
    assert IntellectCLI._render_per_model_usage_lines(None) == []


def test_breakdown_lines_sorted_and_complete():
    per_model = {
        "gpt-y": {"input_tokens": 200, "output_tokens": 20, "api_calls": 1},
        "claude-x": {"input_tokens": 100, "output_tokens": 10,
                     "cache_read_tokens": 5, "api_calls": 2},
    }
    lines = IntellectCLI._render_per_model_usage_lines(per_model)
    assert lines[0] == "  Per-model usage (2 models):"
    # sorted by model name
    assert "claude-x" in lines[1] and "gpt-y" in lines[2]
    # totals include cache reads
    assert "115 tokens" in lines[1]  # 100+10+5 cache
    assert "2 calls" in lines[1]
