"""Skill lifecycle analytics — usage, iteration, and quality metrics.

Data sources:
- ``skills/`` directory: SKILL.md files, timestamps, sizes
- ``state.db`` messages table: tool_name, tool_calls columns
- git log: commit history for iteration tracking

Usage::

    from agent.skill_analytics import SkillAnalytics
    stats = SkillAnalytics().collect()
    # {
    #   "skills": [...],
    #   "summary": {"total": 90, "active_7d": 23, ...},
    #   "trends": {"daily": [...], "weekly": [...]},
    #   "top_used": [...],
    # }
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from intellect_constants import get_intellect_home

logger = logging.getLogger(__name__)

# ── Data models ──────────────────────────────────────────────────────────


@dataclass
class SkillStats:
    name: str
    path: str
    category: str = ""
    size_bytes: int = 0
    created_at: float = 0.0
    modified_at: float = 0.0
    git_commits: int = 0
    git_first_commit: float = 0.0
    git_last_commit: float = 0.0
    usage_count_7d: int = 0
    usage_count_30d: int = 0
    usage_count_total: int = 0
    success_rate: float = 0.0


@dataclass
class AnalyticsReport:
    skills: list[SkillStats] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    trends: dict[str, list[dict]] = field(default_factory=dict)
    top_used: list[dict] = field(default_factory=list)
    collected_at: float = 0.0


# ── Main collector ───────────────────────────────────────────────────────


class SkillAnalytics:
    """Collect and aggregate skill lifecycle metrics."""

    def __init__(self, home: Path | None = None) -> None:
        self._home = home or get_intellect_home()
        self._skills_dir = self._resolve_skills_dir()

    def collect(self, *, db: Any = None) -> AnalyticsReport:
        """Run full collection and return an AnalyticsReport."""
        skills = self._scan_skills()
        self._attach_git_history(skills)
        if db is not None:
            self._attach_usage_stats(skills, db)
        summary = self._build_summary(skills)
        trends = self._build_trends(skills)
        top = self._build_top_used(skills)
        return AnalyticsReport(
            skills=skills,
            summary=summary,
            trends=trends,
            top_used=top,
            collected_at=time.time(),
        )

    # ── Data sources ─────────────────────────────────────────────────

    def _resolve_skills_dir(self) -> Path:
        """Locate the skills directory (repo root or home)."""
        # Check repo-root skills/ first (dev), then home
        repo_root = Path(__file__).resolve().parent.parent
        candidate = repo_root / "skills"
        if candidate.is_dir():
            return candidate
        return self._home / "skills"

    def _scan_skills(self) -> list[SkillStats]:
        """Walk the skills directory and build SkillStats entries."""
        results: list[SkillStats] = []
        for skill_md in sorted(self._skills_dir.rglob("SKILL.md")):
            rel = skill_md.relative_to(self._skills_dir)
            category = str(rel.parent) if rel.parent != Path(".") else "root"
            stat = skill_md.stat()
            results.append(SkillStats(
                name=skill_md.parent.name,
                path=str(rel),
                category=category,
                size_bytes=stat.st_size,
                created_at=stat.st_ctime,
                modified_at=stat.st_mtime,
            ))
        return results

    def _attach_git_history(self, skills: list[SkillStats]) -> None:
        """Enrich skills with git commit counts and first/last commit dates.

        Uses ``git log --follow`` on each SKILL.md path.  Falls back
        gracefully if git is unavailable or the repo is shallow.
        """
        repo = self._skills_dir.parent
        try:
            for s in skills:
                rel_path = os.path.join("skills", s.path)
                result = subprocess.run(
                    ["git", "-C", str(repo), "log", "--follow", "--format=%ct", "--", rel_path],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode != 0:
                    continue
                timestamps = [int(t) for t in result.stdout.strip().split("\n") if t.strip()]
                if timestamps:
                    s.git_commits = len(timestamps)
                    s.git_first_commit = float(timestamps[-1])
                    s.git_last_commit = float(timestamps[0])
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            logger.debug("Git history unavailable: %s", exc)

    def _attach_usage_stats(self, skills: list[SkillStats], db: Any) -> None:
        """Query state.db for per-skill usage counts."""
        now = time.time()
        week_ago = now - 7 * 86400
        month_ago = now - 30 * 86400

        name_map: dict[str, SkillStats] = {s.name: s for s in skills}
        if not name_map:
            return

        try:
            rows = db._conn.execute(
                "SELECT tool_name, COUNT(*) AS cnt "
                "FROM messages "
                "WHERE tool_name IS NOT NULL AND tool_name != '' "
                "GROUP BY tool_name"
            ).fetchall()
            for row in rows:
                name = row["tool_name"]
                if name in name_map:
                    name_map[name].usage_count_total = row["cnt"]
        except Exception as exc:
            logger.debug("Usage stats unavailable: %s", exc)
            return

        try:
            recent = db._conn.execute(
                "SELECT tool_name, COUNT(*) AS cnt "
                "FROM messages "
                "WHERE tool_name IS NOT NULL AND tool_name != '' "
                "AND timestamp > ? "
                "GROUP BY tool_name",
                (month_ago,),
            ).fetchall()
            for row in recent:
                name = row["tool_name"]
                if name in name_map:
                    name_map[name].usage_count_30d = row["cnt"]
        except Exception:
            pass

        try:
            week = db._conn.execute(
                "SELECT tool_name, COUNT(*) AS cnt "
                "FROM messages "
                "WHERE tool_name IS NOT NULL AND tool_name != '' "
                "AND timestamp > ? "
                "GROUP BY tool_name",
                (week_ago,),
            ).fetchall()
            for row in week:
                name = row["tool_name"]
                if name in name_map:
                    name_map[name].usage_count_7d = row["cnt"]
        except Exception:
            pass

    # ── Aggregation ──────────────────────────────────────────────────

    def _build_summary(self, skills: list[SkillStats]) -> dict[str, Any]:
        now = time.time()
        week_ago = now - 7 * 86400
        month_ago = now - 30 * 86400

        total = len(skills)
        active_7d = sum(1 for s in skills if s.usage_count_7d > 0)
        active_30d = sum(1 for s in skills if s.usage_count_30d > 0)
        modified_30d = sum(1 for s in skills if s.modified_at > month_ago)
        total_usage = sum(s.usage_count_total for s in skills)
        total_size_kb = sum(s.size_bytes for s in skills) // 1024

        categories = defaultdict(int)
        for s in skills:
            categories[s.category] += 1

        return {
            "total_skills": total,
            "active_7d": active_7d,
            "active_30d": active_30d,
            "modified_30d": modified_30d,
            "total_usage_count": total_usage,
            "total_size_kb": total_size_kb,
            "categories": dict(sorted(categories.items())),
            "oldest_skill": min((s.created_at for s in skills), default=0),
            "newest_skill": max((s.created_at for s in skills), default=0),
            "avg_commits_per_skill": (
                sum(s.git_commits for s in skills) / max(total, 1)
            ),
        }

    def _build_trends(self, skills: list[SkillStats]) -> dict[str, list[dict]]:
        """Build daily/weekly creation and modification trends from file stats."""
        daily: dict[str, int] = defaultdict(int)
        weekly: dict[str, int] = defaultdict(int)
        daily_mod: dict[str, int] = defaultdict(int)

        for s in skills:
            if s.created_at > 0:
                day = time.strftime("%Y-%m-%d", time.gmtime(s.created_at))
                week = time.strftime("%Y-W%W", time.gmtime(s.created_at))
                daily[day] += 1
                weekly[week] += 1
            if s.modified_at > 0:
                day = time.strftime("%Y-%m-%d", time.gmtime(s.modified_at))
                daily_mod[day] += 1

        return {
            "daily_created": [{"date": k, "count": v} for k, v in sorted(daily.items())[-90:]],
            "weekly_created": [{"week": k, "count": v} for k, v in sorted(weekly.items())[-26:]],
            "daily_modified": [{"date": k, "count": v} for k, v in sorted(daily_mod.items())[-90:]],
        }

    def _build_top_used(self, skills: list[SkillStats]) -> list[dict]:
        ranked = sorted(skills, key=lambda s: s.usage_count_total, reverse=True)
        return [
            {
                "name": s.name,
                "category": s.category,
                "total": s.usage_count_total,
                "recent_30d": s.usage_count_30d,
                "commits": s.git_commits,
            }
            for s in ranked[:20] if s.usage_count_total > 0
        ]


# ── Convenience ──────────────────────────────────────────────────────────


def collect_skill_analytics(db: Any = None) -> dict[str, Any]:
    """One-shot collection returning a JSON-serializable dict."""
    from dataclasses import asdict

    report = SkillAnalytics().collect(db=db)
    result: dict[str, Any] = asdict(report)
    # Convert skills to plain dicts for JSON serialization
    result["skills"] = [asdict(s) for s in report.skills]
    return result
