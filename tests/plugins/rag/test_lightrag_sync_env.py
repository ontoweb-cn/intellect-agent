"""LightRAG sync-server-env and auxiliary.lightrag task."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from intellect_cli.config import DEFAULT_CONFIG


def test_default_config_has_auxiliary_lightrag():
    aux = DEFAULT_CONFIG.get("auxiliary") or {}
    assert "lightrag" in aux
    assert aux["lightrag"].get("provider") == "auto"


def test_build_server_env_openrouter(monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", "/tmp/test-home")
    cfg = {
        "model": {
            "provider": "openrouter",
            "default": "google/gemini-2.5-flash",
        },
    }
    runtime = {
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "",
    }
    with patch("intellect_cli.config.load_config", return_value=cfg), patch(
        "intellect_cli.runtime_provider.resolve_runtime_provider",
        return_value=runtime,
    ):
        from plugins.rag.lightrag.sync_env import build_server_env

        result = build_server_env()
    text = "\n".join(result.lines)
    assert "LLM_BINDING=openai" in text
    assert "LLM_MODEL=google/gemini-2.5-flash" in text
    assert "openrouter.ai" in text
    assert "EMBEDDING_BINDING=openai" in text
    assert "OPENROUTER_API_KEY=" in text


def test_build_server_env_xai_default_host(monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", "/tmp/test-home")
    cfg = {"model": {"provider": "xai", "default": "grok-3"}}
    runtime = {"provider": "xai", "base_url": "", "api_mode": "codex_responses"}
    with patch("intellect_cli.config.load_config", return_value=cfg), patch(
        "intellect_cli.runtime_provider.resolve_runtime_provider",
        return_value=runtime,
    ):
        from plugins.rag.lightrag.sync_env import build_server_env

        result = build_server_env()
    text = "\n".join(result.lines)
    assert "LLM_BINDING_HOST=https://api.x.ai/v1" in text
    assert "XAI_API_KEY=" in text
    assert any("codex_responses" in w for w in result.warnings)


def test_openrouter_substring_in_path_not_matched(monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", "/tmp/test-home")
    cfg = {"model": {"provider": "custom", "default": "gpt-4o-mini"}}
    runtime = {
        "provider": "custom",
        "base_url": "https://corp-gateway.example.com/proxy/openrouter.ai/v1",
        "api_key": "",
    }
    with patch("intellect_cli.config.load_config", return_value=cfg), patch(
        "intellect_cli.runtime_provider.resolve_runtime_provider",
        return_value=runtime,
    ):
        from plugins.rag.lightrag.sync_env import build_server_env

        result = build_server_env()
    text = "\n".join(result.lines)
    assert "OPENROUTER_API_KEY=" not in text
    assert "corp-gateway.example.com" in text


def test_build_server_env_ollama_docker(monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", "/tmp/test-home")
    cfg = {
        "model": {
            "provider": "ollama",
            "default": "mistral-nemo:latest",
            "base_url": "http://127.0.0.1:11434/v1",
        },
    }
    runtime = {
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "ollama",
    }
    with patch("intellect_cli.config.load_config", return_value=cfg), patch(
        "intellect_cli.runtime_provider.resolve_runtime_provider",
        return_value=runtime,
    ):
        from plugins.rag.lightrag.sync_env import build_server_env

        result = build_server_env(for_docker=True)
    text = "\n".join(result.lines)
    assert "LLM_BINDING=ollama" in text
    assert "LLM_BINDING_HOST=http://host.docker.internal:11434" in text
    assert "EMBEDDING_BINDING=ollama" in text
    assert "EMBEDDING_MODEL=bge-m3:latest" in text


def test_vllm_on_11434_not_classified_as_ollama(monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", "/tmp/test-home")
    cfg = {
        "model": {
            "provider": "custom",
            "default": "meta-llama/Llama-3.1-8B",
            "base_url": "http://127.0.0.1:11434/v1",
        },
    }
    runtime = {
        "provider": "custom",
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "sk-test",
    }
    with patch("intellect_cli.config.load_config", return_value=cfg), patch(
        "intellect_cli.runtime_provider.resolve_runtime_provider",
        return_value=runtime,
    ):
        from plugins.rag.lightrag.sync_env import build_server_env

        result = build_server_env()
    text = "\n".join(result.lines)
    assert "LLM_BINDING=openai" in text
    assert "LLM_BINDING=ollama" not in text


def test_normalize_openai_base_preserves_custom_path():
    from plugins.rag.lightrag.sync_env import _normalize_openai_base

    url = "https://proxy.internal/my-llm"
    assert _normalize_openai_base(url) == url


def test_write_server_env_uses_restricted_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / ".intellect"))
    cfg = {"model": {"provider": "openai", "default": "gpt-4o-mini"}}
    runtime = {"provider": "openai", "base_url": "", "api_key": "secret-key"}
    out = tmp_path / "server.env"
    with patch("intellect_cli.config.load_config", return_value=cfg), patch(
        "intellect_cli.runtime_provider.resolve_runtime_provider",
        return_value=runtime,
    ):
        from plugins.rag.lightrag.sync_env import write_server_env

        write_server_env(out)
    mode = stat.S_IMODE(out.stat().st_mode)
    assert mode == 0o600
    assert "OPENAI_API_KEY=" in out.read_text(encoding="utf-8")


def test_find_repo_deploy_env(tmp_path):
    repo = tmp_path / "repo"
    deploy = repo / "deploy" / "lightrag"
    deploy.mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    sync_file = repo / "plugins" / "rag" / "lightrag" / "sync_env.py"
    sync_file.parent.mkdir(parents=True, exist_ok=True)
    sync_file.touch()

    import plugins.rag.lightrag.sync_env as mod

    with patch.object(mod, "__file__", str(sync_file)):
        found = mod._find_repo_deploy_env()
    assert found == deploy / ".env"


def test_default_output_path_profile_when_no_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / ".intellect"))
    with patch("plugins.rag.lightrag.sync_env._find_repo_deploy_env", return_value=None):
        from plugins.rag.lightrag.sync_env import default_output_path

        path, reason = default_output_path()
    assert path == tmp_path / ".intellect" / "lightrag" / "server.env"
    assert "profile-local" in reason


def test_ingest_uses_lightrag_auxiliary_task(monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", "/tmp/x")
    captured: dict = {}

    def fake_call_llm(**kwargs):
        captured.update(kwargs)
        msg = MagicMock()
        msg.content = "summary text"
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    with patch("agent.auxiliary_client.call_llm", side_effect=fake_call_llm):
        from plugins.rag.lightrag.ingest import summarize_text

        out = summarize_text("hello world", max_tokens=64)
    assert out == "summary text"
    assert captured.get("task") == "lightrag"


def test_ingest_empty_choices_returns_empty(caplog):
    caplog.set_level("WARNING")
    resp = MagicMock()
    resp.choices = []

    with patch("agent.auxiliary_client.call_llm", return_value=resp):
        from plugins.rag.lightrag.ingest import summarize_text

        out = summarize_text("hello", max_tokens=32)
    assert out == ""
    assert any("no choices" in rec.message for rec in caplog.records)


def test_lightrag_aux_inherits_compression_when_default(monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", "/tmp/x")
    cfg = {
        "auxiliary": {
            "compression": {
                "provider": "openrouter",
                "model": "google/gemini-2.5-flash",
                "timeout": 120,
            },
            "lightrag": {
                "provider": "auto",
                "model": "",
                "timeout": 60,
            },
        },
    }
    with patch("intellect_cli.config.load_config", return_value=cfg):
        from agent.auxiliary_client import _get_auxiliary_task_config

        resolved = _get_auxiliary_task_config("lightrag")
    assert resolved.get("provider") == "openrouter"
    assert resolved.get("model") == "google/gemini-2.5-flash"
    assert resolved.get("timeout") == 120  # inherits compression when lightrag still default


def test_cli_registers_sync_server_env():
    import argparse
    from plugins.rag.lightrag.cli import register_cli

    parser = argparse.ArgumentParser()
    register_cli(parser)
    args = parser.parse_args(["sync-server-env", "--dry-run", "--docker"])
    assert args.lightrag_command == "sync-server-env"
    assert args.dry_run is True
    assert args.docker is True


def test_aux_tasks_includes_lightrag():
    from intellect_cli.main import _AUX_TASKS

    keys = [k for k, _n, _d in _AUX_TASKS]
    assert "lightrag" in keys
