"""Pet manifest: petdex.dev gallery (host-pinned, TTL-cached) with local
fallback — 网络失败降级本地清单."""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from agent.pet.constants import (
    ALLOWED_MANIFEST_HOST_SUFFIX,
    ALLOWED_MANIFEST_HOSTS,
    MANIFEST_TTL_S,
    MANIFEST_URL,
)

logger = logging.getLogger(__name__)


def _cache_path() -> Path:
    from intellect_constants import get_intellect_home

    return get_intellect_home() / "cache" / "pets" / "manifest.json"


def _host_allowed(url: str) -> bool:
    """SSRF host-pin: only petdex.dev / *.petdex.dev asset URLs."""
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in ALLOWED_MANIFEST_HOSTS or host.endswith(
        ALLOWED_MANIFEST_HOST_SUFFIX
    )


def _read_cached() -> Optional[Dict[str, Any]]:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = float(data.get("fetched_at", 0))
        if time.time() - fetched_at <= MANIFEST_TTL_S:
            return data
        return data  # stale but usable as offline fallback
    except (OSError, ValueError):
        return None


def fetch_manifest(timeout: float = 5.0, *, allow_stale: bool = True) -> Optional[Dict[str, Any]]:
    """Fetch the petdex gallery manifest (TTL-cached). Degrades to the
    local cached copy (even stale) when the network fails — 网络失败降级."""
    cache = _read_cached()
    fresh = cache and (time.time() - float(cache.get("fetched_at", 0)) <= MANIFEST_TTL_S)
    if fresh:
        return cache
    try:
        req = urllib.request.Request(
            MANIFEST_URL, headers={"User-Agent": "intellect-agent"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("manifest is not an object")
        # Host-pin every asset URL before persisting.
        for pet in data.get("pets", []):
            if not isinstance(pet, dict):
                continue
            for key in ("spritesheet_url", "preview_url"):
                url = str(pet.get(key) or "")
                if url and not _host_allowed(url):
                    raise ValueError(f"manifest asset host not allowed: {url}")
        data["fetched_at"] = time.time()
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data
    except Exception as exc:
        logger.debug("manifest fetch failed (%s) — local fallback", exc)
        if allow_stale and cache:
            return cache
        return _local_manifest()


def _local_manifest() -> Dict[str, Any]:
    """本地清单降级: installed pets become the gallery."""
    from agent.pet import store

    return {
        "source": "local",
        "pets": [
            {"slug": meta.get("slug"), "name": meta.get("name") or meta.get("slug"),
             "installed": True}
            for meta in store.installed()
        ],
    }


def manifest_entry(slug: str) -> Optional[Dict[str, Any]]:
    manifest = fetch_manifest() or {}
    for pet in manifest.get("pets", []):
        if isinstance(pet, dict) and pet.get("slug") == slug:
            return pet
    return None
