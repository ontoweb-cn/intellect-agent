"""Cross-gateway pollable run status via CacheBackend (P4a / T3)."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_PREFIX = "intellect:run:status:"
_DEFAULT_TTL = 3600


class RunStatusStore:
    """Local dict with optional Redis backing for GET /v1/runs/{id}."""

    def __init__(self, cache=None, *, ttl: int = _DEFAULT_TTL):
        self._local: dict[str, dict[str, Any]] = {}
        self._cache = cache
        self._ttl = ttl

    def _key(self, run_id: str) -> str:
        return f"{_PREFIX}{run_id}"

    def set(self, run_id: str, status: dict[str, Any]) -> dict[str, Any]:
        self._local[run_id] = status
        if self._cache is not None:
            try:
                self._cache.set_sync(self._key(run_id), status, ttl=self._ttl)
            except Exception:
                logger.debug("run status cache set failed", exc_info=True)
        return status

    def get(self, run_id: str) -> dict[str, Any] | None:
        local = self._local.get(run_id)
        if local is not None:
            return local
        if self._cache is None:
            return None
        try:
            raw = self._cache.get_sync(self._key(run_id))
            return raw if isinstance(raw, dict) else None
        except Exception:
            return None

    def pop(self, run_id: str) -> None:
        self._local.pop(run_id, None)
        if self._cache is not None:
            try:
                self._cache.delete_sync(self._key(run_id))
            except Exception:
                pass

    def items_terminal_before(self, cutoff: float) -> list[tuple[str, dict[str, Any]]]:
        out: list[tuple[str, dict[str, Any]]] = []
        for run_id, status in list(self._local.items()):
            updated = float(status.get("updated_at") or 0)
            if status.get("status") in {"completed", "failed", "cancelled"} and updated < cutoff:
                out.append((run_id, status))
        return out


def create_run_status_store(config: dict | None = None) -> RunStatusStore:
    cache = None
    try:
        from agent.cache.factory import create_cache_backend, get_cache_backend_name

        cfg = config or {}
        if get_cache_backend_name(cfg) == "redis":
            backend = create_cache_backend(cfg)
            if type(backend).__name__ != "MemoryCache":
                cache = backend
    except Exception:
        logger.debug("run status cache init failed", exc_info=True)
    return RunStatusStore(cache)
