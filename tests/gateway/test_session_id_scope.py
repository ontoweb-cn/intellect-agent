"""Tests for gateway.session_context.session_id_scope."""

from __future__ import annotations

import os

import pytest

from gateway.session_context import (
    _SESSION_ID,
    _UNSET,
    get_session_env,
    session_id_scope,
    set_current_session_id,
)


@pytest.fixture(autouse=True)
def _clean_env():
    os.environ.pop("intellect_SESSION_ID", None)
    _SESSION_ID.set(_UNSET)
    yield
    os.environ.pop("intellect_SESSION_ID", None)
    _SESSION_ID.set(_UNSET)


def test_session_id_scope_restores_previous():
    set_current_session_id("original")
    with session_id_scope("temporary"):
        assert get_session_env("intellect_SESSION_ID") == "temporary"
        assert os.environ["intellect_SESSION_ID"] == "temporary"
    assert get_session_env("intellect_SESSION_ID") == "original"
    assert os.environ["intellect_SESSION_ID"] == "original"


def test_session_id_scope_restores_unset():
    with session_id_scope("only"):
        assert get_session_env("intellect_SESSION_ID") == "only"
    assert get_session_env("intellect_SESSION_ID", "fallback") == "fallback"
    assert "intellect_SESSION_ID" not in os.environ
