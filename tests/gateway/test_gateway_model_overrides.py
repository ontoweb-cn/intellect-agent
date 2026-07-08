"""Tests for gateway.model_overrides config resolution (HP-102)."""

from unittest.mock import patch

import pytest

from gateway.config_helpers import (
    _apply_model_override_fields,
    _resolve_config_model_override,
    _session_key_model_override_suffix,
)
from gateway.run import GatewayRunner, _bootstrap_gateway_mixins


@pytest.fixture(autouse=True)
def _inject_gateway_mixin_globals():
    _bootstrap_gateway_mixins()


class TestModelOverrideKeyHelpers:
    def test_session_key_suffix_strips_agent_prefix(self):
        assert _session_key_model_override_suffix(
            "agent:main:telegram:dm:12345",
        ) == "telegram:dm:12345"

    def test_resolve_channel_before_platform(self):
        cfg = {
            "gateway": {
                "model_overrides": {
                    "telegram": {"model": "platform-model"},
                    "telegram:dm:12345": {"model": "channel-model"},
                },
            },
        }
        ov = _resolve_config_model_override("agent:main:telegram:dm:12345", cfg)
        assert ov["model"] == "channel-model"

    def test_resolve_platform_fallback(self):
        cfg = {
            "gateway": {
                "model_overrides": {
                    "telegram": {"model": "platform-model"},
                },
            },
        }
        ov = _resolve_config_model_override("agent:main:telegram:dm:999", cfg)
        assert ov["model"] == "platform-model"


class TestGatewayModelOverridePriority:
    @pytest.fixture
    def runner(self):
        r = GatewayRunner.__new__(GatewayRunner)
        r._session_model_overrides = {}
        r._last_resolved_model = {}
        return r

    def test_config_platform_override_applied(self, runner):
        session_key = "agent:main:telegram:dm:12345"
        user_config = {
            "model": {"default": "global-model"},
            "gateway": {
                "model_overrides": {
                    "telegram": {"model": "telegram-model", "provider": "openrouter"},
                },
            },
        }
        with patch("gateway.agent_runner._resolve_runtime_agent_kwargs", return_value={"provider": "anthropic"}):
            model, rt = runner._resolve_session_agent_runtime(
                session_key=session_key,
                user_config=user_config,
            )
        assert model == "telegram-model"
        assert rt["provider"] == "openrouter"

    def test_session_override_beats_config(self, runner):
        session_key = "agent:main:telegram:dm:12345"
        runner._session_model_overrides[session_key] = {
            "model": "session-model",
            "provider": "anthropic",
        }
        user_config = {
            "model": {"default": "global-model"},
            "gateway": {
                "model_overrides": {
                    "telegram": {"model": "telegram-model"},
                },
            },
        }
        with patch("gateway.agent_runner._resolve_runtime_agent_kwargs", return_value={}):
            model, rt = runner._resolve_session_agent_runtime(
                session_key=session_key,
                user_config=user_config,
            )
        assert model == "session-model"
        assert rt["provider"] == "anthropic"

    def test_no_override_unchanged(self, runner):
        user_config = {"model": {"default": "global-model"}}
        with patch("gateway.agent_runner._resolve_runtime_agent_kwargs", return_value={"provider": "anthropic"}):
            model, rt = runner._resolve_session_agent_runtime(
                session_key="agent:main:discord:dm:1",
                user_config=user_config,
            )
        assert model == "global-model"
        assert rt["provider"] == "anthropic"


class TestApplyModelOverrideFields:
    def test_partial_override_preserves_unset_fields(self):
        model, rt = _apply_model_override_fields(
            "base-model",
            {"provider": "anthropic", "api_key": "sk-test"},
            {"model": "new-model"},
        )
        assert model == "new-model"
        assert rt["provider"] == "anthropic"
        assert rt["api_key"] == "sk-test"
