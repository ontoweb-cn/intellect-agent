"""Tests for config.yaml API key migration to SecretStore (M3)."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from intellect_cli import api_key_secrets as aks


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    return home


class TestPlaintextDetection:
    def test_inline_key_is_plaintext(self):
        assert aks.is_inline_plaintext_api_key("sk-live-secret")

    def test_env_ref_is_not_plaintext(self):
        assert not aks.is_inline_plaintext_api_key("${OPENAI_API_KEY}")

    def test_empty_is_not_plaintext(self):
        assert not aks.is_inline_plaintext_api_key("")


class TestIterPlaintextApiKeys:
    def test_model_api_key(self):
        config = {
            "model": {"provider": "deepseek", "api_key": "sk-model-key"},
        }
        entries = list(aks.iter_plaintext_api_keys(config))
        assert len(entries) == 1
        assert entries[0].config_path == "model.api_key"
        assert entries[0].secret_key == "provider:deepseek:api_key"
        assert entries[0].value == "sk-model-key"

    def test_providers_section(self):
        config = {
            "providers": {"ollama": {"api_key": "ollama-local-key"}},
        }
        entries = list(aks.iter_plaintext_api_keys(config))
        assert len(entries) == 1
        assert entries[0].config_path == "providers.ollama.api_key"

    def test_custom_providers(self):
        config = {
            "custom_providers": [
                {"name": "My Local", "base_url": "http://127.0.0.1:8080/v1", "api_key": "local-key"},
            ],
        }
        entries = list(aks.iter_plaintext_api_keys(config))
        assert len(entries) == 1
        assert entries[0].secret_key == "custom_provider:my-local:api_key"


class TestMigration:
    def test_migrate_removes_plaintext_from_config(self, isolated_home, memory_store):
        config = {"model": {"provider": "deepseek", "api_key": "sk-to-migrate"}}
        result = aks.migrate_api_keys_from_config(config, store=memory_store)

        assert len(result.migrated) == 1
        assert config["model"].get("api_key") is None
        assert memory_store.get_secret("provider:deepseek:api_key") == "sk-to-migrate"

    def test_dry_run_does_not_write(self, isolated_home, memory_store):
        config = {"model": {"provider": "deepseek", "api_key": "sk-dry"}}
        result = aks.migrate_api_keys_from_config(config, dry_run=True, store=memory_store)

        assert len(result.migrated) == 1
        assert config["model"]["api_key"] == "sk-dry"
        assert memory_store.get_secret("provider:deepseek:api_key") is None

    def test_skips_conflicting_existing_secret(self, isolated_home, memory_store):
        memory_store.set_secret("provider:deepseek:api_key", "existing-different")
        config = {"model": {"provider": "deepseek", "api_key": "sk-new"}}
        result = aks.migrate_api_keys_from_config(config, store=memory_store)

        assert result.migrated == []
        assert any("already set" in note for note in result.skipped)
        assert config["model"]["api_key"] == "sk-new"


@pytest.fixture
def memory_store():
    """In-memory SecretStore stand-in (avoids Fernet round-trip in unit tests)."""

    class _MemoryStore:
        def __init__(self):
            self._data: dict[str, str] = {}

        def get_secret(self, key: str) -> str | None:
            return self._data.get(key)

        def set_secret(self, key: str, value: str) -> None:
            self._data[key] = value

        def delete_secret(self, key: str) -> bool:
            return self._data.pop(key, None) is not None

        def list_secrets(self) -> dict[str, str]:
            return {k: "***" for k in self._data}

    return _MemoryStore()


class TestResolution:
    def test_secret_store_preferred_over_config(self, memory_store):
        memory_store.set_secret("provider:deepseek:api_key", "from-store")
        config = {"model": {"provider": "deepseek", "api_key": "from-config"}}

        key, source = aks.resolve_secret_store_provider_key("deepseek", store=memory_store)
        assert key == "from-store"
        assert source.startswith("secret_store:")

        cfg_key, cfg_source = aks.resolve_config_yaml_provider_key(
            "deepseek", config=config
        )
        assert cfg_key == "from-config"
        assert cfg_source == "config:model.api_key"

    def test_resolve_model_inline_api_key_order(self, memory_store):
        memory_store.set_secret("provider:deepseek:api_key", "store-wins")
        model_cfg = {"provider": "deepseek", "api_key": "config-loses"}

        assert (
            aks.resolve_model_inline_api_key(model_cfg, store=memory_store) == "store-wins"
        )

    def test_resolution_order_secret_before_config_yaml(self, memory_store):
        """Document the fallback chain used by auth (env checked separately)."""
        memory_store.set_secret("provider:deepseek:api_key", "store-secret")
        config = {"providers": {"deepseek": {"api_key": "cfg-secret"}}}

        store_key, _ = aks.resolve_secret_store_provider_key("deepseek", store=memory_store)
        assert store_key == "store-secret"

        cfg_key, cfg_source = aks.resolve_config_yaml_provider_key(
            "deepseek", config=config
        )
        assert cfg_key == "cfg-secret"
        assert cfg_source == "config:providers.deepseek.api_key"


class TestSaveConfigWarning:
    def test_warn_on_plaintext_keys(self, isolated_home, monkeypatch):
        config = {"model": {"provider": "deepseek", "api_key": "sk-plain"}}
        stderr = io.StringIO()
        monkeypatch.setattr("sys.stderr", stderr)

        aks.warn_if_plaintext_api_keys_in_config(config)

        out = stderr.getvalue()
        assert "migrate-api-keys" in out
        assert "model.api_key" in out


class TestCliMigrate:
    def test_cmd_migrate_api_keys(self, isolated_home, monkeypatch, memory_store):
        config = {"model": {"provider": "deepseek", "api_key": "sk-cli"}}
        saved = {}

        def _fake_load():
            return config.copy()

        def _fake_save(cfg):
            saved["config"] = cfg

        monkeypatch.setattr("intellect_cli.config.load_config", _fake_load)
        monkeypatch.setattr("intellect_cli.config.save_config", _fake_save)
        monkeypatch.setattr("agent.secret_store.SecretStore", lambda *a, **k: memory_store)

        args = MagicMock(dry_run=False)
        rc = aks.cmd_migrate_api_keys(args)

        assert rc == 0
        assert saved["config"]["model"].get("api_key") is None
        assert memory_store.get_secret("provider:deepseek:api_key") == "sk-cli"
