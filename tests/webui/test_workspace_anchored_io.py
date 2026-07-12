"""W7 workspace anchored I/O — strict containment + leaf open."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))

from api.workspace_io import (  # noqa: E402
    mkdir_under_root,
    open_resolved_nofollow,
    read_bytes_resolved,
    read_text_under_root,
    rename_under_root,
    resolve_under_root,
    rmtree_under_root,
    unlink_under_root,
    write_text_under_root,
)
from api.workspace import safe_resolve_ws  # noqa: E402


def test_resolve_blocks_dotdot(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    with pytest.raises(ValueError, match="traversal"):
        resolve_under_root(root, "../escape.txt")


def test_symlink_escape_outside_rejected(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("leak", encoding="utf-8")
    link = root / "evil"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="traversal"):
        resolve_under_root(root, "evil")
    with pytest.raises(ValueError, match="traversal"):
        safe_resolve_ws(root, "evil")


def test_in_root_symlink_allowed(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    real = root / "real.txt"
    real.write_text("ok", encoding="utf-8")
    link = root / "alias.txt"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks unavailable")
    resolved = resolve_under_root(root, "alias.txt")
    assert resolved == real.resolve()
    assert read_text_under_root(root, "alias.txt") == "ok"


def test_write_read_round_trip(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    write_text_under_root(root, "nested/a.txt", "hello", create_parents=True)
    assert read_text_under_root(root, "nested/a.txt") == "hello"


def test_open_nofollow_flag_used_when_available(tmp_path: Path, monkeypatch):
    """When O_NOFOLLOW exists, open_under_root must pass it to os.open."""
    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("O_NOFOLLOW unavailable")
    root = tmp_path / "ws"
    root.mkdir()
    (root / "f.txt").write_text("x", encoding="utf-8")
    seen = {}

    real_open = os.open

    def _spy(path, flags, mode=0o666):
        seen["flags"] = flags
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", _spy)
    from api.workspace_io import open_under_root

    fd = open_under_root(root, "f.txt", os.O_RDONLY)
    os.close(fd)
    assert seen["flags"] & os.O_NOFOLLOW


def test_unlink_blocks_dotdot(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    with pytest.raises(ValueError, match="traversal"):
        unlink_under_root(root, "../escape.txt")


def test_unlink_symlink_escape_outside_rejected(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("leak", encoding="utf-8")
    link = root / "evil"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="traversal"):
        unlink_under_root(root, "evil")
    assert outside.read_text(encoding="utf-8") == "leak"


def test_unlink_refuses_workspace_root(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    for rel in ("", ".", "nested/.."):
        with pytest.raises(ValueError, match="workspace root"):
            unlink_under_root(root, rel)


def test_unlink_dir_without_recursive_raises(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "d").mkdir()
    with pytest.raises(ValueError, match="recursive"):
        unlink_under_root(root, "d")
    assert (root / "d").is_dir()


def test_rmtree_recursive_dir_ok(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    nested = root / "d" / "nested"
    nested.mkdir(parents=True)
    (nested / "f.txt").write_text("x", encoding="utf-8")
    rmtree_under_root(root, "d")
    assert not (root / "d").exists()


def test_rmtree_refuses_workspace_root(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    with pytest.raises(ValueError, match="workspace root"):
        rmtree_under_root(root, ".")


def test_rename_same_dir_file_ok(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("hi", encoding="utf-8")
    dest = rename_under_root(root, "a.txt", "b.txt")
    assert dest.name == "b.txt"
    assert not (root / "a.txt").exists()
    assert (root / "b.txt").read_text(encoding="utf-8") == "hi"


def test_rename_nonempty_dir_same_parent_ok(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    src = root / "olddir"
    src.mkdir()
    (src / "child.txt").write_text("c", encoding="utf-8")
    rename_under_root(root, "olddir", "newdir")
    assert not src.exists()
    assert (root / "newdir" / "child.txt").read_text(encoding="utf-8") == "c"


def test_rename_dest_exists_rejected(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "b.txt").write_text("b", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        rename_under_root(root, "a.txt", "b.txt")


def test_rename_refuses_workspace_root(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    with pytest.raises(ValueError, match="workspace root"):
        rename_under_root(root, ".", "elsewhere")
    with pytest.raises(ValueError, match="workspace root"):
        rename_under_root(root, "a.txt", ".")


def test_rename_blocks_dotdot(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    with pytest.raises(ValueError, match="traversal"):
        rename_under_root(root, "a.txt", "../outside.txt")


def test_unlink_nested_file_ok(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    (nested / "a.txt").write_text("x", encoding="utf-8")
    unlink_under_root(root, "nested/a.txt")
    assert not (nested / "a.txt").exists()
    assert nested.is_dir()


def test_rename_nested_file_ok(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    (nested / "a.txt").write_text("hi", encoding="utf-8")
    dest = rename_under_root(root, "nested/a.txt", "nested/b.txt")
    assert dest.name == "b.txt"
    assert not (nested / "a.txt").exists()
    assert (nested / "b.txt").read_text(encoding="utf-8") == "hi"


def test_mkdir_nested_path_ok(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "parent").mkdir()
    created = mkdir_under_root(root, "parent/child/grand", parents=True)
    assert created.is_dir()
    assert (root / "parent" / "child" / "grand").is_dir()


def test_mkdir_existing_raises(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "exists").mkdir()
    with pytest.raises(FileExistsError):
        mkdir_under_root(root, "exists")


def test_read_bytes_resolved_nofollow(tmp_path: Path, monkeypatch):
    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("O_NOFOLLOW unavailable")
    root = tmp_path / "ws"
    root.mkdir()
    target = root / "f.txt"
    target.write_text("payload", encoding="utf-8")
    seen = {}
    real_open = os.open

    def _spy(path, flags, mode=0o666):
        seen["flags"] = flags
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", _spy)
    assert read_bytes_resolved(target) == b"payload"
    assert seen["flags"] & os.O_NOFOLLOW


def test_open_resolved_nofollow_after_in_root_symlink_resolve(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    real = root / "real.txt"
    real.write_text("via-link", encoding="utf-8")
    link = root / "alias.txt"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks unavailable")
    resolved = resolve_under_root(root, "alias.txt")
    fd = open_resolved_nofollow(resolved, os.O_RDONLY)
    with os.fdopen(fd, "rb") as fh:
        assert fh.read() == b"via-link"


def test_rmtree_nested_via_dir_fd(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    (nested / "c.txt").write_text("x", encoding="utf-8")
    (root / "a" / "d.txt").write_text("y", encoding="utf-8")
    rmtree_under_root(root, "a")
    assert not (root / "a").exists()


def test_rmtree_does_not_follow_dir_symlink(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    real = root / "keep"
    real.mkdir()
    (real / "secret.txt").write_text("keep-me", encoding="utf-8")
    tree = root / "tree"
    tree.mkdir()
    link = tree / "to_keep"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks unavailable")
    (tree / "file.txt").write_text("z", encoding="utf-8")
    rmtree_under_root(root, "tree")
    assert not tree.exists()
    assert (real / "secret.txt").read_text(encoding="utf-8") == "keep-me"


def test_collect_files_under_root_nested(tmp_path: Path):
    from api.workspace_io import collect_files_under_root

    root = tmp_path / "ws"
    root.mkdir()
    (root / "d").mkdir()
    (root / "d" / "a.txt").write_text("aa", encoding="utf-8")
    (root / "d" / "sub").mkdir()
    (root / "d" / "sub" / "b.txt").write_text("bbb", encoding="utf-8")
    files, total, hit = collect_files_under_root(root, "d", max_bytes=10_000, max_files=100)
    assert hit is None
    arcs = sorted(a for _, a in files)
    assert arcs == ["a.txt", "sub/b.txt"]
    assert total == 5


def test_collect_skips_escape_symlink_file(tmp_path: Path):
    from api.workspace_io import collect_files_under_root

    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("leak", encoding="utf-8")
    (root / "d").mkdir()
    link = root / "d" / "evil.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    (root / "d" / "ok.txt").write_text("ok", encoding="utf-8")
    files, _total, hit = collect_files_under_root(root, "d", max_bytes=10_000, max_files=100)
    assert hit is None
    assert [a for _, a in files] == ["ok.txt"]


def test_list_names_under_root(tmp_path: Path):
    from api.workspace_io import list_names_under_root
    from api.workspace import list_dir

    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("1", encoding="utf-8")
    (root / "b").mkdir()
    names = set(list_names_under_root(root, "."))
    assert names == {"a.txt", "b"}
    entries = list_dir(root, ".")
    assert {e["name"] for e in entries} == {"a.txt", "b"}
