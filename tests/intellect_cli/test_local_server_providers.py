"""Tests for first-class local server providers: vLLM, Ollama (local), GPUStack.

Covers alias normalization, PROVIDER_REGISTRY wiring, keyless placeholder
credentials, model listing dispatch, provider_model_ids live probing,
/model validation, and picker curated injection. All network access is
mocked — these tests must never hit a real server.
"""

from unittest.mock import patch

import pytest

from intellect_cli import providers as providers_mod
from intellect_cli.auth import (
    PROVIDER_REGISTRY,
    LOCAL_SERVER_NOAUTH_PLACEHOLDERS,
    resolve_api_key_provider_credentials,
    resolve_provider,
)
from intellect_cli.models import (
    _LOCAL_SERVER_PROVIDERS,
    fetch_gpustack_models,
    fetch_local_ollama_models,
    fetch_local_server_models,
    fetch_vllm_models,
    normalize_provider,
    provider_model_ids,
    provider_label,
    validate_requested_model,
)


LOCAL_ENV_VARS = (
    "VLLM_API_KEY", "VLLM_BASE_URL",
    "OLLAMA_API_KEY", "OLLAMA_BASE_URL",
    "GPUSTACK_API_KEY", "GPUSTACK_BASE_URL",
    "LM_API_KEY", "LM_BASE_URL",
)


@pytest.fixture
def clean_local_env(monkeypatch):
    """Scrub local-server env vars so resolution sees a keyless default box."""
    for var in LOCAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# -- alias normalization ------------------------------------------------------

class TestProviderAliases:
    def test_models_py_normalize(self):
        assert normalize_provider("vllm") == "vllm"
        assert normalize_provider("ollama") == "ollama"
        assert normalize_provider("gpustack") == "gpustack"
        assert normalize_provider("gpu-stack") == "gpustack"
        assert normalize_provider("gpu_stack") == "gpustack"

    def test_providers_py_normalize(self):
        np = providers_mod.normalize_provider
        assert np("vllm") == "vllm"
        assert np("ollama") == "ollama"
        assert np("gpu-stack") == "gpustack"

    def test_auth_resolve_provider(self):
        assert resolve_provider("vllm") == "vllm"
        assert resolve_provider("ollama") == "ollama"
        assert resolve_provider("gpustack") == "gpustack"
        assert resolve_provider("GPU-Stack") == "gpustack"

    def test_ollama_cloud_untouched(self):
        assert normalize_provider("ollama-cloud") == "ollama-cloud"
        assert resolve_provider("ollama-cloud") == "ollama-cloud"
        assert resolve_provider("ollama_cloud") == "ollama-cloud"

    def test_llamacpp_still_custom(self):
        assert resolve_provider("llamacpp") == "custom"

    def test_labels(self):
        assert provider_label("vllm") == "vLLM"
        assert provider_label("ollama") == "Ollama (Local)"
        assert provider_label("gpustack") == "GPUStack"


# -- registry wiring ----------------------------------------------------------

class TestProviderRegistry:
    def test_entries_exist_with_api_key_auth(self):
        for pid in ("vllm", "ollama", "gpustack"):
            pconfig = PROVIDER_REGISTRY.get(pid)
            assert pconfig is not None, pid
            assert pconfig.auth_type == "api_key"

    def test_default_base_urls(self):
        assert PROVIDER_REGISTRY["vllm"].inference_base_url == "http://127.0.0.1:8000/v1"
        assert PROVIDER_REGISTRY["ollama"].inference_base_url == "http://127.0.0.1:11434/v1"
        # GPUStack serves its OpenAI-compatible API under /v1-openai.
        assert PROVIDER_REGISTRY["gpustack"].inference_base_url.endswith("/v1-openai")

    def test_env_var_declarations(self):
        assert PROVIDER_REGISTRY["vllm"].api_key_env_vars == ("VLLM_API_KEY",)
        assert PROVIDER_REGISTRY["vllm"].base_url_env_var == "VLLM_BASE_URL"
        assert PROVIDER_REGISTRY["ollama"].api_key_env_vars == ("OLLAMA_API_KEY",)
        assert PROVIDER_REGISTRY["ollama"].base_url_env_var == "OLLAMA_BASE_URL"
        assert PROVIDER_REGISTRY["gpustack"].api_key_env_vars == ("GPUSTACK_API_KEY",)
        assert PROVIDER_REGISTRY["gpustack"].base_url_env_var == "GPUSTACK_BASE_URL"

    def test_overlays_pin_local_defaults(self):
        # base_url_override must pin the local endpoint even if models.dev
        # later grows/changes entries for these providers.
        assert providers_mod.intellect_OVERLAYS["vllm"].base_url_override == "http://127.0.0.1:8000/v1"
        assert providers_mod.intellect_OVERLAYS["ollama"].base_url_override == "http://127.0.0.1:11434/v1"
        assert providers_mod.intellect_OVERLAYS["gpustack"].base_url_override == "http://127.0.0.1/v1-openai"


# -- keyless placeholder credentials -----------------------------------------

class TestNoAuthPlaceholder:
    def test_placeholder_substituted_when_no_key(self, clean_local_env):
        for pid in _LOCAL_SERVER_PROVIDERS:
            creds = resolve_api_key_provider_credentials(pid)
            assert creds["api_key"] == LOCAL_SERVER_NOAUTH_PLACEHOLDERS[pid], pid
            assert creds["api_key"], f"{pid} placeholder must be non-empty"

    def test_env_key_wins_over_placeholder(self, clean_local_env, monkeypatch):
        monkeypatch.setenv("GPUSTACK_API_KEY", "real-gs-key")
        creds = resolve_api_key_provider_credentials("gpustack")
        assert creds["api_key"] == "real-gs-key"

    def test_default_base_url_when_no_env(self, clean_local_env):
        creds = resolve_api_key_provider_credentials("ollama")
        assert creds["base_url"] == "http://127.0.0.1:11434/v1"

    def test_base_url_env_override(self, clean_local_env, monkeypatch):
        monkeypatch.setenv("VLLM_BASE_URL", "http://10.0.0.5:8000/v1")
        creds = resolve_api_key_provider_credentials("vllm")
        assert creds["base_url"] == "http://10.0.0.5:8000/v1"

    def test_gpustack_base_url_gets_openai_suffix(self, clean_local_env, monkeypatch):
        """Users enter the server root (with reverse-proxy prefix) — the
        /v1-openai suffix must be appended, or every request 404s against
        GPUStack's management API."""
        monkeypatch.setenv("GPUSTACK_BASE_URL", "https://ai.wust.edu.cn/gpustack")
        creds = resolve_api_key_provider_credentials("gpustack")
        assert creds["base_url"] == "https://ai.wust.edu.cn/gpustack/v1-openai"

    def test_gpustack_suffix_not_duplicated(self, clean_local_env, monkeypatch):
        monkeypatch.setenv("GPUSTACK_BASE_URL", "https://host/gpustack/v1-openai/")
        creds = resolve_api_key_provider_credentials("gpustack")
        assert creds["base_url"] == "https://host/gpustack/v1-openai"

    def test_local_servers_excluded_from_auto_selection(self, clean_local_env, monkeypatch):
        """Env keys must not auto-select a local server (it may be offline)."""
        monkeypatch.setenv("VLLM_API_KEY", "some-key")
        monkeypatch.setenv("OLLAMA_API_KEY", "some-key")
        monkeypatch.setenv("GPUSTACK_API_KEY", "some-key")
        monkeypatch.setenv("GLM_API_KEY", "real-glm-key")
        assert resolve_provider() == "zai"


# -- model listing dispatch ----------------------------------------------------

def _call_arg(call, name, pos):
    """Fetch an argument from a call regardless of positional/keyword form."""
    args = call.args
    if len(args) > pos:
        return args[pos]
    return call.kwargs[name]


class TestFetchLocalServerModels:
    def test_dispatch_to_per_server_fetcher(self):
        with patch("intellect_cli.models.fetch_api_models", return_value=["m1"]) as f:
            assert fetch_vllm_models(api_key="k", base_url="http://x/v1") == ["m1"]
            assert _call_arg(f.call_args, "base_url", 1) == "http://x/v1"
        with patch("intellect_cli.models.fetch_api_models", return_value=["m2"]) as f:
            assert fetch_local_ollama_models(api_key="k", base_url="http://y/v1") == ["m2"]
        with patch("intellect_cli.models.fetch_api_models", return_value=["m3"]) as f:
            assert fetch_gpustack_models(api_key="k", base_url="http://z/v1-openai") == ["m3"]

    def test_unknown_provider_returns_none(self):
        assert fetch_local_server_models("not-a-server") is None

    def test_empty_args_fall_back_to_resolver(self, clean_local_env):
        """Empty api_key/base_url must be filled from the credential resolver
        (env var > default), which also yields the keyless placeholder."""
        with patch(
            "intellect_cli.auth.resolve_api_key_provider_credentials",
            return_value={"api_key": "placeholder", "base_url": "http://127.0.0.1:8000/v1"},
        ) as resolver, patch(
            "intellect_cli.models.fetch_api_models", return_value=["served-model"],
        ) as fetcher:
            models = fetch_local_server_models("vllm")
        assert models == ["served-model"]
        assert resolver.called
        assert _call_arg(fetcher.call_args, "base_url", 1) == "http://127.0.0.1:8000/v1"

    def test_explicit_args_win_over_resolver(self):
        with patch(
            "intellect_cli.auth.resolve_api_key_provider_credentials",
        ) as resolver, patch(
            "intellect_cli.models.fetch_api_models", return_value=["m"],
        ) as fetcher:
            models = fetch_local_server_models(
                "gpustack", api_key="explicit", base_url="http://explicit/v1-openai",
            )
        assert models == ["m"]
        resolver.assert_not_called()
        assert _call_arg(fetcher.call_args, "api_key", 0) == "explicit"

    def test_unreachable_returns_none(self, clean_local_env):
        with patch("intellect_cli.models.fetch_api_models", return_value=None):
            assert fetch_local_server_models("ollama") is None

    def test_gpustack_root_url_retry_with_openai_suffix(self):
        """First probe at the server root fails; retry must target
        <root>/v1-openai — probe_api_models' +/v1 fallback can't reach
        GPUStack's OpenAI-compatible surface."""
        with patch(
            "intellect_cli.models.fetch_api_models", side_effect=[None, ["gs-model"]],
        ) as f:
            models = fetch_gpustack_models(api_key="k", base_url="https://host/gpustack")
        assert models == ["gs-model"]
        assert len(f.call_args_list) == 2
        assert _call_arg(f.call_args_list[0], "base_url", 1) == "https://host/gpustack"
        assert _call_arg(f.call_args_list[1], "base_url", 1) == "https://host/gpustack/v1-openai"

    def test_gpustack_openai_suffix_not_retried(self):
        """URL already ending in /v1-openai: single probe, no retry."""
        with patch(
            "intellect_cli.models.fetch_api_models", side_effect=[None],
        ) as f:
            models = fetch_gpustack_models(
                api_key="k", base_url="https://host/gpustack/v1-openai",
            )
        assert models is None
        assert len(f.call_args_list) == 1


# -- provider_model_ids live branch -------------------------------------------

class TestProviderModelIds:
    def test_live_probe_returned(self, clean_local_env):
        with patch(
            "intellect_cli.models.fetch_local_server_models", return_value=["a", "b"],
        ):
            assert provider_model_ids("vllm") == ["a", "b"]

    def test_unreachable_falls_through_to_empty(self, clean_local_env):
        with patch(
            "intellect_cli.models.fetch_local_server_models", return_value=None,
        ):
            assert provider_model_ids("gpustack") == []


# -- /model validation ---------------------------------------------------------

class TestValidateRequestedModel:
    def _validate(self, model, provider, fetched, **kw):
        with patch(
            "intellect_cli.models.fetch_local_server_models", return_value=fetched,
        ):
            return validate_requested_model(model, provider, **kw)

    def test_accepts_known_model(self, clean_local_env):
        result = self._validate(
            "qwen3-32b", "vllm", ["qwen3-32b", "llama-3-8b"],
        )
        assert result["accepted"] is True
        assert result["recognized"] is True

    def test_rejects_unknown_model(self, clean_local_env):
        result = self._validate("nope", "ollama", ["qwen3:32b"])
        assert result["accepted"] is False
        assert "not found" in result["message"]

    def test_unreachable_message(self, clean_local_env):
        result = self._validate("m", "vllm", None)
        assert result["accepted"] is False
        assert "Could not reach" in result["message"]

    def test_gpustack_unreachable_hints_api_key(self, clean_local_env):
        result = self._validate("m", "gpustack", None)
        assert "GPUSTACK_API_KEY" in result["message"]

    def test_empty_catalog_message(self, clean_local_env):
        result = self._validate("m", "gpustack", [])
        assert result["accepted"] is False
        assert "no models" in result["message"]


# -- picker curated injection ----------------------------------------------------

class TestPickerCuratedInjection:
    def _list_providers(self, monkeypatch, *, fetch_result, current_provider="", env=None):
        monkeypatch.setattr("agent.models_dev.fetch_models_dev", lambda: {})
        monkeypatch.setattr(providers_mod, "intellect_OVERLAYS", {})
        for var in LOCAL_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)

        from intellect_cli.model_switch import list_authenticated_providers
        with patch(
            "intellect_cli.models.fetch_local_server_models",
            side_effect=lambda provider, **kw: dict(fetch_result).get(provider),
        ):
            return list_authenticated_providers(
                current_provider=current_provider,
                current_base_url="",
                current_model="",
            )

    def test_api_key_env_lists_provider_with_live_models(self, monkeypatch):
        """Key env set → provider listed, models come from the live probe."""
        providers = self._list_providers(
            monkeypatch,
            fetch_result={"vllm": ["served/a", "served/b"]},
            env={"VLLM_API_KEY": "k"},
        )
        vllm = next((p for p in providers if p["slug"] == "vllm"), None)
        assert vllm is not None
        assert "served/a" in vllm["models"]

    def test_active_provider_probed_without_key(self, monkeypatch):
        """Active local provider with no key: curated injection still probes
        (mirrors lmstudio), and the fetcher received the provider id."""
        seen_providers: list[str] = []

        monkeypatch.setattr("agent.models_dev.fetch_models_dev", lambda: {})
        monkeypatch.setattr(providers_mod, "intellect_OVERLAYS", {})
        for var in LOCAL_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        from intellect_cli.model_switch import list_authenticated_providers

        def _fake_fetch(provider, **kw):
            seen_providers.append(provider)
            return ["m"] if provider == "ollama" else None

        with patch("intellect_cli.models.fetch_local_server_models", side_effect=_fake_fetch):
            list_authenticated_providers(
                current_provider="ollama",
                current_base_url="",
                current_model="",
            )
        assert "ollama" in seen_providers

    def test_no_signal_no_probe(self, monkeypatch):
        """Without env vars and without being active, no probe runs."""
        seen_providers: list[str] = []

        monkeypatch.setattr("agent.models_dev.fetch_models_dev", lambda: {})
        monkeypatch.setattr(providers_mod, "intellect_OVERLAYS", {})
        for var in LOCAL_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        from intellect_cli.model_switch import list_authenticated_providers

        def _fake_fetch(provider, **kw):
            seen_providers.append(provider)
            return None

        with patch("intellect_cli.models.fetch_local_server_models", side_effect=_fake_fetch):
            list_authenticated_providers(
                current_provider="openrouter",
                current_base_url="",
                current_model="",
            )
        assert not seen_providers
