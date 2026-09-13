"""profile → agent REST alias coverage for the WebUI.

``/api/agents`` + ``/api/agent/*`` are the canonical endpoints after the
profile → agent rename; ``/api/profiles`` + ``/api/profile/*`` stay mounted as
aliases for existing clients (strategy A: canonical name, long-lived legacy).
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest

from intellect_cli.agents_home import ProfileInfo

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))


def _parsed(path: str):
    return urlparse(path)


@pytest.fixture
def routes_mod():
    import api.routes as routes

    return routes


@contextmanager
def _captured_gets(routes_mod, *, enabled: bool = True):
    """Patch the agent-list dependencies and capture ``j(...)`` payloads."""
    payloads: list[dict] = []
    with (
        patch("api.profiles.list_profiles_api", return_value=[{"name": "coder"}]),
        patch("api.profiles.get_active_profile_name", return_value="coder"),
        patch("api.profiles.is_profile_management_enabled", return_value=enabled),
        patch(
            "api.profiles.get_active_intellect_home",
            return_value=Path("/tmp/.intellect/agents/coder"),
        ),
        patch.object(
            routes_mod, "j", side_effect=lambda h, p, **k: payloads.append(p) or True
        ),
    ):
        yield payloads


def test_agents_list_is_canonical_with_profiles_alias(routes_mod):
    """``GET /api/agents`` emits both keys; ``GET /api/profiles`` still works."""
    handler = MagicMock()

    with _captured_gets(routes_mod) as payloads:
        assert routes_mod.handle_get(handler, _parsed("/api/agents")) is True
    assert payloads[-1]["agents"] == [{"name": "coder"}]
    assert payloads[-1]["profiles"] == [{"name": "coder"}]
    assert payloads[-1]["active"] == "coder"

    with _captured_gets(routes_mod) as payloads:
        assert routes_mod.handle_get(handler, _parsed("/api/profiles")) is True
    assert payloads[-1]["profiles"] == [{"name": "coder"}]


def test_agent_active_route_and_legacy_alias(routes_mod):
    handler = MagicMock()

    for path in ("/api/agent/active", "/api/profile/active"):
        with _captured_gets(routes_mod) as payloads:
            assert routes_mod.handle_get(handler, _parsed(path)) is True
        assert payloads[-1]["name"] == "coder", path
        # The active agent's own home — canonical ``agents/``, not profiles/.
        assert payloads[-1]["path"] == "/tmp/.intellect/agents/coder", path
        assert payloads[-1]["management_enabled"] is True, path


def test_agents_get_real_dispatch_serializes_dual_key_body(routes_mod, tmp_path):
    """Real dispatch through the real serializer and the real ``j``.

    The rest of this module mocks ``list_profiles_api`` and ``routes_mod.j``,
    so it asserts the *payload dict handed to the response helper* and never
    exercises serialization or the mount itself. Here the request goes all the
    way to a JSON body: both spellings must return byte-identical content,
    with the canonical ``agents`` key carrying the rows.
    """
    infos = [
        ProfileInfo(
            name="default",
            path=tmp_path,
            is_default=True,
            gateway_running=False,
            has_env=True,
            skill_count=1,
        ),
        ProfileInfo(
            name="coder",
            path=tmp_path / "agents" / "coder",
            is_default=False,
            gateway_running=True,
            model="anthropic/claude-sonnet-5",
            provider="anthropic",
            has_env=False,
            skill_count=3,
        ),
    ]

    bodies = {}
    for path in ("/api/agents", "/api/profiles"):
        handler = MagicMock()
        handler.wfile = io.BytesIO()
        with (
            # Patch the *inner* lister so the real ``list_profiles_api``
            # mapping runs; ``intellect_cli.profiles`` is a module-identity
            # shim for ``agents_home``, so patch the real module.
            patch("intellect_cli.agents_home.list_profiles", return_value=infos),
            patch("api.profiles.get_active_profile_name", return_value="coder"),
            patch(
                "api.profiles.is_profile_management_enabled", return_value=True
            ),
        ):
            # The real contract is ``False`` (→ 404) for an unmounted path;
            # ``j()`` itself returns ``None``, so ``is True`` below would only
            # ever have been the mocked helper's return value.
            assert routes_mod.handle_get(handler, _parsed(path)) is not False
        assert handler.send_response.call_args[0][0] == 200
        bodies[path] = json.loads(handler.wfile.getvalue().decode("utf-8"))

    canonical, legacy = bodies["/api/agents"], bodies["/api/profiles"]
    assert canonical == legacy
    assert canonical["agents"] == canonical["profiles"]
    assert [a["name"] for a in canonical["agents"]] == ["default", "coder"]
    assert canonical["active"] == "coder"
    assert canonical["management_enabled"] is True

    coder = canonical["agents"][1]
    assert coder["is_active"] is True
    assert coder["is_default"] is False
    assert coder["gateway_running"] is True
    assert coder["model"] == "anthropic/claude-sonnet-5"
    assert coder["provider"] == "anthropic"
    assert coder["skill_count"] == 3
    assert coder["path"] == str(tmp_path / "agents" / "coder")


def test_agent_post_aliases_are_mounted(routes_mod):
    """Every canonical ``/api/agent/*`` write path exists alongside its
    legacy ``/api/profile/*`` spelling (probed via the disabled-management
    403, which is returned before any mutation)."""
    handler = MagicMock()

    with (
        patch("api.profiles.is_profile_management_enabled", return_value=False),
        patch.object(routes_mod, "read_body", return_value={}),
        patch.object(routes_mod, "_check_csrf", return_value=True),
        patch.object(routes_mod, "bad", side_effect=lambda *a, **k: True) as bad,
    ):
        for action in ("switch", "create", "delete"):
            assert routes_mod.handle_post(
                handler, _parsed(f"/api/agent/{action}")
            ) is True
            assert routes_mod.handle_post(
                handler, _parsed(f"/api/profile/{action}")
            ) is True

    assert bad.call_count == 6
    for call in bad.call_args_list:
        assert call.kwargs.get("status") == 403


def test_switch_response_carries_canonical_agents_key(tmp_path):
    """``POST /api/agent/switch`` must return the same dual-key shape as the
    other agent endpoints.

    ``switch_profile`` is the one payload built outside ``routes.py``; it
    returned ``profiles`` alone, so a client that migrated to the canonical
    surface got ``undefined`` from ``data.agents`` on a *successful* switch
    while the legacy key kept working.
    """
    import api.profiles as profiles

    with (
        patch.object(profiles, "_require_profile_management_enabled"),
        patch.object(profiles, "list_profiles_api", return_value=[{"name": "coder"}]),
        patch.object(profiles, "_resolve_named_profile_home", return_value=tmp_path),
    ):
        body = profiles.switch_profile("coder", process_wide=False)

    assert body["agents"] == body["profiles"] == [{"name": "coder"}]
    assert body["active"] == "coder"
