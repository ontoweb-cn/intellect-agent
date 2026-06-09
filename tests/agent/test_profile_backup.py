"""T6 profile backup tarball tests."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from agent.storage.profile_backup import create_profile_backup


@pytest.fixture
def profile_home(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    (home / "config.yaml").write_text("storage:\n  backend: sqlite\n", encoding="utf-8")
    (home / "state.db").write_bytes(b"sqlite-placeholder")
    webui = home / "webui" / "sessions"
    webui.mkdir(parents=True)
    (webui / "sess1.json").write_text('{"title":"hi"}', encoding="utf-8")
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    monkeypatch.setenv("INTELLECT_CONFIG_PATH", str(home / "config.yaml"))
    return home


def test_create_profile_backup_sqlite_includes_manifest_and_sessions(profile_home, tmp_path):
    out = tmp_path / "backup.tar.gz"
    report = create_profile_backup(
        intellect_home=profile_home,
        config={"storage": {"backend": "sqlite"}},
        output=out,
    )
    assert report.archive_path == out
    assert out.is_file()
    assert any(f.archive_path == "state.db" for f in report.files)
    assert any("webui/sessions/sess1.json" in f.archive_path for f in report.files)

    with tarfile.open(out, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))
    assert manifest["format"] == "intellect-profile-backup-v1"
    assert manifest["storage_backend"] == "sqlite"
    paths = {f["path"] for f in manifest["files"]}
    assert "state.db" in paths
    assert "webui/sessions/sess1.json" in paths


def test_restore_profile_backup_dry_run(profile_home, tmp_path):
    from agent.storage.profile_backup import create_profile_backup, restore_profile_backup

    out = tmp_path / "backup.tar.gz"
    create_profile_backup(
        intellect_home=profile_home,
        config={"storage": {"backend": "sqlite"}},
        output=out,
    )
    report = restore_profile_backup(out, intellect_home=profile_home / "restored", dry_run=True)
    assert report.dry_run is True
    assert "state.db" in report.restored
    assert "config.yaml" in report.restored
