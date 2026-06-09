"""Tests for temporary profiles.management_enabled gate."""

from __future__ import annotations

import pytest


def test_profile_management_disabled_by_default():
    from intellect_cli.profile_gate import is_profile_management_enabled

    assert is_profile_management_enabled({"profiles": {}}) is False
    assert is_profile_management_enabled({"profiles": {"management_enabled": False}}) is False


def test_profile_management_enabled_when_config_true():
    from intellect_cli.profile_gate import is_profile_management_enabled

    assert is_profile_management_enabled({"profiles": {"management_enabled": True}}) is True


def test_cmd_profile_create_blocked_when_disabled(monkeypatch, capsys):
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
