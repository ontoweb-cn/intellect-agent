"""Anchored workspace I/O — strict containment + dir-fd hardening (W7/W13-O/W14-A).

Policy (W7 S3):
- Final canonical path MUST be under root (strict relative_to).
- In-root symlinks are allowed (resolve lands inside root).
- Escape via in-tree symlink → outside target is REJECTED.
- Open uses post-resolve path with O_NOFOLLOW as a leaf TOCTOU guard.

Hardening depth (Tier table — source of truth):

| Tier | Depth | Ops | TOCTOU claim | Status |
|------|-------|-----|--------------|--------|
| W7/W12c | resolve + leaf O_NOFOLLOW | read/write/open; unlink/rename (leaf) | not closed | done |
| A | no bare Path.open/read_bytes on serve hot paths | _serve_file_bytes, HTML preview, read_file_content; open/create still path+O_NOFOLLOW | leaf-only | W13-O |
| B | workspace dir-fd + unlinkat/renameat/mkdirat (last hop) | unlink, rename, mkdir | last-hop closed (POSIX); parent-chain residual | W13-O |
| C | root→start 分量链 openat + dir-fd scandir/unlinkat（不跟随目录符号链接） | list names; folder-zip collect; rmtree_under_root | POSIX：这三 ops 的 enumerate/walk/delete 在分量链+树内 walk 上 closed；非「全仓 TOCTOU-closed」；list 元数据与 zip **写出**见下方 residual | W14-A done |

Residuals (explicit):
- ``list_dir`` metadata/sort still uses Path on each name after dir-fd name enum.
- Folder-zip **collect** skips directory symlinks and escape file symlinks; **write**
  should re-resolve + leaf ``O_NOFOLLOW`` read (see routes) — not ``ZipFile.write`` bare path.

Windows residual: when O_DIRECTORY / dir_fd / O_NOFOLLOW are unavailable, fall back
to resolve + containment checks + Path/`os.walk`/`shutil.rmtree` (degraded).

Keep out (not this module): agent ``tools/path_security.py``, ``tools/file_tools.py``,
Rust sandbox, attachment-inbox whole-tree openat, git discard, create last-hop openat.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import BinaryIO, Optional, TextIO, Union

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


def open_root_dir_fd(root: Path) -> int:
    """Open the workspace root directory fd (``O_DIRECTORY``)."""
    root_r = Path(root).resolve()
    return os.open(str(root_r), os.O_RDONLY | _O_DIRECTORY)


def open_dir_fd_under_root(root: Path, requested: str) -> tuple[int, Path]:
    """Open ``requested`` via component-chain ``openat`` from the workspace root.

    Containment uses ``resolve_under_root`` (in-root symlink dirs allowed per W7 S3).
    The open walk uses the *resolved* path components with ``O_NOFOLLOW`` so each
    hop is pinned by directory fd (Tier C A1).

    Returns ``(dir_fd, resolved_path)``. Caller must ``os.close(dir_fd)``.
    """
    if not _SUPPORTS_DIR_FD:
        raise RuntimeError("dir-fd openat unavailable on this platform")
    root_r = Path(root).resolve()
    start = resolve_under_root(root, requested)
    if not start.exists():
        raise FileNotFoundError(f"File not found: {requested}")
    if not start.is_dir():
        raise ValueError(f"Not a directory: {requested}")
    try:
        rel = start.relative_to(root_r)
    except ValueError as exc:
        raise ValueError(f"Path traversal blocked: {requested}") from exc
    parts = () if str(rel) == "." else rel.parts

    fd = open_root_dir_fd(root_r)
    try:
        for part in parts:
            next_fd = os.open(
                part,
                os.O_RDONLY | _O_DIRECTORY | _OPEN_FLAGS_NOFOLLOW,
                dir_fd=fd,
            )
            os.close(fd)
            fd = next_fd
        return fd, start
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _rmtree_via_dir_fd(dir_fd: int) -> None:
    """Empty a directory via scandir + unlinkat/openat; does not remove ``dir_fd`` itself.

    Does not follow directory symlinks (unlink the link instead).
    """
    with os.scandir(dir_fd) as it:
        for entry in it:
            name = entry.name
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                os.unlink(name, dir_fd=dir_fd)
                continue
            child_fd = os.open(
                name,
                os.O_RDONLY | _O_DIRECTORY | _OPEN_FLAGS_NOFOLLOW,
                dir_fd=dir_fd,
            )
            try:
                _rmtree_via_dir_fd(child_fd)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=dir_fd)


def _walk_files_via_dir_fd(
    dir_fd: int,
    start: Path,
    workspace_root: Path,
    arc_prefix: str,
    files: list[tuple[Path, str]],
    total_bytes: int,
    max_bytes: int,
    max_files: int,
) -> tuple[int, Optional[str]]:
    """Append file entries; return (total_bytes, limit_hit)."""
    with os.scandir(dir_fd) as it:
        for entry in it:
            name = entry.name
            arc = f"{arc_prefix}/{name}" if arc_prefix else name
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode):
                # Match os.walk(followlinks=False): directory symlinks are neither
                # descended into nor emitted as archive members.
                try:
                    if entry.is_dir(follow_symlinks=True):
                        continue
                except OSError:
                    continue
                fp = start / Path(arc)
                try:
                    if not fp.resolve().is_relative_to(workspace_root):
                        continue
                except (ValueError, OSError):
                    continue
                try:
                    size = fp.stat().st_size
                except OSError:
                    continue
                if len(files) >= max_files:
                    return total_bytes, "max_files"
                if total_bytes + size > max_bytes:
                    return total_bytes, "max_bytes"
                files.append((fp, arc))
                total_bytes += size
                continue
            if stat.S_ISDIR(st.st_mode):
                child_fd = os.open(
                    name,
                    os.O_RDONLY | _O_DIRECTORY | _OPEN_FLAGS_NOFOLLOW,
                    dir_fd=dir_fd,
                )
                try:
                    total_bytes, hit = _walk_files_via_dir_fd(
                        child_fd,
                        start,
                        workspace_root,
                        arc,
                        files,
                        total_bytes,
                        max_bytes,
                        max_files,
                    )
                finally:
                    os.close(child_fd)
                if hit:
                    return total_bytes, hit
                continue
            # Regular file (or other non-dir): include by size from lstat
            size = st.st_size
            if len(files) >= max_files:
                return total_bytes, "max_files"
            if total_bytes + size > max_bytes:
                return total_bytes, "max_bytes"
            files.append((start / Path(arc), arc))
            total_bytes += size
    return total_bytes, None


def collect_files_under_root(
    root: Path,
    requested: str,
    max_bytes: int,
    max_files: int,
) -> tuple[list[tuple[Path, str]], int, Optional[str]]:
    """Collect files under ``requested`` for zip download (Tier C).

    Returns ``(files, total_bytes, limit_hit)`` where each file is
    ``(filesystem_path, archive_name)``. Does not follow or emit directory
    symlinks. File symlinks are included only when their resolved target stays
    under root. Callers must still open members with leaf ``O_NOFOLLOW`` when
    writing the archive (collect alone is not write-TOCTOU-closed).
    """
    workspace_root = Path(root).resolve()
    files: list[tuple[Path, str]] = []
    if _SUPPORTS_DIR_FD:
        dir_fd, start = open_dir_fd_under_root(root, requested)
        try:
            total, hit = _walk_files_via_dir_fd(
                dir_fd,
                start,
                workspace_root,
                "",
                files,
                0,
                max_bytes,
                max_files,
            )
        finally:
            os.close(dir_fd)
        return files, total, hit

    # Windows / degraded: Path walk with resolve containment (W13-era semantics).
    start = resolve_under_root(root, requested)
    if not start.is_dir():
        raise ValueError(f"Not a directory: {requested}")
    total_bytes = 0
    for walk_root, _dirs, names in os.walk(start, followlinks=False):
        root_path = Path(walk_root)
        try:
            if not root_path.resolve().is_relative_to(workspace_root):
                continue
        except (ValueError, OSError):
            continue
        for name in names:
            fp = root_path / name
            if fp.is_symlink():
                try:
                    if not fp.resolve().is_relative_to(workspace_root):
                        continue
                except (ValueError, OSError):
                    continue
            try:
                size = fp.stat().st_size
            except OSError:
                continue
            if len(files) >= max_files:
                return files, total_bytes, "max_files"
            if total_bytes + size > max_bytes:
                return files, total_bytes, "max_bytes"
            try:
                arcname = str(fp.relative_to(start))
            except ValueError:
                continue
            files.append((fp, arcname))
            total_bytes += size
    return files, total_bytes, None


def list_names_under_root(root: Path, requested: str) -> list[str]:
    """List entry names under ``requested`` via component-chain dir-fd scandir."""
    if _SUPPORTS_DIR_FD:
        dir_fd, _start = open_dir_fd_under_root(root, requested)
        try:
            with os.scandir(dir_fd) as it:
                return [entry.name for entry in it]
        finally:
            os.close(dir_fd)
    path = resolve_under_root(root, requested)
    if not path.is_dir():
        raise FileNotFoundError(f"Not a directory: {requested}")
    return [p.name for p in path.iterdir()]


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
    """Recursively delete a directory under root (Tier C on POSIX).

    Start path is opened via component-chain openat from root (in-root symlink
    dirs allowed per W7 S3 resolve). Contents are removed via dir-fd scandir +
    unlinkat/openat without following dir symlinks.
    """
    path = resolve_under_root(root, requested)
    _reject_workspace_root(root, requested, path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {requested}")
    if path.is_symlink():
        # Resolved start should not be a symlink; if it is, unlink the link only.
        path.unlink()
        return path
    if not path.is_dir():
        raise ValueError(f"Not a directory: {requested}")
    if _SUPPORTS_DIR_FD:
        dir_fd, path = open_dir_fd_under_root(root, requested)
        try:
            _rmtree_via_dir_fd(dir_fd)
        finally:
            os.close(dir_fd)
        root_r = Path(root).resolve()
        parent = path.parent
        if parent == root_r:
            parent_fd = open_root_dir_fd(root_r)
        else:
            parent_rel = str(parent.relative_to(root_r))
            parent_fd, _ = open_dir_fd_under_root(root, parent_rel)
        try:
            os.rmdir(path.name, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        return path
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
