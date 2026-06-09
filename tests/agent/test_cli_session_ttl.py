"""CLI .cli-session.json TTL enforcement."""

from __future__ import annotations

import json
import time

import pytest

pytestmark = pytest.mark.no_isolate

from agent.runtime_context import default_cli_session_path, load_cli_session


@pytest.fixture
def members_home(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    return home


class TestCliSessionTTL:
    def test_valid_session_without_expires_at(self, members_home):
        path = default_cli_session_path()
        path.write_text(json.dumps({"member_id": "alice", "login_name": "alice"}))
        data = load_cli_session(path)
        assert data is not None
        assert data["member_id"] == "alice"

    def test_expired_session_removed(self, members_home):
        path = default_cli_session_path()
        path.write_text(
            json.dumps({
                "member_id": "bob",
                "login_name": "bob",
                "expires_at": time.time() - 10,
            })
        )
        assert load_cli_session(path) is None
        assert not path.exists()

    def test_fresh_session_with_expires_at(self, members_home):
        path = default_cli_session_path()
        path.write_text(
            json.dumps({
                "member_id": "carol",
                "expires_at": time.time() + 3600,
            })
        )
        data = load_cli_session(path)
        assert data is not None
        assert data["member_id"] == "carol"
