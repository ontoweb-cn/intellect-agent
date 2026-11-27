"""Bounds/DoS regression tests for tool-call argument repair."""

import time

from agent.message_sanitization import (
    _MAX_TOOL_ARGS_REPAIR_CHARS,
    _python_repair_tool_args,
    _repair_tool_call_arguments,
)


def test_repair_rejects_oversized_malformed_payload():
    payload = "{" + "," * (_MAX_TOOL_ARGS_REPAIR_CHARS + 10) + "}"
    assert _repair_tool_call_arguments(payload) == "{}"


def test_repair_preserves_oversized_valid_json():
    payload = '{"value":"' + ("x" * (_MAX_TOOL_ARGS_REPAIR_CHARS + 10)) + '"}'
    repaired = _repair_tool_call_arguments(payload)
    assert '"value"' in repaired


def test_comma_flood_is_linear_and_bounded():
    payload = "{" + "," * 20_000 + "}"
    started = time.monotonic()
    assert _python_repair_tool_args(payload) == "{}"
    assert time.monotonic() - started < 1.0
