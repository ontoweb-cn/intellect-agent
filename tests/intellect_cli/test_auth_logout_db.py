"""CLI logout clears DB tokens (auth-json §9 / item 8)."""

from __future__ import annotations

import json

import pytest


@pytest.mark.no_isolate
def test_clear_provider_auth_deletes_db_token(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    monkeypatch.setenv("INTELLECT_OAUTH_WRITE_AUTH_JSON", "0")
    monkeypatch.setenv("INTELLECT_OAUTH_READ_AUTH_JSON", "0")

    home = tmp_path
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text(json.dumps({"version": 1, "providers": {}}), encoding="utf-8")

    from intellect_state import SessionDB
    from agent.oauth.model_tokens import persist_model_token, try_load_model_token_row

    db = SessionDB(home / "state.db")
    try:
        persist_model_token(
            db,
            "openai-codex",
            access_token="secret",
            refresh_token="r",
            expires_in=3600,
        )
        assert try_load_model_token_row("openai-codex") is not None
    finally:
        db.close()

    from intellect_cli.auth import clear_provider_auth

    assert clear_provider_auth("openai-codex") is True
    assert try_load_model_token_row("openai-codex") is None
