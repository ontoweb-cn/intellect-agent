"""Shared hermeticity guards for ACP tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_real_auto_title(monkeypatch):
    """Never spawn the real auto-title network thread in ACP tests.

    A thread that outlives the test can touch SessionDB during interpreter
    shutdown (bus error/segfault in CI). Tests that assert title scheduling
    patch this function themselves.
    """
    monkeypatch.setattr("agent.title_generator.maybe_auto_title", lambda *a, **k: None)
