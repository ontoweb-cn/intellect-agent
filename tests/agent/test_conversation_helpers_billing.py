"""Regression coverage for billing/entitlement guidance helpers."""

from agent.conversation_helpers import _billing_or_entitlement_message


def test_billing_message_openrouter_includes_credits_hint():
    message = _billing_or_entitlement_message(
        capability="chat",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model="test/model",
    )
    assert "billing" in message.lower()
    assert "openrouter.ai/settings/credits" in message
    assert "/model" in message


def test_billing_message_non_openrouter_still_returns_guidance():
    message = _billing_or_entitlement_message(
        capability="chat",
        provider="custom",
        base_url="https://inference.example.com/v1",
        model="test/model",
    )
    assert "billing" in message.lower()
    assert "openrouter.ai/settings/credits" not in message
