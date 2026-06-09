"""Write guards for scoped LLM Wiki paths."""

from __future__ import annotations

from pathlib import Path

from intellect_cli.wiki_scaffold import global_wiki_dir


class WikiWriteForbidden(PermissionError):
    """Raised when a write targets a read-only wiki scope."""


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def assert_wiki_write_allowed(
    target_path: Path | str,
    *,
    write_mode: str | None = None,
    global_root: Path | str | None = None,
    intellect_home: Path | str | None = None,
) -> None:
    """Block writes under global wiki when WIKI_WRITE_MODE is read_only."""
    if str(write_mode or "").strip().lower() != "read_only":
        return
    home = Path(intellect_home).expanduser() if intellect_home else None
    root = Path(global_root).expanduser() if global_root else (
        global_wiki_dir(home) if home else None
    )
    if root is None:
        try:
            from intellect_constants import get_intellect_home

            root = global_wiki_dir(get_intellect_home())
        except Exception:
            return
    if _is_under(Path(target_path), root):
        raise WikiWriteForbidden(
            "Global wiki is read-only for your role. "
            "Write to your personal wiki or ask an admin to merge."
        )
