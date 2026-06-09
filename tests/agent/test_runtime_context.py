"""Tests for RuntimeContext (single-user mode)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent.runtime_context import (
    RuntimeContext,
    assemble_soul,
    build_env_snapshot,
    clear_cli_session,
    load_cli_session,
    resolve_terminal_cwd,
)


class TestRuntimeContext:
    def test_defaults(self):
        ctx = RuntimeContext()
        assert ctx.platform == ""
        assert ctx.session_key == ""
        assert ctx.terminal_cwd is None
        assert ctx.env_snapshot is None

    def test_frozen(self):
        ctx = RuntimeContext(platform="cli")
        assert ctx.platform == "cli"
        with pytest.raises(Exception):  # frozen dataclass
            ctx.platform = "other"  # type: ignore[misc]

    def test_full(self):
        ctx = RuntimeContext(
            platform="telegram",
            session_key="test-key",
            terminal_cwd="/tmp/ws",
            env_snapshot={"FOO": "bar"},
        )
        assert ctx.platform == "telegram"
        assert ctx.session_key == "test-key"
        assert ctx.terminal_cwd == "/tmp/ws"
        assert ctx.env_snapshot == {"FOO": "bar"}


class TestAssembleSoul:
    def test_returns_empty(self):
        """Single-user mode: no member/team/project SOUL layers."""
        assert assemble_soul() == []
        assert assemble_soul(RuntimeContext()) == []


class TestResolveTerminalCwd:
    def test_returns_profile_workspace(self, tmp_path, monkeypatch):
        from intellect_constants import get_intellect_home

        monkeypatch.setattr(
            "intellect_constants.get_intellect_home", lambda: tmp_path
        )
        result = resolve_terminal_cwd()
        assert result.endswith("/workspace")
        assert Path(result).exists()


class TestBuildEnvSnapshot:
    def test_includes_process_env(self):
        import os

        os.environ["TEST_RC_VAR"] = "rc_value"
        try:
            snap = build_env_snapshot()
            assert "TEST_RC_VAR" in snap
            assert snap["TEST_RC_VAR"] == "rc_value"
        finally:
            del os.environ["TEST_RC_VAR"]

    def test_includes_profile_dotenv(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "intellect_constants.get_intellect_home", lambda: tmp_path
        )
        (tmp_path / ".env").write_text("PROFILE_KEY=profile_value\n")
        snap = build_env_snapshot()
        assert snap.get("PROFILE_KEY") == "profile_value"


class TestCliSession:
    def test_load_missing(self):
        assert load_cli_session(Path("/nonexistent/session.json")) is None

    def test_load_expired(self, tmp_path):
        import json, time

        path = tmp_path / ".cli-session.json"
        path.write_text(json.dumps({
            "member_id": "test",
            "expires_at": time.time() - 3600,  # expired
        }))
        assert load_cli_session(path) is None

    def test_clear(self, tmp_path):
        import json

        path = tmp_path / ".cli-session.json"
        path.write_text(json.dumps({"member_id": "test"}))
        assert path.exists()
        clear_cli_session(path)
        assert not path.exists()
