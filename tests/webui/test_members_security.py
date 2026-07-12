"""push/pop member runtime env + vault/auth fail-closed checks."""

from __future__ import annotations

import os
import sys
from pathlib import Path
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
    for name in list(sys.modules):
        if name == "api" or name.startswith("api."):
            del sys.modules[name]
    import importlib

    import api.members as mod

    return importlib.reload(mod)


def test_push_pop_member_runtime_env_roundtrip(members_api, monkeypatch):
    monkeypatch.setenv("INTELLECT_MEMBER_ID", "before")
    monkeypatch.setenv("WIKI_PATH", "/tmp/wiki-before")
    snap = members_api.push_member_runtime_env(None)
    assert os.environ.get("INTELLECT_MEMBER_ID") == "before"
    monkeypatch.setenv("INTELLECT_MEMBER_ID", "mutated")
    monkeypatch.delenv("WIKI_PATH", raising=False)
    members_api.pop_member_runtime_env(snap)
    assert os.environ.get("INTELLECT_MEMBER_ID") == "before"
    assert os.environ.get("WIKI_PATH") == "/tmp/wiki-before"


def test_vault_access_check_team_denied(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("INTELLECT_WEBUI_DEFAULT_WORKSPACE", str(ws))
    for name in list(sys.modules):
        if name == "api" or name.startswith("api."):
            del sys.modules[name]
    import api.routes as routes

    assert routes._vault_access_check("m1", "global", "x") is True
    assert routes._vault_access_check("m1", "member", "m1") is True
    assert routes._vault_access_check("m1", "member", "other") is False
    assert routes._vault_access_check("m1", "team", "t1") is False
    assert routes._vault_access_check("m1", "project", "p1") is False


def test_auth_login_rejects_member_id(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("INTELLECT_WEBUI_DEFAULT_WORKSPACE", str(ws))
    for name in list(sys.modules):
        if name == "api" or name.startswith("api."):
            del sys.modules[name]
    import api.routes as routes

    handler = MagicMock()
    handler.client_address = ("127.0.0.1", 1)
    parsed = urlparse("/api/auth/login")
    body = {"member_id": "alice", "password": "x"}

    with (
        patch.object(routes, "read_body", return_value=body),
        patch("api.helpers.read_body", return_value=body),
        patch.object(routes, "bad", return_value=True) as bad,
    ):
        assert routes.handle_post(handler, parsed) is True
        assert bad.called
        kwargs = bad.call_args.kwargs
        args = bad.call_args.args
        status = kwargs.get("status")
        if status is None and len(args) >= 3:
            status = args[2]
        assert status == 404
