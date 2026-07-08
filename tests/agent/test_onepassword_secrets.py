"""Tests for 1Password secret source (HP-404)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestOnePassword:
    """Unit tests for onepassword.py — no real op CLI needed."""

    def test_find_op_not_found(self, monkeypatch):
        """find_op returns None when op is not on PATH."""
        import shutil
        monkeypatch.setattr(shutil, "which", lambda x: None)
        from agent.secret_sources.onepassword import find_op
        assert find_op() is None

    def test_check_op_cli_not_installed(self):
        with patch("agent.secret_sources.onepassword.find_op", return_value=None):
            from agent.secret_sources.onepassword import check_op_cli
            assert check_op_cli() is False

    def test_check_op_cli_not_signed_in(self):
        with patch("agent.secret_sources.onepassword.find_op", return_value="/usr/bin/op"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                from agent.secret_sources.onepassword import check_op_cli
                assert check_op_cli() is False

    def test_check_op_cli_signed_in(self):
        with patch("agent.secret_sources.onepassword.find_op", return_value="/usr/bin/op"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                from agent.secret_sources.onepassword import check_op_cli
                assert check_op_cli() is True

    def test_fetch_secrets_not_signed_in(self):
        with patch("agent.secret_sources.onepassword.check_op_cli", return_value=False):
            from agent.secret_sources.onepassword import fetch_onepassword_secrets
            result = fetch_onepassword_secrets()
            assert not result.ok
            assert "not available" in result.errors[0]

    def test_fetch_secrets_no_items(self):
        with patch("agent.secret_sources.onepassword.check_op_cli", return_value=True):
            with patch("agent.secret_sources.onepassword._list_items", return_value=[]):
                from agent.secret_sources.onepassword import fetch_onepassword_secrets
                result = fetch_onepassword_secrets(vault="test")
                assert not result.ok

    def test_fetch_secrets_with_items(self):
        items = [{"title": "OPENAI_API_KEY", "vault": {"name": "intellect"}}]
        detail = {
            "fields": [
                {"label": "credential", "type": "CONCEALED", "value": "sk-test123"},
            ]
        }
        with patch("agent.secret_sources.onepassword.check_op_cli", return_value=True):
            with patch("agent.secret_sources.onepassword._list_items", return_value=items):
                with patch("agent.secret_sources.onepassword._read_item", return_value=detail):
                    from agent.secret_sources.onepassword import fetch_onepassword_secrets
                    result = fetch_onepassword_secrets()
                    assert result.ok
                    assert result.secrets["OPENAI_API_KEY"] == "sk-test123"

    def test_extract_credential_value_prefers_concealed(self):
        from agent.secret_sources.onepassword import _extract_credential_value
        item = {
            "fields": [
                {"label": "credential", "type": "TEXT", "value": "plain"},
                {"label": "credential", "type": "CONCEALED", "value": "secret"},
            ]
        }
        assert _extract_credential_value(item, "credential") == "secret"

    def test_extract_credential_value_fallback(self):
        from agent.secret_sources.onepassword import _extract_credential_value
        item = {
            "fields": [
                {"label": "my credential", "type": "TEXT", "value": "my-key"},
            ]
        }
        assert _extract_credential_value(item, "credential") == "my-key"

    def test_extract_credential_value_none(self):
        from agent.secret_sources.onepassword import _extract_credential_value
        assert _extract_credential_value({}, "credential") is None
        assert _extract_credential_value({"fields": []}, "credential") is None

    def test_fetchresult_ok(self):
        from agent.secret_sources.onepassword import FetchResult
        r = FetchResult()
        assert r.ok is True
        r.errors.append("oops")
        assert r.ok is False
