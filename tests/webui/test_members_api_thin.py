"""Smoke tests for the thin single-user WebUI members API."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))


@pytest.fixture
def members_api(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("INTELLECT_WEBUI_DEFAULT_WORKSPACE", str(ws))
    # config.py caches DEFAULT_WORKSPACE at import — force a clean import path.
    for name in list(sys.modules):
        if name == "api" or name.startswith("api."):
            del sys.modules[name]
    import importlib

    import api.members as mod

    return importlib.reload(mod)


def _parsed(path: str):
    return urlparse(path)


def test_status_returns_enabled_false(members_api):
    handler = MagicMock()
    captured: dict = {}

    def _capture(_handler, payload, status=200):
        captured["payload"] = payload
        captured["status"] = status

    with (
        patch.object(members_api, "agent_membership_available", return_value=True),
        patch.object(members_api, "json_response", side_effect=_capture),
        patch("api.auth.is_auth_enabled", return_value=False),
        patch("agent.membership.is_members_enabled", return_value=False),
        patch("agent.membership.is_teams_enabled", return_value=False),
        patch("agent.membership.is_projects_enabled", return_value=False),
        patch("agent.membership.members_mode", return_value="legacy"),
        patch.object(members_api, "_load_config", return_value={}),
    ):
        assert members_api.handle_get(handler, _parsed("/api/members/status")) is True

    assert captured["status"] == 200
    assert captured["payload"]["enabled"] is False
    assert captured["payload"]["teams_enabled"] is False
    assert captured["payload"]["projects_enabled"] is False
    assert captured["payload"]["actor_member_id"] is None


def test_non_status_members_get_returns_404(members_api):
    handler = MagicMock()
    with (
        patch.object(members_api, "agent_membership_available", return_value=True),
        patch.object(members_api, "bad", return_value=True) as bad,
    ):
        assert members_api.handle_get(handler, _parsed("/api/members")) is True
    assert bad.call_args.kwargs.get("status") == 404


def test_teams_and_projects_routes_404(members_api):
    handler = MagicMock()
    with (
        patch.object(members_api, "agent_membership_available", return_value=True),
        patch.object(members_api, "bad", return_value=True) as bad,
    ):
        assert members_api.handle_get(handler, _parsed("/api/teams")) is True
        assert members_api.handle_post(handler, _parsed("/api/member-projects")) is True
    assert bad.call_count == 2
    for call in bad.call_args_list:
        assert call.kwargs.get("status") == 404


def test_resolve_hooks_are_noops(members_api):
    handler = MagicMock()
    parsed = _parsed("/api/chat")
    assert members_api.resolve_member_id(handler, parsed) is None
    assert members_api.resolve_team_id(handler, parsed) is None
    assert members_api.resolve_project_id(handler, parsed) is None
    assert members_api.check_member_access(handler, parsed) is True
    assert members_api.maybe_redirect_oauth_canonical_host(handler, parsed) is False
    members_api.bind_request_member_context(handler, parsed)
    assert members_api.get_bound_runtime_context() is None
    members_api.clear_request_member_context()


def test_cmd_members_exits_one(capsys):
    from intellect_cli import main as main_mod

    with pytest.raises(SystemExit) as exc:
        main_mod.cmd_members(SimpleNamespace())
    assert exc.value.code == 1
    err = capsys.readouterr().err.lower()
    assert "removed" in err
    assert "v0.5.0" in err
