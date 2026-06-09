"""Runtime context resolution for agent sessions.

When in single-user mode this provides simplified context aggregation
with profile-level scoping only — no member, team, or project layers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ──────────────────────────────────────────────────────────────────────────────
# CLI session file (.cli-session.json)
# ──────────────────────────────────────────────────────────────────────────────

def default_cli_session_path() -> Path:
    from intellect_constants import get_intellect_home
    return get_intellect_home() / ".cli-session.json"


def clear_cli_session(
    session_file: Path | None = None,
) -> None:
    """Remove CLI session file (best-effort)."""
    path = session_file or default_cli_session_path()
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def load_cli_session(
    session_file: Path | None = None,
) -> dict[str, Any] | None:
    """Load CLI session JSON; return None if missing or past ``expires_at``."""
    import json
    import time

    path = session_file or default_cli_session_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    expires = data.get("expires_at")
    if expires is not None:
        try:
            if time.time() > float(expires):
                clear_cli_session(path)
                return None
        except (TypeError, ValueError):
            pass
    return data


# ──────────────────────────────────────────────────────────────────────────────
# Legacy stubs — kept for backward compat with callers that still import them
# ──────────────────────────────────────────────────────────────────────────────


def resolve_member_id(
    *,
    platform: str = "cli",
    external_id: str | None = None,
    token: str | None = None,
    session_file: Path | None = None,
    config: dict[str, Any] | None = None,
    db: Any = None,
    session_key: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """Single-user stub: always returns (None, None)."""
    return None, None


def resolve_effective_member_id(
    *,
    config: dict[str, Any] | None = None,
    explicit_member_id: str | None = None,
    db: Any = None,
) -> str | None:
    """Single-user stub: always returns None."""
    return None


def resolve_project_id(
    *,
    member_id: str | None = None,
    team_id: str | None = None,
    headers: dict[str, str] | None = None,
    session_key: str | None = None,
    config: dict[str, Any] | None = None,
    db: Any = None,
    cli_project: str | None = None,
    active_project_file: Path | None = None,
) -> str | None:
    """Single-user stub: always returns None."""
    return None


def _resolve_wiki_path(
    runtime_ctx: RuntimeContext | None = None,
    config: dict[str, Any] | None = None,
    *,
    target_scope: str | None = None,
) -> str | None:
    """Single-user: resolve wiki path (default ~/wiki)."""
    from intellect_cli.wiki_scaffold import resolve_wiki_target
    try:
        from intellect_constants import get_intellect_home
        home = get_intellect_home()
    except Exception:
        from pathlib import Path
        home = Path.home() / ".intellect"
    target = resolve_wiki_target(
        intellect_home=home,
        config=config,
    )
    return str(target.path)


# ──────────────────────────────────────────────────────────────────────────────
# RuntimeContext
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RuntimeContext:
    """Aggregated context for one agent run.

    All fields are optional — single-user mode keeps only session-level
    metadata (platform, session_key, cwd, env). Legacy multi-user fields
    are accepted (for backward compat) but not used.
    """

    platform: str = ""
    session_key: str = ""
    env_snapshot: dict[str, str] | None = None
    terminal_cwd: str | None = None

    # Legacy multi-user fields — accepted but unused in single-user mode
    member_id: str | None = None
    member_role: str | None = None
    team_id: str | None = None
    project_id: str | None = None
    project_workspace: str | None = None


# ──────────────────────────────────────────────────────────────────────────────
# SOUL assembly (spec §11)
# ──────────────────────────────────────────────────────────────────────────────

def assemble_soul(runtime_ctx: RuntimeContext | None = None) -> list[str]:
    """Assemble SOUL parts — single-user: profile-level only.

    Profile SOUL is handled by system_prompt.py's load_soul_md().
    Returns an empty list (no member/team/project layers).
    """
    return []


# ──────────────────────────────────────────────────────────────────────────────
# cwd / env resolution (spec §12)
# ──────────────────────────────────────────────────────────────────────────────

def resolve_terminal_cwd(
    runtime_ctx: RuntimeContext | None = None,
    config: dict[str, Any] | None = None,
) -> str | None:
    """Resolve the terminal working directory — always profile workspace."""
    from intellect_constants import get_intellect_home

    pws = get_intellect_home() / "workspace"
    pws.mkdir(parents=True, exist_ok=True)
    return str(pws)


def build_env_snapshot(
    runtime_ctx: RuntimeContext | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Merge environment variables — single-user: profile .env only."""
    import os as _os
    result: dict[str, str] = dict(_os.environ)

    # Profile .env
    try:
        from intellect_constants import get_intellect_home
        profile_env = get_intellect_home() / ".env"
        if profile_env.exists():
            result.update(_parse_dotenv(profile_env))
    except Exception:
        pass

    # Wiki runtime env
    wiki_snap = _wiki_env_from_context(config)
    for key, value in wiki_snap.items():
        if value is not None:
            result[key] = value

    return result


WIKI_RUNTIME_ENV_KEYS = (
    "WIKI_PATH",
    "WIKI_SCOPE",
    "WIKI_SCOPE_ID",
    "WIKI_WRITE_MODE",
    "WIKI_SKILL_VERSION",
    "WIKI_TARGET_SCOPE",
)


def snapshot_wiki_runtime_env() -> dict[str, str | None]:
    import os as _os
    return {key: _os.environ.get(key) for key in WIKI_RUNTIME_ENV_KEYS}


def restore_wiki_runtime_env(snapshot: dict[str, str | None]) -> None:
    import os as _os
    for key in WIKI_RUNTIME_ENV_KEYS:
        val = snapshot.get(key)
        if val is None:
            _os.environ.pop(key, None)
        else:
            _os.environ[key] = val


def inject_wiki_runtime_env(
    config: dict[str, Any] | None = None,
    *,
    target_scope: str | None = None,
) -> dict[str, str | None]:
    """Set WIKI_* process env; return prior values for restore."""
    import os as _os

    old = snapshot_wiki_runtime_env()
    values = _wiki_env_from_context(
        config,
        target_scope=target_scope,
    )
    if target_scope:
        values["WIKI_TARGET_SCOPE"] = str(target_scope).strip().lower()
    else:
        values.pop("WIKI_TARGET_SCOPE", None)
    for key in WIKI_RUNTIME_ENV_KEYS:
        val = values.get(key)
        if val is None:
            _os.environ.pop(key, None)
        else:
            _os.environ[key] = val
    return old


def _wiki_env_from_context(
    config: dict[str, Any] | None,
    *,
    target_scope: str | None = None,
) -> dict[str, str]:
    from intellect_cli.wiki_scaffold import (
        env_wiki_path_for_scoping,
        resolve_wiki_target,
        wiki_write_mode,
    )

    try:
        from intellect_constants import get_intellect_home
        intellect_home = get_intellect_home()
    except Exception:
        intellect_home = Path.home() / ".intellect"

    target = resolve_wiki_target(
        intellect_home=intellect_home,
        member_id=None,
        team_id=None,
        project_id=None,
        target_scope=target_scope,
        env_wiki_path=env_wiki_path_for_scoping(config, member_id=None),
        config=config if isinstance(config, dict) else None,
    )
    skill_version = _loaded_llm_wiki_skill_version()
    # Single-user: actor_role=None means profile owner → read_write
    write_mode = wiki_write_mode(target.scope, None)
    out: dict[str, str] = {
        "WIKI_PATH": str(target.path),
        "WIKI_SCOPE": target.scope,
        "WIKI_SCOPE_ID": target.scope_id or "",
        "WIKI_WRITE_MODE": write_mode,
    }
    if skill_version:
        out["WIKI_SKILL_VERSION"] = skill_version
    return out


def _loaded_llm_wiki_skill_version() -> str | None:
    try:
        from intellect_constants import get_intellect_home
        from pathlib import Path as _Path
        import re

        skills_root = get_intellect_home() / "skills"
        candidates = list(skills_root.rglob("llm-wiki/SKILL.md"))
        if not candidates:
            candidates = [
                p for p in skills_root.rglob("SKILL.md")
                if "llm-wiki" in p.as_posix() or p.parent.name == "llm-wiki"
            ]
        for skill_md in candidates:
            text = skill_md.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"(?m)^version:\s*['\"]?([^'\"\n]+)", text)
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return None


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE .env file into a dict."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result
