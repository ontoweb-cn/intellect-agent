"""Automation Blueprint catalog — dual-source: built-in + user directory (HP-304)."""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any, Optional

import yaml

from intellect_constants import get_intellect_home

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _load_builtin() -> list[dict[str, Any]]:
    """Return built-in blueprints (thread-safe, cached)."""
    return [
        {
            "id": "daily-standup",
            "name": "Daily Standup Summary",
            "description": "Summarize yesterday's work and plan for today",
            "category": "productivity",
            "schedule": "0 9 * * 1-5",
            "prompt_template": (
                "Based on the session history, summarize what was accomplished "
                "yesterday and suggest priorities for today."
            ),
            "params": {},
            "skills": [],
            "delivery": "origin",
        },
        {
            "id": "code-review-check",
            "name": "Code Review Check",
            "description": "Check for open PRs needing review and summarize changes",
            "category": "development",
            "schedule": "0 10 * * 1-5",
            "prompt_template": (
                "Check the git repository for recent changes on the current branch. "
                "Run: git log --oneline origin/main..HEAD and summarize the changes "
                "that need code review."
            ),
            "params": {},
            "skills": ["git-manager"],
            "delivery": "origin",
        },
        {
            "id": "weekly-digest",
            "name": "Weekly Digest",
            "description": "Summarize the week's accomplishments and learnings",
            "category": "productivity",
            "schedule": "0 17 * * 5",
            "prompt_template": (
                "Review this week's sessions and produce a summary of: "
                "1) Key accomplishments, 2) Skills learned, 3) Open items for next week."
            ),
            "params": {},
            "skills": [],
            "delivery": "origin",
        },
        {
            "id": "site-monitor",
            "name": "Site Health Monitor",
            "description": "Check a website for availability and report status",
            "category": "devops",
            "schedule": "0 */4 * * *",
            "prompt_template": (
                "Visit {{url}} and check if it loads correctly. "
                "Report the HTTP status, page load time, and any visible errors."
            ),
            "params": {
                "url": {
                    "type": "string",
                    "description": "URL to monitor",
                    "default": None,
                },
            },
            "skills": ["web-browser"],
            "delivery": "origin",
        },
        {
            "id": "backup-reminder",
            "name": "Backup Reminder",
            "description": "Periodic backup verification check",
            "category": "devops",
            "schedule": "0 12 * * 0",
            "prompt_template": (
                "Check that recent backups exist for {{target}}. "
                "Verify the last backup timestamp and report any issues."
            ),
            "params": {
                "target": {
                    "type": "string",
                    "description": "What to check backups for (dotfiles, projects, databases)",
                    "default": "dotfiles",
                },
            },
            "skills": [],
            "delivery": "origin",
        },
    ]


def _user_catalog_dir() -> Path:
    return get_intellect_home() / "blueprints"


@functools.lru_cache(maxsize=1)
def _load_user_catalog() -> dict[str, dict[str, Any]]:
    """Load user blueprints from disk (cached, invalidated by process restart)."""
    user: dict[str, dict[str, Any]] = {}
    user_dir = _user_catalog_dir()
    if user_dir.is_dir():
        for yaml_file in sorted(user_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("id"):
                    user[data["id"]] = data
            except Exception:
                logger.debug(
                    "blueprint_catalog: failed to load %s", yaml_file, exc_info=True
                )
    return user


def load_catalog() -> list[dict[str, Any]]:
    """Return the merged catalog: user blueprints override built-in by id."""
    builtin = {bp["id"]: bp for bp in _load_builtin()}
    builtin.update(_load_user_catalog())
    return sorted(builtin.values(), key=lambda b: (b.get("category", ""), b.get("name", "")))


def find_blueprint(blueprint_id: str) -> Optional[dict[str, Any]]:
    """Find a blueprint by id."""
    for bp in load_catalog():
        if bp["id"] == blueprint_id:
            return bp
    return None


def register_skill_blueprint(skill_md_path: Path) -> Optional[str]:
    """Extract blueprint from SKILL.md ``metadata.intellect.blueprint`` block.

    Returns the blueprint id if registered, None otherwise.
    """
    try:
        text = skill_md_path.read_text(encoding="utf-8")[:8000]
        parts = text.split("---", 2)
        if len(parts) < 3:
            return None
        fm = yaml.safe_load(parts[1])
        if not isinstance(fm, dict):
            return None
        meta = fm.get("metadata", {})
        if not isinstance(meta, dict):
            return None
        intellect = meta.get("intellect", {})
        if not isinstance(intellect, dict):
            return None
        bp_data = intellect.get("blueprint")
        if not isinstance(bp_data, dict) or not bp_data.get("id"):
            return None

        from intellect_rust import rust_validate_blueprint_yaml

        yaml_str = yaml.dump(bp_data)
        try:
            rust_validate_blueprint_yaml(yaml_str)
        except Exception as e:
            logger.warning(
                "blueprint: skill %s has invalid blueprint: %s", skill_md_path, e
            )
            return None

        user_dir = _user_catalog_dir()
        user_dir.mkdir(parents=True, exist_ok=True)
        out_path = user_dir / f"{bp_data['id']}.yaml"
        out_path.write_text(yaml_str, encoding="utf-8")

        return bp_data["id"]
    except Exception:
        logger.debug(
            "blueprint: failed to extract from %s", skill_md_path, exc_info=True
        )
        return None
