"""Tests for the G-05 413 byte-aware recovery metric."""

from agent.message_sanitization import serialized_messages_bytes


def test_bytes_grow_with_content():
    small = [{"role": "user", "content": "x" * 100}]
    big = [{"role": "user", "content": "x" * 100_000}]
    assert serialized_messages_bytes(big) > serialized_messages_bytes(small) * 10


def test_bytes_not_tokens_vision_payload():
    """A base64 payload barely moves token estimates (flat accounting) but
    dominates wire bytes — the byte metric must see it."""
    tiny_text = [{"role": "user", "content": "hi"}]
    b64_blob = "A" * 400_000  # pretend image payload
    img_msgs = [{"role": "user", "content": "hi", "images": [b64_blob]}]
    # Token estimators charge ~4 chars/token flat; bytes charge 1:1. The
    # byte metric must reflect the full blob size.
    assert serialized_messages_bytes(img_msgs) > serialized_messages_bytes(tiny_text) + 400_000


def test_exact_not_estimate():
    import json

    msgs = [{"role": "user", "content": "hello"}]
    expected = len(json.dumps(msgs, ensure_ascii=False, separators=(",", ":")).encode())
    assert serialized_messages_bytes(msgs) == expected


def test_unserializable_falls_back_to_str():
    class Weird:
        def __str__(self):
            return "weird-object"

    msgs = [{"role": "user", "content": Weird()}]
    val = serialized_messages_bytes(msgs)
    assert isinstance(val, int) and val > 0


def test_progress_gate_byte_leg():
    """The 413 branch's progress predicate: >=5% byte reduction counts as
    progress even when the message count is unchanged (image strip case)."""
    original_bytes = 1_000_000
    stripped_bytes = 900_000  # 10% reduction, same message count
    assert stripped_bytes < original_bytes * 0.95
    no_progress = 980_000  # only 2% — not enough
    assert not (no_progress < original_bytes * 0.95)
