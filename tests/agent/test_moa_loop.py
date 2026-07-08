"""E2E tests for MoA virtual provider — MoaRunner orchestration (HP-302h)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────

def _fake_call_llm_result(content: str) -> dict:
    """Return a dict matching call_llm's typical return shape."""
    return {"content": content, "text": content}


def _make_mock_call_llm(responses: list[str]):
    """Build a mock call_llm that returns each response in order."""
    idx = 0

    def _call(messages=None, provider=None, model=None, temperature=None,
              task=None, **kwargs):
        nonlocal idx
        if idx < len(responses):
            result = _fake_call_llm_result(responses[idx])
            idx += 1
            return result
        return _fake_call_llm_result("fallback")

    return _call


# ── MoaRunner tests ────────────────────────────────────────────────────────

class TestMoaRunnerBasic:
    """Test MoaRunner orchestration with mocked call_llm."""

    @pytest.fixture
    def preset(self):
        return {
            "references": [
                {"provider": "anthropic", "model": "claude-sonnet-4-6"},
                {"provider": "openai", "model": "gpt-5.4-pro"},
                {"provider": "google", "model": "gemini-2.5-pro"},
                {"provider": "deepseek", "model": "deepseek-v3.2"},
            ],
            "aggregator": {"provider": "anthropic", "model": "claude-opus-4-8"},
            "reference_temperature": 0.6,
            "aggregator_temperature": 0.4,
        }

    def test_runner_parallel_references_all_succeed(self, preset):
        """All 4 reference models succeed + aggregator → correct response."""
        from agent.moa_loop import MoaRunner

        runner = MoaRunner(preset)
        messages = [{"role": "user", "content": "What is 2+2?"}]

        # 4 ref responses + 1 aggregator = 5 calls
        mock_call = _make_mock_call_llm([
            "It is 4 (Anthropic).",
            "The answer is four (OpenAI).",
            "2+2 equals 4 (Google).",
            "Result: 4 (DeepSeek).",
            "All models agree: 2+2 = 4.",  # aggregator
        ])

        async def _run():
            with patch("agent.moa_loop.call_llm", side_effect=mock_call):
                return await runner.run(messages)

        response = asyncio.run(_run())
        content = response.choices[0].message.content
        assert "All models agree" in content
        assert response._moa_api_calls == 5

    def test_runner_handles_partial_failures(self, preset):
        """1 reference fails, 3 succeed → aggregator still runs."""
        from agent.moa_loop import MoaRunner

        runner = MoaRunner(preset)
        messages = [{"role": "user", "content": "test"}]

        call_count = 0

        def _flaky_call(messages=None, provider=None, model=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if provider == "google":
                raise RuntimeError("simulated API error")
            if call_count <= 4:
                return _fake_call_llm_result(f"Response from {provider}")
            return _fake_call_llm_result("Synthesized answer")

        async def _run():
            with patch("agent.moa_loop.call_llm", side_effect=_flaky_call):
                return await runner.run(messages)

        response = asyncio.run(_run())
        content = response.choices[0].message.content
        assert "Synthesized" in content
        # 3 successful refs + 1 aggregator = 4 API calls tracked
        assert response._moa_api_calls == 5  # 4 refs attempted + 1 agg

    def test_runner_all_references_fail_raises(self, preset):
        """All N reference models fail → RuntimeError (below MIN_SUCCESSFUL)."""
        from agent.moa_loop import MoaRunner

        runner = MoaRunner(preset)
        messages = [{"role": "user", "content": "test"}]

        def _all_fail(**kwargs):
            raise RuntimeError("simulated failure")

        async def _run():
            with patch("agent.moa_loop.call_llm", side_effect=_all_fail):
                return await runner.run(messages)

        with pytest.raises(RuntimeError, match="only 0"):
            asyncio.run(_run())

    def test_runner_aggregator_failure_falls_back_to_best_ref(self, preset):
        """Aggregator fails → returns best reference response."""
        from agent.moa_loop import MoaRunner

        runner = MoaRunner(preset)
        messages = [{"role": "user", "content": "test"}]

        call_count = 0

        def _agg_fail(messages=None, provider=None, model=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                return _fake_call_llm_result(f"Ref answer {call_count}")
            raise RuntimeError("aggregator down")

        async def _run():
            with patch("agent.moa_loop.call_llm", side_effect=_agg_fail):
                return await runner.run(messages)

        response = asyncio.run(_run())
        content = response.choices[0].message.content
        assert "Aggregator unavailable" in content
        assert "Ref answer 1" in content

    def test_extract_content_dict_and_str(self):
        """_extract_content handles dict and str results."""
        from agent.moa_loop import _extract_content
        assert _extract_content({"content": "hello"}) == "hello"
        assert _extract_content({"text": "world"}) == "world"
        assert _extract_content("plain string") == "plain string"
        assert _extract_content(None) == ""
        assert _extract_content(42) == ""


class TestMoaPresetSwitching:
    """Test preset loading and model selection."""

    def test_load_default_preset(self):
        from intellect_cli.moa_config import load_preset, list_presets
        presets = list_presets()
        assert "default" in presets
        preset = load_preset("default")
        assert preset is not None
        assert len(preset["references"]) == 4
        assert preset["aggregator"]["model"] == "claude-opus-4-8"

    def test_preset_summary_shows_cost(self):
        from intellect_cli.moa_config import preset_summary
        summary = preset_summary("default")
        assert summary is not None
        assert summary["reference_count"] == 4
        assert "5 LLM calls" in summary["estimated_cost_note"]

    def test_custom_preset_override(self, tmp_path, monkeypatch):
        """User YAML overrides built-in defaults."""
        import yaml
        from intellect_cli.moa_config import _presets_file, load_preset, load_presets

        presets_dir = tmp_path / "moa"
        presets_dir.mkdir()
        pf = presets_dir / "presets.yaml"
        pf.write_text(yaml.dump({
            "custom": {
                "references": [
                    {"provider": "anthropic", "model": "claude-haiku-4-5"},
                ],
                "aggregator": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
            }
        }), encoding="utf-8")

        # Clear lru_cache on moa_config functions before monkeypatching
        from intellect_cli.moa_config import load_presets as _lp
        _lp.cache_clear() if hasattr(_lp, 'cache_clear') else None

        monkeypatch.setattr(
            "intellect_cli.moa_config._presets_file", lambda: pf
        )
        load_presets.cache_clear() if hasattr(load_presets, 'cache_clear') else None

        preset = load_preset("custom")
        assert preset is not None
        assert len(preset["references"]) == 1


class TestMoaToolCallError:
    """MoA with tools should raise a clear error."""

    def test_build_kwargs_with_tools_does_not_crash(self):
        """build_api_kwargs for moa mode should not crash with tools present.
        (Runtime error is raised later, not in build_kwargs.)"""
        # This test verifies the transport handles tools gracefully.
        # The actual tool-call guard is in the agent loop level, not the transport.
        from agent.transports.moa import MoaTransport
        transport = MoaTransport()
        kwargs = transport.build_kwargs(
            model="moa/default",
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "test_tool"}}],
        )
        assert "_moa_preset_name" in kwargs
        assert kwargs["_moa_preset_name"] == "default"
