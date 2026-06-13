"""Shared path validation helpers for tool implementations.

Extracts the ``resolve() + relative_to()`` and ``..`` traversal check
patterns previously duplicated across skill_manager_tool, skills_tool,
skills_hub, cronjob_tools, and credential_files.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Rust-accelerated path safety ─────────────────────────────────────────────
try:
    from intellect_community_core import is_forbidden_path_rs as _rust_is_forbidden_path
    _HAS_RUST_PATH = True
except ImportError:
    _HAS_RUST_PATH = False


def is_forbidden_path(path: str) -> Optional[str]:
    """Check if a file path targets a sensitive system location.

    Returns a reason string if the path is forbidden, ``None`` if safe.
    Uses Rust acceleration when available, falls back to Python.
    """
    if _HAS_RUST_PATH:
        return _rust_is_forbidden_path(path)
    return _is_forbidden_path_py(path)


def _is_forbidden_path_py(path: str) -> Optional[str]:
    """Pure Python fallback for ``is_forbidden_path``."""
    lower = path.lower()

    # Always-blocked system paths
    always_blocked = [
        ("/etc/shadow", "system shadow file"),
        ("/etc/gshadow", "system group shadow file"),
        ("/etc/master.passwd", "system master passwd"),
        ("/etc/sudoers", "sudoers configuration"),
        ("/etc/ssh/ssh_host_", "SSH host private key"),
        ("/proc/kcore", "kernel memory image"),
        ("/proc/sysrq-trigger", "kernel sysrq trigger"),
        ("/dev/mem", "physical memory device"),
        ("/dev/kmem", "kernel memory device"),
    ]
    for pattern, reason in always_blocked:
        if pattern in lower:
            return reason

    # Sensitive user directories
    sensitive_dirs = [
        ".ssh/", ".gnupg/", ".aws/", ".kube/",
        ".docker/config", ".intellect/.env",
        ".netrc", ".pgpass", ".npmrc", ".pypirc",
    ]
    for pattern in sensitive_dirs:
        if pattern in lower:
            return f"sensitive directory: {pattern}"

    # Private key files in sensitive contexts
    if any(lower.endswith(ext) for ext in (".pem", ".key", ".p12", ".pfx", ".jks")):
        if path.startswith("/") or "/.ssh/" in lower or "/.gnupg/" in lower:
            return "private key file"

    return None


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
