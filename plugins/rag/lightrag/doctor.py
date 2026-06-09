"""LightRAG health diagnostics for ``intellect doctor``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

IssueFn = Callable[[str, str, str], None]
CheckOk = Callable[[str, str], None]
CheckWarn = Callable[[str, str], None]
CheckInfo = Callable[[str], None]


def diagnose_lightrag_rag(
    intellect_home: str,
    *,
    check_ok: CheckOk,
    check_warn: CheckWarn,
    check_info: CheckInfo,
    fail_fn: IssueFn,
    issues: List[str],
) -> None:
    """Run LightRAG checks when ``rag.provider: lightrag``."""
    from plugins.rag import load_rag_provider
    from plugins.rag.lightrag import LightRAGRAGProvider
    from plugins.rag.lightrag.client import LightRAGClientManager, LightRAGUnavailable
    from plugins.rag.lightrag.config import load_config

    provider = load_rag_provider("lightrag")
    if provider is None or not isinstance(provider, LightRAGRAGProvider):
        fail_fn(
            "LightRAG plugin not loadable",
            "plugins/rag/lightrag missing or broken",
            "RAG provider is lightrag but the plugin failed to import",
            issues,
        )
        return

    cfg = load_config(intellect_home)
    cfg_path = Path(intellect_home) / "lightrag" / "config.json"
    base_url = (cfg.get("server") or {}).get("base_url", "")

    if not provider.is_available():
        fail_fn(
            "LightRAG not configured",
            f"set server.base_url in {cfg_path}",
            "rag.provider is lightrag but server.base_url is empty",
            issues,
        )
        return

    ingest_mode = (cfg.get("ingest") or {}).get("auto_mode", "off")
    check_info(f"config: {cfg_path}")
    check_info(f"server: {base_url}  ingest.auto_mode={ingest_mode!r}")

    mgr: Optional[LightRAGClientManager] = None
    try:
        mgr = LightRAGClientManager(cfg)
        health = mgr.health()
        status = health.get("status") or health.get("pipeline_busy") or "ok"
        embedding = _extract_embedding_summary(health)
        detail = f"status={status}"
        if embedding:
            detail += f"  embedding={embedding}"
        check_ok("LightRAG server reachable", detail)

        dim_warn = _embedding_dimension_warning(health)
        if dim_warn:
            check_warn("LightRAG embedding config", dim_warn)

        if health.get("pipeline_busy"):
            check_warn(
                "LightRAG pipeline busy",
                "indexing in progress — queries may be slow",
            )
    except LightRAGUnavailable as exc:
        fail_fn(
            "LightRAG server unreachable",
            str(exc),
            f"Cannot reach LightRAG at {base_url}: {exc}",
            issues,
        )
    except Exception as exc:
        fail_fn(
            "LightRAG health check failed",
            str(exc),
            f"LightRAG doctor probe error: {exc}",
            issues,
        )
    finally:
        if mgr is not None:
            try:
                mgr.shutdown()
            except Exception:
                pass


def _extract_embedding_summary(health: Dict[str, Any]) -> str:
    for key in ("embedding_model", "embedding_binding", "embed_model"):
        val = health.get(key)
        if val:
            return str(val)
    emb = health.get("embedding")
    if isinstance(emb, dict):
        model = emb.get("model") or emb.get("model_name")
        if model:
            return str(model)
    return ""


def _embedding_dimension_warning(health: Dict[str, Any]) -> str:
    """Surface server hints about embedding dimension / re-index needs."""
    text = str(health).lower()
    if "dimension" in text and ("mismatch" in text or "re-index" in text):
        return (
            "embedding dimension may have changed — clear workspace "
            "and re-index documents"
        )
    status = str(health.get("status", "")).lower()
    if "embedding" in status and "error" in status:
        return health.get("message") or status
    return ""
