"""W11 sweep: auth carve-outs keep status; wiki contributions unmounted."""

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
def webui_api(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("INTELLECT_WEBUI_DEFAULT_WORKSPACE", str(ws))
    for name in list(sys.modules):
        if name == "api" or name.startswith("api."):
            del sys.modules[name]
    import importlib

    import api.auth as auth
    import api.routes as routes

    return SimpleNamespace(auth=importlib.reload(auth), routes=importlib.reload(routes))


def test_public_paths_keep_status_drop_member_auth_carveouts(webui_api):
    paths = webui_api.auth.PUBLIC_PATHS
    assert "/api/members/status" in paths
    for path in (
        "/api/members/oauth/providers",
        "/api/members/register",
        "/api/members/register/check",
        "/api/members/register/pending",
        "/api/members/register/local",
        "/api/members/login",
    ):
        assert path not in paths


def test_check_auth_requires_login_for_member_oauth_when_auth_on(webui_api):
    auth = webui_api.auth
    handler = MagicMock()
    handler.headers = {}
    handler.client_address = ("127.0.0.1", 1)
    handler.wfile = MagicMock()
    captured = {}

    def _send_response(code):
        captured["status"] = code

    handler.send_response = _send_response
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()

    with (
        patch.object(auth, "is_auth_enabled", return_value=True),
        patch.object(auth, "_multi_user_members_enabled", return_value=False),
        patch.object(auth, "parse_cookie", return_value=None),
    ):
        ok = auth.check_auth(handler, urlparse("/api/members/oauth/providers"))
    assert ok is False
    assert captured.get("status") == 401


def test_check_auth_allows_members_status_when_auth_on(webui_api):
    auth = webui_api.auth
    handler = MagicMock()
    with patch.object(auth, "is_auth_enabled", return_value=True):
        assert auth.check_auth(handler, urlparse("/api/members/status")) is True


def test_wiki_contributions_unmounted(webui_api):
    routes = webui_api.routes
    handler = MagicMock()
    assert routes.handle_get(handler, urlparse("/api/wiki/contributions")) is False
    assert routes.handle_get(handler, urlparse("/api/wiki/contributions/abc")) is False
    with patch.object(routes, "_check_csrf", return_value=True), patch.object(
        routes, "read_body", return_value={}
    ):
        assert routes.handle_post(handler, urlparse("/api/wiki/contributions")) is False
        assert routes.handle_post(handler, urlparse("/api/wiki/contributions/abc/review")) is False


def test_members_flag_from_config_ignores_raw_yaml(webui_api, tmp_path, monkeypatch):
    auth = webui_api.auth
    cfg = tmp_path / "config.yaml"
    cfg.write_text("members:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    with patch.object(auth, "_multi_user_members_enabled", return_value=False):
        assert auth._members_flag_from_config() is False
