"""Shared path validation helpers for tool implementations.

Extracts the ``resolve() + relative_to()`` and ``..`` traversal check
patterns previously duplicated across skill_manager_tool, skills_tool,
skills_hub, cronjob_tools, and credential_files.
"""

import logging
from pathlib import Path
from typing import Optional

from intellect_rust import rust_is_forbidden_path as _rust_is_forbidden_path

logger = logging.getLogger(__name__)


def is_forbidden_path(path: str) -> Optional[str]:
    """Check if a file path targets a sensitive system location.

    Returns a reason string if the path is forbidden, ``None`` if safe.
    """
    return _rust_is_forbidden_path(path)


def validate_within_dir(path: Path, root: Path) -> Optional[str]:
    """Ensure *path* resolves to a location within *root*.

    Returns an error message string if validation fails, or ``None`` if the
    path is safe.  Uses ``Path.resolve()`` to follow symlinks and normalize
    ``..`` components.

    Usage::

        error = validate_within_dir(user_path, allowed_root)
        if error:
            return json.dumps({"error": error})
    """
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
        resolved.relative_to(root_resolved)
    except (ValueError, OSError) as exc:
        return f"Path escapes allowed directory: {exc}"
    return None


def has_traversal_component(path_str: str) -> bool:
    """Return True if *path_str* contains ``..`` traversal components.

    Quick check for obvious traversal attempts before doing full resolution.
    """
    parts = Path(path_str).parts
    return ".." in parts


def validate_local_image_file(path_str: str) -> tuple[Optional[Path], Optional[str], Optional[str]]:
    """Validate a local filesystem path for use as an image source.

    Resolves symlinks before forbidden-path checks so a benign-looking path
    cannot point at sensitive files after ``resolve()``.

    Returns ``(resolved_path, error_message, error_type)``.  On success the
    error fields are ``None``; on failure ``resolved_path`` is ``None``.
    """
    raw = (path_str or "").strip()
    if not raw:
        return None, "source_image must be a non-empty file path", "invalid_argument"
    try:
        path = Path(raw).expanduser().resolve()
    except OSError as exc:
        return None, f"source_image path invalid: {exc}", "invalid_argument"
    if is_forbidden_path(str(path)):
        return None, f"Refusing to read sensitive path as source image: {raw}", "forbidden_path"
    if not path.is_file():
        return None, f"source_image file not found: {raw}", "invalid_argument"
    return path, None, None
