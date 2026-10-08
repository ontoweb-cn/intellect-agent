"""Regression tests: /api/models must not emit groups for unconfigured providers.

Root cause (container deployments): any dict-shaped entry under config.yaml
``providers:`` — including a bare ``providers.deepseek: {}`` copied from an
example config — seeded a picker group that was then backfilled from the
static ``_PROVIDER_MODELS`` catalog. Users saw a wall of models belonging to
providers they have no credentials for; selecting any of them fails.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def models_payload(monkeypatch, tmp_path):
    """Build get_available_models() output for a written config.yaml, fully offline."""
    from webui.api import config as apicfg

    def _run(cfg_text: str) -> dict:
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(cfg_text)
        monkeypatch.setenv("INTELLECT_CONFIG_PATH", str(cfg_path))
        # Reset the module-level /api/models cache so each scenario rebuilds.
        monkeypatch.setattr(apicfg, "_available_models_cache", None, raising=False)
        monkeypatch.setattr(apicfg, "_available_models_cache_ts", 0.0, raising=False)
        monkeypatch.setattr(
            apicfg, "_available_models_cache_source_fingerprint", None, raising=False
        )

        def _no_network(*args, **kwargs):
            raise OSError("network disabled in test")

        monkeypatch.setattr("socket.create_connection", _no_network)
        with patch("intellect_cli.models.get_curated_ontoweb_model_ids", return_value=[]), \
             patch("agent.models_dev.fetch_models_dev", return_value={}), \
             patch("intellect_cli.models.fetch_openrouter_models", return_value=[]), \
             patch("intellect_cli.models._fetch_anthropic_models", return_value=[]):
            return apicfg.get_available_models()

    return _run


def _group_ids(payload: dict) -> set[str]:
    return {g.get("provider_id") for g in payload.get("groups", [])}


_GPUSTACK_ACTIVE = """
model:
  provider: gpustack
  default: qwen3-32b
  base_url: https://ai.wust.edu.cn/gpustack/v1-openai
"""


def test_empty_provider_dicts_do_not_seed_groups(models_payload):
    """``providers.deepseek: {}`` (copied-example shape) must NOT render a
    full static catalog for a provider the user has no credentials for."""
    payload = models_payload(_GPUSTACK_ACTIVE + """
providers:
  gpustack:
    base_url: https://ai.wust.edu.cn/gpustack/v1-openai
    api_key: sk-gs-test
  deepseek: {}
  zai: {}
  kimi-coding: {}
  minimax: {}
""")
    ids = _group_ids(payload)
    assert "gpustack" in ids
    for phantom in ("deepseek", "zai", "kimi-coding", "minimax"):
        assert phantom not in ids, f"phantom group for unconfigured {phantom}"


def test_actionable_provider_entry_still_seeds_group(models_payload):
    """A providers entry that actually configures something (api_key) keeps
    its group — #604 behavior preserved."""
    payload = models_payload(_GPUSTACK_ACTIVE + """
providers:
  deepseek:
    api_key: sk-real-key
""")
    assert "deepseek" in _group_ids(payload)


def test_base_url_only_entry_passes_gate(models_payload):
    """Keyless endpoints configured by URL alone (lmstudio/vllm/... or a
    self-hosted gateway) must still pass the detection gate — the gate
    excludes only entries that configure nothing at all. deepseek here uses
    the generic group branch, which emits a group even when the live probe
    fails (offline fallback to the static catalog)."""
    payload = models_payload(_GPUSTACK_ACTIVE + """
providers:
  deepseek:
    base_url: http://127.0.0.1:9999/v1
""")
    assert "deepseek" in _group_ids(payload)


def test_scalar_provider_keys_still_excluded(models_payload):
    """Scalar ``providers:`` siblings are config flags, not providers (#2399)."""
    payload = models_payload(_GPUSTACK_ACTIVE + """
providers:
  only_configured: true
""")
    assert "only-configured" not in _group_ids(payload)
    assert "only_configured" not in _group_ids(payload)


def test_models_allowlist_entry_seeds_group(models_payload):
    """A providers entry that only pins a model allowlist still seeds."""
    payload = models_payload(_GPUSTACK_ACTIVE + """
providers:
  deepseek:
    models:
      deepseek-chat: {}
""")
    assert "deepseek" in _group_ids(payload)
