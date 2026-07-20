"""Anchored workspace I/O — strict containment + dir-fd last-hop hardening (W7/W13-O).

Policy (W7 S3):
- Final canonical path MUST be under root (strict relative_to).
- In-root symlinks are allowed (resolve lands inside root).
- Escape via in-tree symlink → outside target is REJECTED.
- Open uses post-resolve path with O_NOFOLLOW as a leaf TOCTOU guard.
- Delete/rename helpers centralize the same containment checks (W12c).
- Not TOCTOU-closed; not a full directory-fd openat walk (residual → W13).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import BinaryIO, TextIO, Union

_OPEN_FLAGS_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_SUPPORTS_DIR_FD = sys.platform != "win32" and hasattr(os, "O_DIRECTORY")


def resolve_under_root(root: Path, requested: str) -> Path:
    """Resolve ``requested`` under ``root``; raise ValueError on escape/traversal."""
    root_r = Path(root).resolve()
    # Empty / bare "." → root itself
    req = "." if requested in ("", None) else str(requested)
    resolved = (root_r / req).resolve()
    try:
        resolved.relative_to(root_r)
    except ValueError as exc:
        raise ValueError(f"Path traversal blocked: {requested}") from exc
    return resolved


def open_resolved_nofollow(path: Path, flags: int, mode: int = 0o666) -> int:
    """Open an already-resolved path with leaf ``O_NOFOLLOW`` when available."""
    if not _OPEN_FLAGS_NOFOLLOW and path.is_symlink():
        raise ValueError(f"Symlink open blocked: {path}")
    open_flags = flags | _OPEN_FLAGS_NOFOLLOW
    try:
        return os.open(path, open_flags, mode)
    except OSError as exc:
        if path.is_symlink():
            raise ValueError(f"Symlink open blocked: {path}") from exc
        raise


def read_bytes_resolved(path: Path) -> bytes:
    """Read bytes from a resolved path via leaf ``O_NOFOLLOW`` open."""
    fd = open_resolved_nofollow(path, os.O_RDONLY)
    with os.fdopen(fd, "rb") as fh:
        return fh.read()


def _open_parent_dir_fd(parent: Path) -> int:
    return os.open(str(parent), os.O_RDONLY | _O_DIRECTORY)


def open_under_root(
    root: Path,
    requested: str,
    flags: int,
    mode: int = 0o666,
) -> int:
    """``os.open`` under root with leaf ``O_NOFOLLOW`` when available."""
    path = resolve_under_root(root, requested)
    return open_resolved_nofollow(path, flags, mode)


def read_bytes_under_root(root: Path, requested: str) -> bytes:
    fd = open_under_root(root, requested, os.O_RDONLY)
    with os.fdopen(fd, "rb") as fh:
        return fh.read()


def read_text_under_root(
    root: Path,
    requested: str,
    *,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> str:
    fd = open_under_root(root, requested, os.O_RDONLY)
    with os.fdopen(fd, "r", encoding=encoding, errors=errors) as fh:
        return fh.read()


def write_bytes_under_root(
    root: Path,
    requested: str,
    data: bytes,
    *,
    append: bool = False,
    create_parents: bool = False,
) -> Path:
    path = resolve_under_root(root, requested)
    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Re-resolve after mkdir so parent escapes cannot sneak in.
        path = resolve_under_root(root, requested)
    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_APPEND if append else os.O_TRUNC
    fd = open_under_root(root, requested, flags)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


def write_text_under_root(
    root: Path,
    requested: str,
    text: str,
    *,
    encoding: str = "utf-8",
    append: bool = False,
    create_parents: bool = False,
) -> Path:
    return write_bytes_under_root(
        root,
        requested,
        text.encode(encoding),
        append=append,
        create_parents=create_parents,
    )


def open_file_under_root(
    root: Path,
    requested: str,
    mode: str = "rb",
) -> Union[BinaryIO, TextIO]:
    """Open a file-like object under root (binary or text)."""
    if "b" not in mode:
        # Text mode: open via fd then wrap.
        flags = os.O_RDONLY
        if "w" in mode or "a" in mode or "+" in mode:
            flags = os.O_RDWR | os.O_CREAT
            if "a" in mode:
                flags |= os.O_APPEND
            elif "w" in mode:
                flags |= os.O_TRUNC
        fd = open_under_root(root, requested, flags)
        return os.fdopen(fd, mode, encoding="utf-8", errors="replace")
    flags = os.O_RDONLY
    if "w" in mode or "a" in mode or "+" in mode:
        flags = os.O_RDWR | os.O_CREAT
        if "a" in mode:
            flags |= os.O_APPEND
        elif "w" in mode:
            flags |= os.O_TRUNC
    fd = open_under_root(root, requested, flags)
    return os.fdopen(fd, mode)


def _reject_workspace_root(root: Path, requested: str, resolved: Path) -> None:
    """Refuse ops whose canonical target is the workspace root itself."""
    root_r = Path(root).resolve()
    if resolved == root_r or requested in ("", None, "."):
        raise ValueError("Refusing to operate on workspace root")


def unlink_under_root(root: Path, requested: str) -> Path:
    """Unlink a non-directory leaf under root (in-root symlinks OK).

    Raises ValueError on traversal / workspace-root / directory targets.
    Raises FileNotFoundError if the path does not exist.
    """
    path = resolve_under_root(root, requested)
    _reject_workspace_root(root, requested, path)
    # Best-effort re-check after resolve (not TOCTOU-closed).
    path = resolve_under_root(root, requested)
    _reject_workspace_root(root, requested, path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {requested}")
    if path.is_symlink():
        # Unlink the link itself; never follow into the target tree.
        path.unlink()
        return path
    if path.is_dir():
        raise ValueError("Set recursive=true to delete directories")
    path.unlink()
    return path


def rmtree_under_root(root: Path, requested: str) -> Path:
    """Recursively delete a directory under root.

    Re-resolves before ``shutil.rmtree`` as a best-effort containment check.
    Not TOCTOU-closed.
    """
    path = resolve_under_root(root, requested)
    _reject_workspace_root(root, requested, path)
    path = resolve_under_root(root, requested)
    _reject_workspace_root(root, requested, path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {requested}")
    if not path.is_dir():
        raise ValueError(f"Not a directory: {requested}")
    shutil.rmtree(path)
    return path


def rename_under_root(root: Path, src_rel: str, dest_rel: str) -> Path:
    """Rename ``src_rel`` to ``dest_rel`` under root (same containment rules).

    Dest must not already exist. Refuses workspace-root src/dest.
    """
    source = resolve_under_root(root, src_rel)
    _reject_workspace_root(root, src_rel, source)
    dest = resolve_under_root(root, dest_rel)
    _reject_workspace_root(root, dest_rel, dest)
    # Best-effort re-resolve before rename.
    source = resolve_under_root(root, src_rel)
    dest = resolve_under_root(root, dest_rel)
    _reject_workspace_root(root, src_rel, source)
    _reject_workspace_root(root, dest_rel, dest)
    if not source.exists():
        raise FileNotFoundError(f"File not found: {src_rel}")
    if dest.exists():
        raise ValueError(f'A file named "{Path(dest_rel).name}" already exists')
    source.rename(dest)
    return dest
