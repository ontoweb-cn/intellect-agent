"""Phase 5c tests for the graphiti memory plugin.

Covers:
  5c.1  LLM endpoint — _build_llm_client (openai / openai_compat / local proxies)
  5c.2  Embedder selection — _build_embedder (local fastembed / openai / unknown)
  5c.3  Config schema — llm_* fields, env-var wiring, secret masking
  5c.4  FastembedEmbedder — model alias resolution, lazy-load contract
  5c.5  End-to-end — client & manager plumbs llm/embedder params through

Live graphiti-core round-trips stay in tests/integration/.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 5c.1 — _build_llm_client
# ---------------------------------------------------------------------------

def test_build_llm_client_openai_no_base_url_returns_none():
    """When provider=openai and base_url is unset, return None so
    graphiti-core uses its default OpenAIClient (needs OPENAI_API_KEY)."""
    from plugins.memory.graphiti.client import _build_llm_client

    assert _build_llm_client(provider="openai") is None


def test_build_llm_client_empty_provider_no_base_url_returns_none():
    from plugins.memory.graphiti.client import _build_llm_client

    assert _build_llm_client(provider="") is None


def test_build_llm_client_openai_with_base_url_returns_openai_client():
    """When base_url is set, always return a configured OpenAIClient
    (even for provider=openai)."""
    from plugins.memory.graphiti.client import _build_llm_client
    from graphiti_core.llm_client.openai_client import OpenAIClient

    c = _build_llm_client(
        provider="openai",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
    )
    assert isinstance(c, OpenAIClient)
    assert getattr(c, "config", None) is not None


def test_build_llm_client_openai_compat_returns_openai_client():
    from plugins.memory.graphiti.client import _build_llm_client
    from graphiti_core.llm_client.openai_client import OpenAIClient

    c = _build_llm_client(
        provider="openai_compat",
        base_url="http://localhost:8080/v1",
        api_key="local",
    )
    assert isinstance(c, OpenAIClient)


def test_build_llm_client_ollama_returns_openai_client():
    from plugins.memory.graphiti.client import _build_llm_client
    from graphiti_core.llm_client.openai_client import OpenAIClient

    c = _build_llm_client(
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key="not-used",
    )
    assert isinstance(c, OpenAIClient)


def test_build_llm_client_vllm_returns_openai_client():
    from plugins.memory.graphiti.client import _build_llm_client
    from graphiti_core.llm_client.openai_client import OpenAIClient

    c = _build_llm_client(
        provider="vllm",
        base_url="http://localhost:8000/v1",
        api_key="placeholder",
    )
    assert isinstance(c, OpenAIClient)


def test_build_llm_client_litellm_returns_openai_client():
    from plugins.memory.graphiti.client import _build_llm_client
    from graphiti_core.llm_client.openai_client import OpenAIClient

    c = _build_llm_client(
        provider="litellm",
        base_url="http://localhost:4000",
    )
    assert isinstance(c, OpenAIClient)


def test_build_llm_client_uses_placeholder_api_key_when_missing():
    """When api_key is None/empty but base_url is set, generate a
    placeholder so the OpenAI SDK doesn't refuse to construct."""
    from plugins.memory.graphiti.client import _build_llm_client

    c = _build_llm_client(
        provider="openai_compat",
        base_url="http://localhost:8080/v1",
        api_key=None,
    )
    cfg = getattr(c, "config", None)
    assert cfg is not None
    assert cfg.api_key == "sk-not-used-by-local-endpoint"


def test_build_llm_client_sets_model_and_small_model():
    from plugins.memory.graphiti.client import _build_llm_client

    c = _build_llm_client(
        provider="openai_compat",
        base_url="http://localhost:8080/v1",
        model="llama3-8b",
        small_model="phi3-mini",
    )
    cfg = getattr(c, "config", None)
    assert cfg.model == "llama3-8b"
    assert cfg.small_model == "phi3-mini"


def test_build_llm_client_unknown_provider_raises():
    from plugins.memory.graphiti.client import _build_llm_client

    with pytest.raises(ValueError, match="unknown graphiti llm_provider"):
        _build_llm_client(provider="cohere", base_url="http://x")


def test_build_llm_client_empty_provider_with_base_url_normalizes_to_openai():
    """Empty provider + base_url should be treated as openai with custom
    endpoint, not fall through to ValueError."""
    from plugins.memory.graphiti.client import _build_llm_client
    from graphiti_core.llm_client.openai_client import OpenAIClient

    c = _build_llm_client(provider="", base_url="http://localhost:11434/v1")
    assert isinstance(c, OpenAIClient)


def test_build_llm_client_ollama_without_base_url_raises():
    """Non-openai providers like ollama MUST have a base_url set."""
    from plugins.memory.graphiti.client import _build_llm_client

    with pytest.raises(ValueError, match="requires llm_base_url"):
        _build_llm_client(provider="ollama", base_url="")


def test_build_llm_client_vllm_without_base_url_raises():
    from plugins.memory.graphiti.client import _build_llm_client

    with pytest.raises(ValueError, match="requires llm_base_url"):
        _build_llm_client(provider="vllm", base_url="")


# ---------------------------------------------------------------------------
# 5c.2 — _build_embedder
# ---------------------------------------------------------------------------

def test_build_embedder_local_returns_fastembed_embedder():
    from plugins.memory.graphiti.client import _build_embedder
    from plugins.memory.graphiti.embedder_local import FastembedEmbedder

    e = _build_embedder(provider="local")
    assert isinstance(e, FastembedEmbedder)


def test_build_embedder_local_respects_model_override():
    from plugins.memory.graphiti.client import _build_embedder
    from plugins.memory.graphiti.embedder_local import FastembedEmbedder

    e = _build_embedder(provider="local", model="BAAI/bge-small-en-v1.5")
    assert isinstance(e, FastembedEmbedder)
    assert e.model_name == "BAAI/bge-small-en-v1.5"


def test_build_embedder_local_uses_bge_m3_by_default():
    from plugins.memory.graphiti.client import _build_embedder

    e = _build_embedder(provider="local", model=None)
    assert "bge-m3" in e.model_name


def test_build_embedder_openai_returns_none():
    from plugins.memory.graphiti.client import _build_embedder

    assert _build_embedder(provider="openai") is None


def test_build_embedder_empty_provider_returns_none():
    from plugins.memory.graphiti.client import _build_embedder

    assert _build_embedder(provider="") is None


def test_build_embedder_unknown_provider_raises():
    from plugins.memory.graphiti.client import _build_embedder

    with pytest.raises(ValueError, match="unknown graphiti embedding_provider"):
        _build_embedder(provider="gemini")


# ---------------------------------------------------------------------------
# 5c.3 — Config schema + env-var wiring
# ---------------------------------------------------------------------------

def test_config_schema_has_llm_fields():
    from plugins.memory.graphiti.config import get_config_schema

    fields = {f["key"]: f for f in get_config_schema()}
    assert "llm_provider" in fields
    assert "llm_base_url" in fields
    assert "llm_api_key" in fields
    assert "llm_model" in fields
    assert "llm_small_model" in fields
    # Secret field annotation
    assert fields["llm_api_key"].get("secret") is True


def test_load_config_reads_llm_env_vars(tmp_path, monkeypatch):
    """load_config must pick up GRAPHITI_LLM_* env vars."""
    from plugins.memory.graphiti.config import load_config

    monkeypatch.setenv("GRAPHITI_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("GRAPHITI_LLM_BASE_URL", "http://192.168.1.10:11434/v1")
    monkeypatch.setenv("GRAPHITI_LLM_API_KEY", "my-key")
    monkeypatch.setenv("GRAPHITI_LLM_MODEL", "llama3")
    monkeypatch.setenv("GRAPHITI_LLM_SMALL_MODEL", "phi3")

    # Give load_config a home dir that doesn't exist so it uses defaults.
    cfg = load_config(str(tmp_path / "no-such-dir"))

    assert cfg["llm_provider"] == "ollama"
    assert cfg["llm_base_url"] == "http://192.168.1.10:11434/v1"
    assert cfg["llm_api_key"] == "my-key"
    assert cfg["llm_model"] == "llama3"
    assert cfg["llm_small_model"] == "phi3"


def test_save_config_excludes_llm_api_key(tmp_path):
    """llm_api_key is a secret — save_config must strip it from the
    on-disk JSON file (same as falkordb_password)."""
    from plugins.memory.graphiti.config import save_config

    values = {
        "falkordb_host": "host",
        "falkordb_password": "secret-1",
        "llm_api_key": "secret-2",
        "llm_provider": "ollama",
    }
    save_config(values, str(tmp_path))

    path = tmp_path / "graphiti" / "config.json"
    saved = __import__("json").loads(path.read_text())
    assert "falkordb_password" not in saved
    assert "llm_api_key" not in saved
    assert saved["llm_provider"] == "ollama"    # non-secret is kept


# ---------------------------------------------------------------------------
# 5c.4 — FastembedEmbedder model alias resolution
# ---------------------------------------------------------------------------

def test_fastembed_embedder_default_model_is_bge_m3():
    from plugins.memory.graphiti.embedder_local import FastembedEmbedder

    e = FastembedEmbedder()
    assert "bge-m3" in e.model_name


def test_fastembed_embedder_resolves_aliases():
    from plugins.memory.graphiti.embedder_local import FastembedEmbedder

    e = FastembedEmbedder(model="bge-small")
    assert e.model_name == "BAAI/bge-small-en-v1.5"


def test_fastembed_embedder_passes_through_unknown_model():
    from plugins.memory.graphiti.embedder_local import FastembedEmbedder

    e = FastembedEmbedder(model="some/custom-model")
    assert e.model_name == "some/custom-model"


def test_fastembed_embedder_known_dimensions():
    """Known models return correct dimension without loading the model."""
    from plugins.memory.graphiti.embedder_local import FastembedEmbedder

    assert FastembedEmbedder(model="bge-m3").embedding_dim == 1024
    assert FastembedEmbedder(model="bge-small").embedding_dim == 384
    assert FastembedEmbedder(model="bge-base-en").embedding_dim == 768
    assert FastembedEmbedder(model="nomic-v1").embedding_dim == 768


def test_fastembed_embedder_stores_cache_dir_and_threads():
    from plugins.memory.graphiti.embedder_local import FastembedEmbedder

    e = FastembedEmbedder(cache_dir="/tmp/fastembed", threads=4)
    assert e._cache_dir == "/tmp/fastembed"
    assert e._threads == 4


def test_fastembed_embedder_lazy_does_not_load_on_init():
    """FastembedEmbedder.__init__ must NOT trigger any fastembed import
    or model download — that only happens on first create()."""
    from plugins.memory.graphiti.embedder_local import FastembedEmbedder

    e = FastembedEmbedder()
    assert e._model is None
    assert e._dim is None


def test_fastembed_embedder_embedding_dim_probe_raises_when_fastembed_not_installed():
    """embedding_dim triggers a probe `create` call, which tries to load
    fastembed.  When fastembed is not installed, this raises RuntimeError
    with a clear message — the caller should install fastembed or switch
    to the openai embedder."""
    from plugins.memory.graphiti.embedder_local import FastembedEmbedder

    e = FastembedEmbedder()
    # If fastembed happens to be installed, this returns an int — OK.
    # If not, it raises RuntimeError — also OK (clear guidance).
    try:
        dim = e.embedding_dim
        assert isinstance(dim, int) and dim > 0, (
            f"expected positive int, got {dim!r}"
        )
    except RuntimeError as exc:
        assert "fastembed" in str(exc).lower()


# ---------------------------------------------------------------------------
# 5c.5 — Client + Manager integration
# ---------------------------------------------------------------------------

def test_graphiti_client_stores_llm_params():
    from plugins.memory.graphiti.client import GraphitiClient

    c = GraphitiClient(
        graph_name="test",
        llm_provider="ollama",
        llm_base_url="http://localhost:11434/v1",
        llm_api_key="k",
        llm_model="mistral",
        llm_small_model="tinyllama",
    )
    assert c._llm_provider == "ollama"
    assert c._llm_base_url == "http://localhost:11434/v1"
    assert c._llm_api_key == "k"
    assert c._llm_model == "mistral"
    assert c._llm_small_model == "tinyllama"


def test_graphiti_client_default_llm_provider_is_openai():
    from plugins.memory.graphiti.client import GraphitiClient

    c = GraphitiClient(graph_name="test")
    assert c._llm_provider == "openai"
    assert c._llm_base_url is None
    assert c._llm_api_key is None


def test_graphiti_client_manager_passes_llm_config_to_client():
    from plugins.memory.graphiti.client import GraphitiClientManager

    config = {
        "falkordb_host": "host",
        "falkordb_port": 1234,
        "llm_provider": "ollama",
        "llm_base_url": "http://localhost:11434/v1",
        "llm_api_key": "sk-abc",
        "llm_model": "llama3",
        "llm_small_model": "",
    }
    mgr = GraphitiClientManager(config)
    mgr.bind_scope(member_id="m1", team_id=None, project_id=None)

    with patch.object(mgr, "_client", wraps=mgr._client) as spy:
        client = mgr._client("member_m1")

    assert client._llm_provider == "ollama"
    assert client._llm_base_url == "http://localhost:11434/v1"
    assert client._llm_api_key == "sk-abc"
    assert client._llm_model == "llama3"
    assert client._llm_small_model == ""


def test_graphiti_client_manager_defaults_llm_to_openai():
    from plugins.memory.graphiti.client import GraphitiClientManager

    mgr = GraphitiClientManager({"falkordb_host": "h", "falkordb_port": 0})
    mgr.bind_scope(member_id="m1", team_id=None, project_id=None)
    client = mgr._client("member_m1")
    assert client._llm_provider == "openai"


def test_graphiti_client_manager_passes_embedding_to_client():
    from plugins.memory.graphiti.client import GraphitiClientManager

    config = {
        "falkordb_host": "host",
        "falkordb_port": 1234,
        "embedding_provider": "local",
        "embedding_model": "bge-large-en",
    }
    mgr = GraphitiClientManager(config)
    mgr.bind_scope(member_id="m1", team_id=None, project_id=None)
    client = mgr._client("member_m1")

    assert client._embedding_provider == "local"
    assert client._embedding_model == "bge-large-en"