"""Anchored workspace I/O — strict containment + leaf O_NOFOLLOW (W7 S1–S3).

Policy:
- Final canonical path MUST be under root (strict relative_to).
- In-root symlinks are allowed (resolve lands inside root).
- Escape via in-tree symlink → outside target is REJECTED.
- Open uses post-resolve path with O_NOFOLLOW as a leaf TOCTOU guard.
- Not a full directory-fd openat walk (explicit W7 non-goal).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO, TextIO, Union

_OPEN_FLAGS_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


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


def open_under_root(
    root: Path,
    requested: str,
    flags: int,
    mode: int = 0o666,
) -> int:
    """``os.open`` under root with leaf ``O_NOFOLLOW`` when available."""
    path = resolve_under_root(root, requested)
    if not _OPEN_FLAGS_NOFOLLOW and path.is_symlink():
        # Windows / platforms without O_NOFOLLOW: refuse unresolved symlink leaf.
        raise ValueError(f"Symlink open blocked: {requested}")
    open_flags = flags | _OPEN_FLAGS_NOFOLLOW
    try:
        return os.open(path, open_flags, mode)
    except OSError as exc:
        if path.is_symlink():
            raise ValueError(f"Symlink open blocked: {requested}") from exc
        raise


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
