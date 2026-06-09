"""active_provider canonical storage in config.yaml (auth-json §2.1 goal 4)."""

import json

import pytest


def test_get_active_provider_reads_config_not_auth_json(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    monkeypatch.delenv("INTELLECT_OAUTH_READ_AUTH_JSON", raising=False)
    monkeypatch.delenv("INTELLECT_OAUTH_WRITE_AUTH_JSON", raising=False)

    (tmp_path / "config.yaml").write_text(
        "auth:\n  active_provider: openai-codex\n",
        encoding="utf-8",
    )
    (tmp_path / "auth.json").write_text(
        json.dumps({"version": 1, "active_provider": "xai-oauth"}),
        encoding="utf-8",
    )
    from agent.oauth.runtime_settings import get_oauth_runtime_settings
    from intellect_cli.auth import get_active_provider

    get_oauth_runtime_settings.cache_clear()
    assert get_active_provider() == "openai-codex"


def test_set_active_provider_writes_config_only_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    monkeypatch.delenv("INTELLECT_OAUTH_WRITE_AUTH_JSON", raising=False)

    from agent.oauth.runtime_settings import get_oauth_runtime_settings
    from intellect_cli.auth import _set_active_provider_id, get_active_provider

    get_oauth_runtime_settings.cache_clear()
    _set_active_provider_id("qwen-oauth")
    assert get_active_provider() == "qwen-oauth"
    assert "active_provider: qwen-oauth" in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert not (tmp_path / "auth.json").exists()
