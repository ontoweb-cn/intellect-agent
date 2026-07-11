"""P0-1: Learning REST must not accept client profile override."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))


@pytest.fixture
def learning_api_module():
    import importlib

    import api.learning as mod

    return importlib.reload(mod)


def _nullcontext():
    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return _Ctx()


def _record_home(seen_homes: list[Path]):
    def _ctx(home):
        seen_homes.append(home)
        return _nullcontext()

    return _ctx


def test_learning_handlers_use_active_home_only(learning_api_module, tmp_path, monkeypatch):
    """?profile= must not switch INTELLECT_HOME away from cookie-active profile."""
    active = tmp_path / "active"
    other = tmp_path / "other"
    active.mkdir()
    other.mkdir()

    seen_homes: list[Path] = []

    monkeypatch.setattr("api.profiles.get_active_intellect_home", lambda: active)
    monkeypatch.setattr("api.profiles.cron_profile_context_for_home", _record_home(seen_homes))

    handler = MagicMock()
    parsed = urlparse("/api/learning/graph?profile=other")

    with patch("agent.learning_graph.build_learning_graph", return_value={"nodes": [], "stats": {}}):
        learning_api_module.handle_learning_graph_get(handler, parsed)

    assert seen_homes == [active]
    assert other not in seen_homes


def test_learning_put_ignores_body_profile(learning_api_module, tmp_path, monkeypatch):
    active = tmp_path / "active"
    active.mkdir()
    seen_homes: list[Path] = []

    monkeypatch.setattr("api.profiles.get_active_intellect_home", lambda: active)
    monkeypatch.setattr("api.profiles.cron_profile_context_for_home", _record_home(seen_homes))

    handler = MagicMock()
    body = {"id": "skill:test", "content": "hello", "profile": "other-profile"}

    with patch(
        "agent.learning_mutations.edit_node",
        return_value={"ok": True},
    ):
        learning_api_module.handle_learning_node_put(handler, body)

    assert seen_homes == [active]


def test_learning_delete_ignores_body_profile(learning_api_module, tmp_path, monkeypatch):
    active = tmp_path / "active"
    active.mkdir()
    seen_homes: list[Path] = []

    monkeypatch.setattr("api.profiles.get_active_intellect_home", lambda: active)
    monkeypatch.setattr("api.profiles.cron_profile_context_for_home", _record_home(seen_homes))

    handler = MagicMock()
    body = {"id": "skill:test", "profile": "other-profile"}

    with patch(
        "agent.learning_mutations.delete_node",
        return_value={"ok": True},
    ):
        learning_api_module.handle_learning_node_delete(handler, body)

    assert seen_homes == [active]


def test_learning_node_get_ignores_query_profile(learning_api_module, tmp_path, monkeypatch):
    active = tmp_path / "active"
    active.mkdir()
    seen_homes: list[Path] = []

    monkeypatch.setattr("api.profiles.get_active_intellect_home", lambda: active)
    monkeypatch.setattr("api.profiles.cron_profile_context_for_home", _record_home(seen_homes))

    handler = MagicMock()
    parsed = urlparse("/api/learning/node?id=skill%3Atest&profile=other")

    with patch(
        "agent.learning_mutations.node_detail",
        return_value={"ok": True, "id": "skill:test", "content": "x"},
    ):
        learning_api_module.handle_learning_node_get(handler, parsed)

    assert seen_homes == [active]


def test_learning_frames_ignores_query_profile(learning_api_module, tmp_path, monkeypatch):
    active = tmp_path / "active"
    active.mkdir()
    seen_homes: list[Path] = []

    monkeypatch.setattr("api.profiles.get_active_intellect_home", lambda: active)
    monkeypatch.setattr("api.profiles.cron_profile_context_for_home", _record_home(seen_homes))

    handler = MagicMock()
    parsed = urlparse("/api/learning/frames?profile=other&cols=40&rows=10&frames=2")

    with (
        patch("agent.learning_graph.build_learning_graph", return_value={"nodes": [], "stats": {}}),
        patch("agent.learning_graph_render.render_frames", return_value={"frames": []}) as render_mock,
    ):
        learning_api_module.handle_learning_frames_get(handler, parsed)

    assert seen_homes == [active]
    render_mock.assert_called_once()


def test_learning_module_has_no_client_profile_helpers(learning_api_module):
    import inspect
    import re

    src = inspect.getsource(learning_api_module)
    assert "_profile_from_query" not in src
    assert "get_intellect_home_for_profile" not in src
    # Old client-override helper name (not a substring of _active_profile_context).
    assert re.search(r"\bdef _profile_context\b", src) is None
    assert "_active_profile_context" in src
