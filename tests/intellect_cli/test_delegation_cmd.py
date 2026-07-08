"""Tests for /delegations command helpers (HP-203)."""

from intellect_cli.delegation_cmd import format_delegations_list, run_delegations_subcommand


def test_format_empty_list():
    assert "(none)" in format_delegations_list([])


def test_usage_without_subcommand(monkeypatch):
    class _FakeReg:
        def list(self, parent_session_key=None):
            return []

    monkeypatch.setattr("tools.async_delegation.get_registry", lambda: _FakeReg())
    text = run_delegations_subcommand("", session_key="sk1")
    assert "(none)" in text
