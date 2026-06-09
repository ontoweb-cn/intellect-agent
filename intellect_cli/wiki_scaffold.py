"""Shared LLM Wiki path resolution and directory scaffold for WebUI and CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_RAW_SUBDIRS = ("articles", "papers", "transcripts", "assets")
_PAGE_DIRS = ("entities", "concepts", "comparisons", "queries")
_GUARD_FILES = ("SCHEMA.md", "index.md")

_FORBIDDEN_ROOTS = frozenset(
    str(Path(p).expanduser().resolve())
    for p in ("/", "/etc", "/usr", "/var", "/opt", "/sys", "/proc")
)


class WikiInitError(Exception):
    """Raised when wiki initialization cannot proceed safely."""


@dataclass(frozen=True)
class WikiTarget:
    path: Path
    scope: str
    scope_id: str | None
    path_source: str


@dataclass
class InitResult:
    ok: bool
    path: Path
    files_created: list[str] = field(default_factory=list)
    files_skipped: list[str] = field(default_factory=list)
    error: str | None = None
    error_code: str | None = None


def safe_slug(value: str | None) -> bool:
    if not value or not str(value).strip():
        return False
    text = str(value).strip()
    if len(text) > 128:
        return False
    for ch in ("..", "/", "\\", "\0"):
        if ch in text:
            return False
    return True


def is_forbidden_path(path: Path) -> bool:
    try:
        return str(path.expanduser().resolve()) in _FORBIDDEN_ROOTS
    except Exception:
        return True


def redact_path_hint(path: Path) -> str:
    try:
        resolved = path.expanduser().resolve()
        home = Path.home().resolve()
        text = str(resolved)
        if text.startswith(str(home)):
            return "~" + text[len(str(home)) :]
        return text
    except Exception:
        return str(path)


WIKI_ENV_KEYS = (
    "WIKI_PATH",
    "WIKI_SCOPE",
    "WIKI_SCOPE_ID",
    "WIKI_WRITE_MODE",
    "WIKI_SKILL_VERSION",
    "WIKI_TARGET_SCOPE",
)

_GLOBAL_WIKI_REL = Path("wiki") / "global"
_FORBIDDEN_CONTRIB_PATHS = frozenset({"SCHEMA.md", "index.md", "log.md"})


def global_wiki_dir(intellect_home: Path | str) -> Path:
    """Canonical multi-tenant Global LLM Wiki directory."""
    return Path(intellect_home).expanduser() / _GLOBAL_WIKI_REL


def wiki_scoping_mode(config: dict[str, Any] | None) -> str:
    """Return wiki scoping mode: auto (default), tenant, or global."""
    if not isinstance(config, dict):
        return "auto"
    skills = config.get("skills")
    if not isinstance(skills, dict):
        return "auto"
    cfg = skills.get("config")
    if not isinstance(cfg, dict):
        return "auto"
    wiki = cfg.get("wiki")
    if not isinstance(wiki, dict):
        return "auto"
    raw = str(wiki.get("scoping") or "auto").strip().lower()
    if raw in ("auto", "tenant", "global"):
        return raw
    return "auto"


def members_scoping_active(config: dict[str, Any] | None, member_id: str | None) -> bool:
    """True when tenant wiki paths should override profile-level WIKI_PATH."""
    if not member_id:
        return False
    mode = wiki_scoping_mode(config)
    if mode == "global":
        return False
    if mode == "tenant":
        return True
    # (single-user: always use profile/global scope)
    return False


def env_wiki_path_for_scoping(
    config: dict[str, Any] | None,
    *,
    member_id: str | None = None,
) -> str | None:
    """Profile WIKI_PATH override, suppressed under multi-tenant scoping."""
    if members_scoping_active(config, member_id):
        return None
    import os

    raw = os.getenv("WIKI_PATH")
    if raw:
        return str(raw).strip() or None
    cfg_path = _config_wiki_path(config)
    return cfg_path


def wiki_write_mode(scope: str, actor_role: str | None) -> str:
    """read_write or read_only (Global wiki for non-admin members).

    In single-user mode (actor_role is None), the user is effectively the
    profile owner and always has read_write access.
    """
    if scope == "global":
        role = str(actor_role or "").strip().lower()
        if not role:
            # Single-user: no role context → profile owner
            return "read_write"
        if role in ("owner", "admin"):
            return "read_write"
        return "read_only"
    return "read_write"


def contributions_staging_root(intellect_home: Path | str) -> Path:
    return Path(intellect_home).expanduser() / "wiki" / ".contributions"


def _config_wiki_path(config: dict[str, Any] | None) -> str | None:
    if not isinstance(config, dict):
        return None
    skills = config.get("skills")
    if not isinstance(skills, dict):
        return None
    cfg = skills.get("config")
    if not isinstance(cfg, dict):
        return None
    wiki = cfg.get("wiki")
    if not isinstance(wiki, dict):
        return None
    raw = wiki.get("path")
    return str(raw).strip() if raw else None


def resolve_wiki_target(
    *,
    intellect_home: Path | str,
    member_id: str | None = None,
    team_id: str | None = None,
    project_id: str | None = None,
    target_scope: str | None = None,
    env_wiki_path: str | None = None,
    config: dict[str, Any] | None = None,
) -> WikiTarget:
    """Resolve the wiki path for the active tenant context (may not exist yet)."""
    home = Path(intellect_home).expanduser()
    explicit = str(target_scope or "").strip().lower()
    if explicit == "global":
        return WikiTarget(global_wiki_dir(home), "global", None, "global")

    effective_env = env_wiki_path
    if effective_env is None and not members_scoping_active(config, member_id):
        import os

        raw = os.getenv("WIKI_PATH")
        effective_env = str(raw).strip() if raw else _config_wiki_path(config)

    if effective_env:
        import os

        path = Path(effective_env).expanduser()
        cfg_path = _config_wiki_path(config)
        env_raw = os.getenv("WIKI_PATH")
        if env_raw and Path(env_raw).expanduser() == path:
            source = "WIKI_PATH"
        elif cfg_path and Path(cfg_path).expanduser() == path:
            source = "skills.config.wiki.path"
        else:
            source = "WIKI_PATH"
        return WikiTarget(path, "global", None, source)

    if project_id and safe_slug(project_id):
        path = home / "projects" / project_id / "wiki"
        return WikiTarget(path, "project", project_id, "project")

    if team_id and safe_slug(team_id):
        path = home / "teams" / team_id / "wiki"
        return WikiTarget(path, "team", team_id, "team")

    if member_id and safe_slug(member_id):
        path = home / "members" / member_id / "wiki"
        return WikiTarget(path, "member", member_id, "member")

    cfg_path = _config_wiki_path(config)
    if cfg_path:
        path = Path(cfg_path).expanduser()
        return WikiTarget(path, "global", None, "skills.config.wiki.path")

    path = Path.home() / "wiki"
    return WikiTarget(path, "global", None, "default")


def _schema_template(domain: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    domain_text = (domain or "General knowledge").strip() or "General knowledge"
    return f"""# Wiki Schema

## Domain
{domain_text}

## Conventions
- File names: lowercase, hyphens, no spaces (e.g., `transformer-architecture.md`)
- Every wiki page starts with YAML frontmatter (see below)
- Use `[[wikilinks]]` to link between pages (minimum 2 outbound links per page)
- When updating a page, always bump the `updated` date
- Every new page must be added to `index.md` under the correct section
- Every action must be appended to `log.md`

## Frontmatter
```yaml
---
title: Page Title
created: {today}
updated: {today}
type: entity | concept | comparison | query | summary
tags: [from taxonomy below]
sources: [raw/articles/source-name.md]
---
```

## Tag Taxonomy
- Models: model, architecture, benchmark, training
- People/Orgs: person, company, lab, open-source
- Techniques: optimization, fine-tuning, inference, alignment, data
- Meta: comparison, timeline, controversy, prediction

## Page Thresholds
- **Create a page** when an entity/concept appears in 2+ sources OR is central to one source
- **Add to existing page** when a source mentions something already covered
- **DON'T create a page** for passing mentions or minor details
"""


def _index_template() -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"""# Wiki Index

> Catalog of wiki pages. Updated: {today}

## Entities

## Concepts

## Comparisons

## Queries

## Recent
- Wiki initialized via WebUI on {today}
"""


def _log_template() -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"""# Wiki Log

## [{today}] wiki-initialized | webui
- Created standard LLM Wiki scaffold (SCHEMA.md, index.md, directories)
"""


def init_wiki(path: Path | str, *, domain: str = "General knowledge") -> InitResult:
    """Create the standard LLM Wiki directory scaffold at *path*."""
    wiki_path = Path(path).expanduser()

    if is_forbidden_path(wiki_path):
        return InitResult(
            ok=False,
            path=wiki_path,
            error="Refusing to initialize wiki at a forbidden system path.",
            error_code="forbidden_path",
        )

    for name in _GUARD_FILES:
        guard = wiki_path / name
        if guard.exists() and guard.is_file():
            return InitResult(
                ok=False,
                path=wiki_path,
                error="Wiki already exists at this location.",
                error_code="wiki_already_exists",
            )

    created: list[str] = []
    skipped: list[str] = []

    def _mkdir(rel: str) -> None:
        target = wiki_path / rel
        if target.exists():
            skipped.append(rel + "/")
            return
        target.mkdir(parents=True, exist_ok=True)
        created.append(rel + "/")

    def _write(rel: str, content: str) -> None:
        target = wiki_path / rel
        if target.exists():
            skipped.append(rel)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        created.append(rel)

    try:
        for sub in _RAW_SUBDIRS:
            _mkdir(f"raw/{sub}")
        for sub in _PAGE_DIRS:
            _mkdir(sub)
        _write("SCHEMA.md", _schema_template(domain))
        _write("index.md", _index_template())
        _write("log.md", _log_template())
    except OSError as exc:
        return InitResult(
            ok=False,
            path=wiki_path,
            error=str(exc),
            error_code="io_error",
        )

    return InitResult(ok=True, path=wiki_path, files_created=created, files_skipped=skipped)
