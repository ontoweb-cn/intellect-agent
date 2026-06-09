"""Tests for the OntoWeb-Intellect-3/4 non-agentic warning detector.

Prior to this check, the warning fired on any model whose name contained
``"intellect"`` anywhere (case-insensitive). That false-positived on unrelated
local Modelfiles such as ``intellect-brain:qwen3-14b-ctx16k`` — a tool-capable
Qwen3 wrapper that happens to live under the "intellect" tag namespace.

``is_ontoweb_intellect_non_agentic`` should only match the actual ONTOWEB
Intellect-3 / Intellect-4 chat family.
"""

from __future__ import annotations

import pytest

from intellect_cli.model_switch import (
    _intellect_MODEL_WARNING,
    _check_intellect_model_warning,
    is_ontoweb_intellect_non_agentic,
)


@pytest.mark.parametrize(
    "model_name",
    [
        "ONTOWEB/Intellect-3-Llama-3.1-70B",
        "ONTOWEB/Intellect-3-Llama-3.1-405B",
        "hermes-3",
        "Intellect-3",
        "intellect-4",
        "intellect-4-405b",
        "intellect_4_70b",
        "openrouter/intellect3:70b",
        "openrouter/ontoweb/intellect-4-405b",
        "ONTOWEB/intellect3",
        "hermes-3.1",
    ],
)
def test_matches_real_ontoweb_intellect_chat_models(model_name: str) -> None:
    assert is_ontoweb_intellect_non_agentic(model_name), (
        f"expected {model_name!r} to be flagged as Ontoweb Intellect 3/4"
    )
    assert _check_intellect_model_warning(model_name) == _intellect_MODEL_WARNING


@pytest.mark.parametrize(
    "model_name",
    [
        # Kyle's local Modelfile — qwen3:14b under a custom tag
        "intellect-brain:qwen3-14b-ctx16k",
        "intellect-brain:qwen3-14b-ctx32k",
        "intellect-honcho:qwen3-8b-ctx8k",
        # Plain unrelated models
        "qwen3:14b",
        "qwen3-coder:30b",
        "qwen2.5:14b",
        "claude-opus-4-6",
        "anthropic/claude-sonnet-4.5",
        "gpt-5",
        "openai/gpt-4o",
        "google/gemini-2.5-flash",
        "deepseek-chat",
        # Non-chat Intellect models we don't warn about
        "intellect-llm-2",
        "intellect2-pro",
        "ontoweb-intellect-2-mistral",
        # Edge cases
        "",
        "intellect",  # bare "intellect" isn't the 3/4 family
        "intellect-brain",
        "brain-hermes-3-impostor",  # "3" not preceded by /: boundary
    ],
)
def test_does_not_match_unrelated_models(model_name: str) -> None:
    assert not is_ontoweb_intellect_non_agentic(model_name), (
        f"expected {model_name!r} NOT to be flagged as Ontoweb Intellect 3/4"
    )
    assert _check_intellect_model_warning(model_name) == ""


def test_none_like_inputs_are_safe() -> None:
    assert is_ontoweb_intellect_non_agentic("") is False
    # Defensive: the helper shouldn't crash on None-ish falsy input either.
    assert _check_intellect_model_warning("") == ""
