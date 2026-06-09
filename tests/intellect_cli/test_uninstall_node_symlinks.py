"""Tests for intellect_cli.uninstall.remove_node_symlinks.

Regression for #34536: the POSIX installer drops node/npm/npx symlinks in
~/.local/bin pointing into $INTELLECT_HOME/node and prepends ~/.local/bin to
PATH, shadowing an existing nvm. Uninstall must remove those symlinks, but
only when they still resolve into the Intellect-managed node dir.
"""

import os
from pathlib import Path

import pytest

import intellect_cli.uninstall as uninstall


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect Path.home() at the home both the installer-symlink target and
    the ~/.local/bin links live under the same temp dir."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".local" / "bin").mkdir(parents=True)
    return home


def _make_intellect_node(intellect_home: Path) -> Path:
    """Create a fake $INTELLECT_HOME/node/bin/{node,npm,npx} tree."""
    node_bin = intellect_home / "node" / "bin"
    node_bin.mkdir(parents=True)
    for name in ("node", "npm", "npx"):
        (node_bin / name).write_text("#!/bin/sh\n")
        (node_bin / name).chmod(0o755)
    return node_bin


def test_removes_symlinks_pointing_into_intellect_node(fake_home):
    intellect_home = fake_home / ".intellect"
    node_bin = _make_intellect_node(intellect_home)
    local_bin = fake_home / ".local" / "bin"

    for name in ("node", "npm", "npx"):
        (local_bin / name).symlink_to(node_bin / name)

    removed = uninstall.remove_node_symlinks(intellect_home)

    assert sorted(p.name for p in removed) == ["node", "npm", "npx"]
    for name in ("node", "npm", "npx"):
        assert not (local_bin / name).exists()
        assert not (local_bin / name).is_symlink()


def test_leaves_unrelated_symlinks_untouched(fake_home):
    """A node symlink the user repointed at nvm must survive uninstall."""
    intellect_home = fake_home / ".intellect"
    _make_intellect_node(intellect_home)
    local_bin = fake_home / ".local" / "bin"

    # Simulate nvm's node living elsewhere; user's ~/.local/bin/node -> nvm.
    nvm_bin = fake_home / ".nvm" / "versions" / "node" / "v20.0.0" / "bin"
    nvm_bin.mkdir(parents=True)
    (nvm_bin / "node").write_text("#!/bin/sh\n")
    (local_bin / "node").symlink_to(nvm_bin / "node")

    removed = uninstall.remove_node_symlinks(intellect_home)

    assert removed == []
    assert (local_bin / "node").is_symlink()
    assert (local_bin / "node").resolve() == (nvm_bin / "node").resolve()


def test_leaves_real_binaries_untouched(fake_home):
    """A real (non-symlink) binary in ~/.local/bin is never deleted."""
    intellect_home = fake_home / ".intellect"
    _make_intellect_node(intellect_home)
    local_bin = fake_home / ".local" / "bin"

    real_node = local_bin / "node"
    real_node.write_text("#!/bin/sh\necho real\n")
    real_node.chmod(0o755)

    removed = uninstall.remove_node_symlinks(intellect_home)

    assert removed == []
    assert real_node.exists()
    assert not real_node.is_symlink()


def test_handles_missing_local_bin(fake_home):
    """No symlinks present -> no-op, no error."""
    intellect_home = fake_home / ".intellect"
    _make_intellect_node(intellect_home)

    assert uninstall.remove_node_symlinks(intellect_home) == []


def test_removes_dangling_symlink_into_intellect_node(fake_home):
    """A link into the Intellect node dir is removed even if the target file is
    already gone (dangling) \u2014 the link still shadows PATH."""
    intellect_home = fake_home / ".intellect"
    node_bin = intellect_home / "node" / "bin"
    node_bin.mkdir(parents=True)
    local_bin = fake_home / ".local" / "bin"

    # Create the symlink, then delete the target so it dangles.
    (local_bin / "node").symlink_to(node_bin / "node")
    assert (local_bin / "node").is_symlink()

    removed = uninstall.remove_node_symlinks(intellect_home)

    assert [p.name for p in removed] == ["node"]
    assert not (local_bin / "node").is_symlink()


def test_only_some_links_present(fake_home):
    """Removes the Intellect links that exist; ignores the ones that don't."""
    intellect_home = fake_home / ".intellect"
    node_bin = _make_intellect_node(intellect_home)
    local_bin = fake_home / ".local" / "bin"

    # Only npm and npx are Intellect-managed; node is a real user binary.
    (local_bin / "npm").symlink_to(node_bin / "npm")
    (local_bin / "npx").symlink_to(node_bin / "npx")
    (local_bin / "node").write_text("#!/bin/sh\n")

    removed = uninstall.remove_node_symlinks(intellect_home)

    assert sorted(p.name for p in removed) == ["npm", "npx"]
    assert (local_bin / "node").exists()
    assert not (local_bin / "npm").is_symlink()
    assert not (local_bin / "npx").is_symlink()
