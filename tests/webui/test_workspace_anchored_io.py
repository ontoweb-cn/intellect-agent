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
    read_text_under_root,
    resolve_under_root,
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
