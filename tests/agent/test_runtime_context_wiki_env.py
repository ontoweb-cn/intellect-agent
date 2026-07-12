"""Wiki runtime env helpers — single-user profile scope."""

from __future__ import annotations

import os

import pytest

from agent.runtime_context import (
    inject_wiki_runtime_env,
    restore_wiki_runtime_env,
    snapshot_wiki_runtime_env,
)


@pytest.fixture
def wiki_env_isolation(monkeypatch: pytest.MonkeyPatch):
    snap = snapshot_wiki_runtime_env()
    for key in snap:
        monkeypatch.delenv(key, raising=False)
    yield
    restore_wiki_runtime_env(snap)


def test_inject_wiki_runtime_env_sets_path(tmp_path, monkeypatch, wiki_env_isolation):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    old = inject_wiki_runtime_env({})
    assert "WIKI_PATH" in os.environ
    assert os.environ["WIKI_SCOPE"] == "global"
    assert os.environ["WIKI_WRITE_MODE"] == "read_write"
    restore_wiki_runtime_env(old)
    assert os.environ.get("WIKI_PATH") is None


def test_inject_global_target_scope(tmp_path, monkeypatch, wiki_env_isolation):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    inject_wiki_runtime_env({}, target_scope="global")
    assert os.environ["WIKI_SCOPE"] == "global"
    assert os.environ.get("WIKI_TARGET_SCOPE") == "global"


def test_snapshot_restore_roundtrip(monkeypatch, wiki_env_isolation):
    monkeypatch.setenv("WIKI_PATH", "/tmp/wiki-test")
    snap = snapshot_wiki_runtime_env()
    monkeypatch.delenv("WIKI_PATH", raising=False)
    assert os.environ.get("WIKI_PATH") is None
    restore_wiki_runtime_env(snap)
    assert os.environ.get("WIKI_PATH") == "/tmp/wiki-test"
