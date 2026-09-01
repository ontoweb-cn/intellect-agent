"""Tests for update hardening (G-19 / A3-7)."""

from types import SimpleNamespace

from intellect_cli import main as cli_main


def test_locked_uv_sync_success_uses_frozen_inexact_all_extras(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli_main.subprocess, "run", fake_run)
    assert cli_main._locked_uv_sync("/usr/bin/uv", "/proj", {"A": "1"}) is True
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[:2] == ["/usr/bin/uv", "sync"]
    assert "--frozen" in cmd          # locked to uv.lock
    assert "--inexact" in cmd         # unrelated user extras survive
    assert "--all-extras" in cmd      # parity with the -e .[all] profile


def test_locked_uv_sync_failure_returns_false(monkeypatch):
    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(cli_main.subprocess, "run", fake_run)
    assert cli_main._locked_uv_sync("/usr/bin/uv", "/proj", {}) is False


def test_locked_uv_sync_never_raises(monkeypatch):
    def boom(cmd, **kwargs):
        raise OSError("no uv")

    monkeypatch.setattr(cli_main.subprocess, "run", boom)
    assert cli_main._locked_uv_sync("/usr/bin/uv", "/proj", {}) is False


def test_update_parser_has_yes_flag():
    """G-19: non-interactive updates need --yes/-y on the update parser."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "intellect_cli" / "main.py").read_text()
    assert '"--yes"' in src and '"-y"' in src
    assert "update_parser.add_argument" in src
