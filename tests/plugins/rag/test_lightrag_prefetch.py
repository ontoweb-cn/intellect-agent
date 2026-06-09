"""LightRAG prefetch policy tests."""

from __future__ import annotations

from plugins.rag.lightrag.prefetch import should_prefetch


def test_hybrid_triggers_on_question():
    assert should_prefetch("What is the refund policy?", policy="hybrid", min_chars=40)


def test_hybrid_triggers_on_length():
    assert should_prefetch("x" * 40, policy="hybrid", min_chars=40)


def test_hybrid_triggers_on_keyword():
    assert should_prefetch("see README", policy="hybrid", keywords=["README"])


def test_hybrid_skips_short_chat():
    assert not should_prefetch("hi", policy="hybrid", min_chars=40)


def test_off_never_prefetches():
    assert not should_prefetch("anything?", policy="off")
