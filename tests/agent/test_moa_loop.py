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
        # 4 refs attempted (1 failed) + 1 aggregator = 5 API calls tracked
        assert response._moa_api_calls == 5

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

    def test_runner_short_circuits_when_interrupted_before_fanout(self, preset):
        """A pre-existing interrupt aborts before spending N reference calls."""
        from agent.moa_loop import MoaRunner

        runner = MoaRunner(preset)
        messages = [{"role": "user", "content": "test"}]

        async def _run():
            with patch("agent.moa_loop.is_interrupted", return_value=True):
                return await runner.run(messages)

        with pytest.raises(RuntimeError, match="interrupted before"):
            asyncio.run(_run())

    def test_runner_skips_aggregator_when_interrupted_during_fanout(self, preset):
        """Interrupt arriving during the fan-out skips the +1 aggregator call."""
        from agent.moa_loop import MoaRunner

        runner = MoaRunner(preset)
        messages = [{"role": "user", "content": "test"}]

        mock_call = _make_mock_call_llm([
            "Ref answer 1",
            "Ref answer 2",
            "Ref answer 3",
            "Ref answer 4",
            "Aggregator should not run",
        ])

        # False on the pre-fanout check, True on the post-fanout check.
        states = iter([False, True])

        async def _run():
            with patch("agent.moa_loop.is_interrupted", side_effect=lambda: next(states)), \
                 patch("agent.moa_loop.call_llm", side_effect=mock_call):
                return await runner.run(messages)

        response = asyncio.run(_run())
        content = response.choices[0].message.content
        assert "Interrupted" in content
        assert "Ref answer 1" in content
        # 4 reference calls only — the aggregator was skipped.
        assert response._moa_api_calls == 4

    def test_runner_short_circuits_via_agent_flag(self, preset):
        """The thread-independent agent._interrupt_requested flag short-circuits."""
        from agent.moa_loop import MoaRunner

        class _Agent:
            _interrupt_requested = True

        runner = MoaRunner(preset, agent=_Agent())
        messages = [{"role": "user", "content": "test"}]

        async def _run():
            return await runner.run(messages)

        with pytest.raises(RuntimeError, match="interrupted before"):
            asyncio.run(_run())

    def test_runner_skips_aggregator_via_agent_flag_during_fanout(self, preset):
        """Interrupt set on agent._interrupt_requested during the fan-out skips the aggregator."""
        from agent.moa_loop import MoaRunner

        class _Agent:
            _interrupt_requested = False

        agent = _Agent()
        runner = MoaRunner(preset, agent=agent)
        messages = [{"role": "user", "content": "test"}]

        calls = 0

        def _flip_interrupt(messages=None, provider=None, model=None, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                agent._interrupt_requested = True  # interrupt arrives mid-fan-out
            return _fake_call_llm_result(f"Ref {calls}")

        async def _run():
            with patch("agent.moa_loop.call_llm", side_effect=_flip_interrupt):
                return await runner.run(messages)

        response = asyncio.run(_run())
        content = response.choices[0].message.content
        assert "Interrupted" in content
        assert response._moa_api_calls == 4  # 4 refs, no aggregator


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


class TestReferenceMessages:
    """Denoised advisory view (M2) — read-only, text-only, user-ending."""

    def test_drops_system_and_has_no_tool_role(self):
        from agent.moa_loop import _reference_messages
        view = _reference_messages([
            {"role": "system", "content": "big system prompt"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path": "x.py"}'}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "file contents"},
            {"role": "assistant", "content": "done"},
        ])
        assert all(m["role"] in ("user", "assistant") for m in view)
        assert all("tool_calls" not in m for m in view)

    def test_ends_with_user_turn(self):
        from agent.moa_loop import _reference_messages
        view = _reference_messages([
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])
        assert view[-1]["role"] == "user"

    def test_renders_tool_calls_as_text(self):
        from agent.moa_loop import _reference_messages
        view = _reference_messages([
            {"role": "user", "content": "run it"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path": "x.py"}'}},
            ]},
        ])
        assert any(
            "[called tool: read_file" in m["content"]
            for m in view if m["role"] == "assistant"
        )

    def test_folds_tool_results_into_preceding_assistant(self):
        from agent.moa_loop import _reference_messages
        view = _reference_messages([
            {"role": "user", "content": "read it"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "SECRET FILE CONTENT"},
        ])
        assert any("[tool result: SECRET FILE CONTENT" in m["content"] for m in view)
        assert all(m["role"] != "tool" for m in view)

    def test_multi_tool_turn_produces_alternating_roles(self):
        from agent.moa_loop import _reference_messages
        view = _reference_messages([
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "file A"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c2", "type": "function",
                 "function": {"name": "write_file", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c2", "content": "wrote B"},
            {"role": "assistant", "content": "done"},
        ])
        roles = [m["role"] for m in view]
        assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1))

    def test_orphan_tool_result_dropped(self):
        from agent.moa_loop import _reference_messages
        view = _reference_messages([
            {"role": "tool", "tool_call_id": "c1", "content": "orphan result"},
            {"role": "user", "content": "hi"},
        ])
        # The orphan tool result is dropped; the view must start with a user.
        assert view[0]["role"] == "user"
        assert all("[tool result" not in m["content"] for m in view)

    def test_multimodal_content_flattened(self):
        from agent.moa_loop import _content_to_text
        out = _content_to_text([
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {}},
        ])
        assert out == "hello [image]"

    def test_run_single_reference_prepends_system_prompt(self):
        from agent.moa_loop import MoaRunner, _REFERENCE_SYSTEM_PROMPT
        runner = MoaRunner({"references": [], "aggregator": {}})

        captured = {}

        def _capture(messages=None, **kwargs):
            captured["messages"] = messages
            return {"content": "advice"}

        async def _run():
            with patch("agent.moa_loop.call_llm", side_effect=_capture):
                return await runner._run_single_reference(
                    {"provider": "x", "model": "y"},
                    [{"role": "user", "content": "hi"}],
                )

        asyncio.run(_run())
        msgs = captured["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == _REFERENCE_SYSTEM_PROMPT
        assert msgs[1] == {"role": "user", "content": "hi"}


class TestMoaToolCalling:
    """M1 — aggregator tool-call passthrough."""

    _preset = {
        "references": [{"provider": "anthropic", "model": "claude-sonnet-4-6"}],
        "aggregator": {"provider": "anthropic", "model": "claude-opus-4-8"},
    }

    def test_aggregator_receives_tools_and_returns_tool_calls(self):
        from agent.moa_loop import MoaRunner

        runner = MoaRunner(self._preset)
        messages = [{"role": "user", "content": "read x.py"}]
        tool_calls = [{"id": "c1", "type": "function",
                       "function": {"name": "read_file", "arguments": '{"path":"x.py"}'}}]

        class _Msg:
            content = ""

        _Msg.tool_calls = tool_calls

        class _Choice:
            message = _Msg()

        class _Raw:
            choices = [_Choice()]

        captured = {}
        calls = 0

        def _mock(messages=None, provider=None, model=None, tools=None, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:  # the single reference
                return {"content": "advice"}
            captured["tools"] = tools  # aggregator call
            return _Raw()

        async def _run():
            with patch("agent.moa_loop.call_llm", side_effect=_mock):
                return await runner.run(
                    messages,
                    tools=[{"type": "function", "function": {"name": "read_file"}}],
                )

        response = asyncio.run(_run())
        assert captured["tools"] == [{"type": "function", "function": {"name": "read_file"}}]
        assert response.choices[0].message.tool_calls == tool_calls

    def test_extract_tool_calls_dict_and_raw(self):
        from agent.moa_loop import _extract_tool_calls
        assert _extract_tool_calls({"content": "x", "tool_calls": ["a"]}) == ["a"]
        assert _extract_tool_calls({"content": "x"}) is None

        class _Msg:
            tool_calls = ["raw"]

        class _Choice:
            message = _Msg()

        class _Raw:
            choices = [_Choice()]

        assert _extract_tool_calls(_Raw()) == ["raw"]

    def test_transport_normalize_passes_tool_calls(self):
        from agent.transports.moa import MoaTransport

        class _Msg:
            content = ""
            tool_calls = ["tc"]

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]
            _moa_total_ms = 0
            _moa_ref_results = []

        out = MoaTransport().normalize_response(_Resp())
        assert out.tool_calls == ["tc"]
        assert out.finish_reason == "tool_calls"


class TestMoaCostAndTrim:
    """M5 — cost accounting + context trimming + fault markers."""

    def test_extract_usage_dict_and_raw(self):
        from agent.moa_loop import _extract_usage
        assert _extract_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}) == {
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        assert _extract_usage({"content": "x"}) == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        class _Usage:
            prompt_tokens = 20
            completion_tokens = 8
            total_tokens = 28

        class _Raw:
            usage = _Usage()

        assert _extract_usage(_Raw()) == {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}

    def test_trim_drops_oldest_pairs_and_keeps_tail(self):
        from agent.moa_loop import _trim_reference_messages
        big = [
            {"role": "user", "content": "a" * 4000},
            {"role": "assistant", "content": "b" * 4000},
            {"role": "user", "content": "c" * 4000},
            {"role": "assistant", "content": "d" * 4000},
            {"role": "user", "content": "tail"},
        ]
        trimmed = _trim_reference_messages(big, max_tokens=2000)
        assert len(trimmed) < len(big)
        assert trimmed[-1]["role"] == "user"
        assert trimmed[-1]["content"] == "tail"
        roles = [m["role"] for m in trimmed]
        assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1))

    def test_trim_noop_when_fits(self):
        from agent.moa_loop import _trim_reference_messages
        small = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
        assert _trim_reference_messages(small) == small

    def test_aggregator_surfaces_failed_refs(self):
        from agent.moa_loop import MoaRunner
        runner = MoaRunner({"references": [], "aggregator": {}})
        msgs = runner._build_aggregator_messages(
            [{"role": "user", "content": "q"}],
            [
                {"provider": "anthropic", "model": "claude", "success": True, "content": "ok"},
                {"provider": "openai", "model": "gpt", "success": False, "failed_label": "openai/gpt"},
            ],
        )
        guidance = msgs[-1]["content"]
        assert "openai/gpt" in guidance
        assert "unavailable" in guidance

    def test_aggregator_includes_conversation_and_guidance_at_end(self):
        from agent.moa_loop import MoaRunner
        runner = MoaRunner({"references": [], "aggregator": {}})
        msgs = runner._build_aggregator_messages(
            [
                {"role": "system", "content": "MAIN SYSTEM PROMPT"},
                {"role": "user", "content": "read x.py"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                ]},
                {"role": "tool", "tool_call_id": "c1", "content": "FILE CONTENT"},
            ],
            [{"provider": "anthropic", "model": "claude", "success": True, "content": "advice"}],
        )
        # Main system prompt is dropped; the aggregator has its own.
        assert "MAIN SYSTEM PROMPT" not in msgs[0]["content"]
        # The tool result is preserved in the conversation.
        assert any("FILE CONTENT" in m.get("content", "") for m in msgs)
        # Guidance is appended at the end.
        assert "advice" in msgs[-1]["content"]


class TestMoaCadence:
    """M4 — user_turn fan-out caching."""

    _preset = {
        "references": [{"provider": "anthropic", "model": "claude-sonnet-4-6"}],
        "aggregator": {"provider": "anthropic", "model": "claude-opus-4-8"},
    }

    def test_fanout_reused_across_tool_iterations(self):
        from agent.moa_loop import MoaRunner

        class _Agent:
            pass

        agent = _Agent()
        messages = [{"role": "user", "content": "do it"}]

        call_count = 0

        def _mock(messages=None, provider=None, model=None, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"content": "answer"}

        async def _run():
            with patch("agent.moa_loop.call_llm", side_effect=_mock):
                return await MoaRunner(self._preset, agent=agent).run(messages)

        r1 = asyncio.run(_run())
        assert call_count == 2  # 1 reference + 1 aggregator

        r2 = asyncio.run(_run())
        assert call_count == 3  # +1 aggregator only (fan-out reused)
        assert r2.choices[0].message.content == r1.choices[0].message.content

    def test_turn_signature_stable(self):
        from agent.moa_loop import _turn_signature
        m1 = [{"role": "user", "content": "hello"}]
        m2 = [{"role": "user", "content": "hello"}]
        m3 = [{"role": "user", "content": "world"}]
        assert _turn_signature(m1) == _turn_signature(m2)
        assert _turn_signature(m1) != _turn_signature(m3)

    def test_turn_signature_ignores_tool_results(self):
        from agent.moa_loop import _turn_signature
        base = [{"role": "user", "content": "do it"}]
        with_tool = [
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
        ]
        # Signature is stable across tool iterations (tool results excluded).
        assert _turn_signature(base) == _turn_signature(with_tool)
