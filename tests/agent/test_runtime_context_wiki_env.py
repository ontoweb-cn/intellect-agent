from __future__ import annotations

import os

import pytest

from agent.runtime_context import (
    RuntimeContext,
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


def test_inject_wiki_runtime_env_member(tmp_path, monkeypatch: pytest.MonkeyPatch, wiki_env_isolation) -> None:
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    ctx = RuntimeContext(member_id="alice", platform="webui")
    config = {"members": {"enabled": True}}
    old = inject_wiki_runtime_env(ctx, config, actor_role="member")
    assert os.environ["WIKI_SCOPE"] == "member"
    assert os.environ["WIKI_PATH"].endswith("/members/alice/wiki")
    assert os.environ["WIKI_WRITE_MODE"] == "read_write"
    restore_wiki_runtime_env(old)
    assert os.environ.get("WIKI_PATH") is None


def test_inject_global_read_only_for_member(tmp_path, monkeypatch: pytest.MonkeyPatch, wiki_env_isolation) -> None:
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    ctx = RuntimeContext(member_id="alice", platform="webui")
    config = {"members": {"enabled": True}}
    inject_wiki_runtime_env(ctx, config, target_scope="global", actor_role="member")
    assert os.environ["WIKI_SCOPE"] == "global"
    assert os.environ["WIKI_WRITE_MODE"] == "read_only"
