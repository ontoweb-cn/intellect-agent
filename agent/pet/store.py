"""Pet store: <INTELLECT_HOME>/pets/<slug>/ with traversal-safe slugs."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from intellect_constants import get_intellect_home

logger = logging.getLogger(__name__)

PETS_DIR_NAME = "pets"
PET_META_NAME = "pet.json"


def pets_dir() -> Path:
    return get_intellect_home() / PETS_DIR_NAME


def safe_slug(slug: str) -> Optional[str]:
    """Validate a pet slug — traversal-proof (no separators, no dots-led,
    charset [a-z0-9-], length cap)."""
    slug = (slug or "").strip().lower()
    if not slug or len(slug) > 64:
        return None
    if slug.startswith(".") or ".." in slug:
        return None
    if not all(c.isalnum() or c == "-" for c in slug):
        return None
    return slug


def pet_dir(slug: str) -> Optional[Path]:
    safe = safe_slug(slug)
    if safe is None:
        return None
    return pets_dir() / safe


def install(slug: str, files: Dict[str, bytes], meta: dict) -> Optional[Path]:
    """Install a pet (meta JSON + asset files) into the store."""
    directory = pet_dir(slug)
    if directory is None:
        return None
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    meta = dict(meta)
    meta.setdefault("slug", slug)
    (directory / PET_META_NAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for name, blob in files.items():
        target = (directory / name).resolve()
        if not str(target).startswith(str(directory.resolve())):
            logger.warning("pet asset path escapes store: %s", name)
            continue
        target.write_bytes(blob)
    return directory


def load_pet(slug: str) -> Optional[dict]:
    directory = pet_dir(slug)
    meta_path = (directory / PET_META_NAME) if directory else None
    if not meta_path or not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def installed() -> List[dict]:
    """All installed pets (pet.json metadata), slug-sorted."""
    root = pets_dir()
    if not root.is_dir():
        return []
    out = []
    for entry in sorted(root.iterdir()):
        meta = load_pet(entry.name) if entry.is_dir() else None
        if meta:
            out.append(meta)
    return out


def remove(slug: str) -> bool:
    import shutil

    directory = pet_dir(slug)
    if not directory or not directory.is_dir():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return True
