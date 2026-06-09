"""Volcengine Ark URL → provider inference (path disambiguation)."""

from __future__ import annotations


def test_infer_volcengine_variants_by_path():
    from agent.model_metadata import _infer_provider_from_url

    base = "https://ark.cn-beijing.volces.com"
    assert _infer_provider_from_url(f"{base}/api/v3") == "volcengine"
    assert _infer_provider_from_url(f"{base}/api/coding/v3") == "volcengine-coding-plan"
    assert _infer_provider_from_url(f"{base}/api/plan/v3") == "volcengine-agent-plan"
