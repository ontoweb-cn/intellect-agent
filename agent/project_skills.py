"""Project-local skills discovery + per-repo trust gate (G-16 / A3-4).

Two-gate model (deep-dive §9, R6 hard clause — both gates ship in this
same change set):

1. **Discovery gate** — ``skills.project_discovery`` (default True) plus
   the per-repo trust list ``skills.trusted_project_dirs`` (absolute paths
   stored in config.yaml, not a separate file). An untrusted project's
   skills directory is never even discovered.
2. **Content gate** (fail-closed) — trust only enables DISCOVERY. Every
   individual project skill is scanned (``tools.skills_guard.scan_skill``)
   before it is auto-loaded; anything dangerous — or a scanner failure —
   is excluded (quarantined). This is the only defense against
   "trust the repo, then git-pull an injected malicious skill".

Coverage note: the content gate protects the auto-load surface (slash
commands injected into the model). Direct user-initiated reads
(``skill_view``) remain user-directed actions.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

PROJECT_SKILLS_SUBDIR = Path(".intellect") / "skills"
_DEFAULT_KEEP = 50

# in-process scan verdict cache keyed by (path, max-mtime) — per-process
# only; a fresh process rescans (trust never skips the content gate).
_scan_cache: dict = {}


# ── config access ───────────────────────────────────────────────────────

def _load_cfg() -> dict:
    try:
        from intellect_cli.config import load_config

        return load_config() or {}
    except Exception:
        return {}


def _skills_cfg(cfg: Optional[dict] = None) -> dict:
    data = (cfg or _load_cfg()).get("skills")
    return data if isinstance(data, dict) else {}


def project_discovery_enabled() -> bool:
    """``skills.project_discovery`` — default True (recorded ruling:
    shipped together with the content quarantine gate, R6)."""
    try:
        return bool(_skills_cfg().get("project_discovery", True))
    except Exception:
        return False


def trusted_project_dirs() -> List[str]:
    try:
        dirs = _skills_cfg().get("trusted_project_dirs") or []
        if isinstance(dirs, list):
            return [str(d) for d in dirs if d]
    except Exception:
        pass
    return []


# ── persistence (config.yaml round-trip, scoped to the one key) ─────────

def _save_trusted_dirs(dirs: List[str]) -> bool:
    """Persist ``skills.trusted_project_dirs`` into the user config.yaml.

    Scoped best-effort rewrite of that single key (same yaml round-trip
    trade-off as ``cli.save_config_value``).
    """
    try:
        import yaml

        from intellect_constants import get_intellect_home

        config_path = get_intellect_home() / "config.yaml"
        data = {}
        if config_path.exists():
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        skills = data.get("skills")
        if not isinstance(skills, dict):
            skills = {}
            data["skills"] = skills
        skills["trusted_project_dirs"] = dirs
        config_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return True
    except Exception as exc:
        logger.warning("Could not persist trusted project dirs: %s", exc)
        return False


# ── trust operations ────────────────────────────────────────────────────

def find_project_skills_root(cwd: Optional[Path] = None) -> Optional[Path]:
    """Walk up from ``cwd`` to the git root; return ``<root>/.intellect/skills``.

    None when no git root is found above the working directory.
    """
    start = Path(cwd or os.getcwd()).resolve()
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory / PROJECT_SKILLS_SUBDIR
        if directory.parent == directory:
            break
    return None


def trust_project(path: Optional[Path] = None) -> Optional[Path]:
    """Add a project's skills root to the trust list. Returns the root."""
    root = find_project_skills_root(path or os.getcwd())
    if root is None:
        return None
    dirs = trusted_project_dirs()
    resolved = str(root.resolve())
    if resolved not in [str(Path(d).resolve()) for d in dirs]:
        dirs.append(resolved)
        if not _save_trusted_dirs(dirs):
            return None
    return root


def untrust_project(path: Optional[Path] = None) -> Optional[Path]:
    root = find_project_skills_root(path or os.getcwd())
    if root is None:
        return None
    resolved = str(root.resolve())
    dirs = [d for d in trusted_project_dirs()
            if str(Path(d).resolve()) != resolved]
    _save_trusted_dirs(dirs)
    return root


def is_project_trusted(root: Path) -> bool:
    resolved = str(Path(root).resolve())
    return resolved in [str(Path(d).resolve()) for d in trusted_project_dirs()]


# ── discovery gate ──────────────────────────────────────────────────────

def project_skill_dirs(cwd: Optional[Path] = None) -> List[Path]:
    """Trusted project skills directories for this working directory.

    Discovery gate only: total switch + per-repo trust + existence.
    """
    if not project_discovery_enabled():
        return []
    root = find_project_skills_root(cwd)
    if root is None or not root.is_dir():
        return []
    if not is_project_trusted(root):
        return []
    return [root]


# ── content gate (fail-closed) ──────────────────────────────────────────

def project_skill_allowed(skill_md: Path, cwd: Optional[Path] = None) -> bool:
    """Content-level second gate for one project SKILL.md (R6).

    Trust enables discovery; each skill is still scanned before auto-load.
    A dangerous verdict — or ANY scanner failure — excludes the skill
    (fail-closed quarantine). Verdicts are cached per (path, mtime).
    """
    try:
        skill_md = Path(skill_md)
        skill_dir = skill_md.parent
        stamp = max(f.stat().st_mtime for f in skill_dir.rglob("*") if f.is_file()) \
            if skill_dir.is_dir() else 0
        key = (str(skill_md), stamp)
        cached = _scan_cache.get(key)
        if cached is not None:
            return cached
        from tools.skills_guard import scan_skill

        result = scan_skill(skill_dir, source="project")
        allowed = result.verdict == "safe"
    except Exception as exc:
        logger.warning(
            "Project skill scan failed for %s — fail-closed quarantine: %s",
            skill_md, exc,
        )
        allowed = False
    # unbounded dict is bounded in practice (per-process, per-file); keep it
    # from growing without limit over very long sessions anyway.
    if len(_scan_cache) > 512:
        _scan_cache.clear()
    _scan_cache[key] = allowed
    return allowed
