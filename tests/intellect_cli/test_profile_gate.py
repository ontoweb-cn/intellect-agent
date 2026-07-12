"""Tests for temporary profiles.management_enabled gate."""

from __future__ import annotations

import pytest


def test_profile_management_disabled_by_default():
    from intellect_cli.profile_gate import is_profile_management_enabled

    assert is_profile_management_enabled({"profiles": {}}) is False
    assert is_profile_management_enabled({"profiles": {"management_enabled": False}}) is False


def test_default_config_management_enabled_is_false():
    """G1: DEFAULT_CONFIG + empty user yaml merge must stay disabled."""
    from intellect_cli.config import DEFAULT_CONFIG
    from intellect_cli.profile_gate import is_profile_management_enabled

    assert DEFAULT_CONFIG["profiles"]["management_enabled"] is False
    assert is_profile_management_enabled(DEFAULT_CONFIG) is False


def test_load_config_without_profiles_key_is_disabled(tmp_path, monkeypatch):
    """G1: minimal user config.yaml (no profiles key) → management off."""
    home = tmp_path / ".intellect"
    home.mkdir()
    (home / "config.yaml").write_text("model: test-model\n", encoding="utf-8")
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    from intellect_cli.config import load_config
    from intellect_cli.profile_gate import is_profile_management_enabled

    cfg = load_config()
    assert is_profile_management_enabled(cfg) is False


def test_profile_management_enabled_when_config_true(tmp_path, monkeypatch):
    """G2: explicit true in user yaml enables management via load_config()."""
    home = tmp_path / ".intellect"
    home.mkdir()
    (home / "config.yaml").write_text(
        "profiles:\n  management_enabled: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    from intellect_cli.config import load_config
    from intellect_cli.profile_gate import is_profile_management_enabled

    assert is_profile_management_enabled({"profiles": {"management_enabled": True}}) is True
    assert is_profile_management_enabled(load_config()) is True


def test_cmd_profile_create_blocked_when_disabled(monkeypatch, capsys):
    """G3: CLI create rejected while disabled."""
    from intellect_cli import main as main_mod

    monkeypatch.setattr(
        "intellect_cli.profile_gate.is_profile_management_enabled",
        lambda config=None: False,
    )
    args = type("Args", (), {"profile_action": "create", "profile_name": "x"})()
    with pytest.raises(SystemExit) as exc:
        main_mod.cmd_profile(args)
    assert exc.value.code == 1
    assert "temporarily disabled" in capsys.readouterr().err.lower()
