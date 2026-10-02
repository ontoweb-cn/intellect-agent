"""Stale pytest temp workspaces must not brick chat or leak into real settings."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))


def _reload_config(monkeypatch, state_dir: Path):
    monkeypatch.setenv("INTELLECT_WEBUI_STATE_DIR", str(state_dir))
    monkeypatch.delenv("INTELLECT_WEBUI_DEFAULT_WORKSPACE", raising=False)
    for name in list(sys.modules):
        if name == "api" or name.startswith("api."):
            del sys.modules[name]
    import api.config as config

    return importlib.reload(config)


def test_missing_temp_workspace_is_not_recreated(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    config = _reload_config(monkeypatch, state)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(config, "HOME", home)
    monkeypatch.setattr(config, "STATE_DIR", state)

    missing = tmp_path / "gone" / "ws"
    chosen = config.resolve_default_workspace(missing)

    assert not missing.exists()
    assert chosen.is_dir()
    assert chosen.resolve() != missing.resolve()


def test_env_workspace_does_not_overwrite_saved_default(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    real_ws = (tmp_path / "real").resolve()
    real_ws.mkdir()
    ephemeral = (tmp_path / "ephemeral-ws").resolve()
    ephemeral.mkdir()
    settings_path = state / "settings.json"
    settings_path.write_text(
        json.dumps({"default_workspace": str(real_ws), "onboarding_completed": True}),
        encoding="utf-8",
    )
    monkeypatch.setenv("INTELLECT_WEBUI_DEFAULT_WORKSPACE", str(ephemeral))
    config = _reload_config(monkeypatch, state)
    # _reload_config clears the env override; set it again and reload once more
    # so startup sees both the saved settings file and the ephemeral env path.
    monkeypatch.setenv("INTELLECT_WEBUI_DEFAULT_WORKSPACE", str(ephemeral))
    config = importlib.reload(config)

    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    assert Path(saved["default_workspace"]).resolve() == real_ws
    assert Path(config.DEFAULT_WORKSPACE).resolve() == ephemeral


def test_state_dir_follows_intellect_home(tmp_path, monkeypatch):
    home = tmp_path / "intellect-home"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    monkeypatch.delenv("INTELLECT_WEBUI_STATE_DIR", raising=False)
    for name in list(sys.modules):
        if name == "api" or name.startswith("api."):
            del sys.modules[name]
    import api.config as config

    assert config.STATE_DIR == (home / "webui").resolve()


def test_session_workspace_falls_back_when_path_is_gone(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    usable = tmp_path / "usable"
    usable.mkdir()
    config = _reload_config(monkeypatch, state)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(config, "HOME", home)
    monkeypatch.setattr(config, "STATE_DIR", state)

    import api.workspace as workspace

    workspace = importlib.reload(workspace)
    monkeypatch.setattr(workspace, "load_workspaces", lambda: [{"path": str(usable), "name": "Home"}])

    missing = tmp_path / "pytest-gone" / "ws"
    resolved = workspace.resolve_session_workspace(missing)

    assert resolved.resolve() == usable.resolve()
    assert not missing.exists()
