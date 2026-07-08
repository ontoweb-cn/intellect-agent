"""Tests for gateway.helpers secret redaction in user-facing replies."""

from gateway.helpers import _redact_gateway_user_facing_secrets


def test_redacts_slack_app_level_token():
    token = "xapp-1-" + "A" * 30
    text = f"Socket Mode failed with {token}"
    result = _redact_gateway_user_facing_secrets(text)
    assert token not in result
    assert "[REDACTED]" in result


def test_redacts_slack_bot_token():
    token = "xoxb-" + "0" * 12 + "-" + "a" * 24
    result = _redact_gateway_user_facing_secrets(f"error: {token}")
    assert token not in result
