"""OAuth migrate-from-auth-json tests (auth-json §9 / item 7)."""

from __future__ import annotations

import json

import pytest

from agent.oauth.migrate_from_auth_json import (
    migrate_auth_json_to_db,
    migration_marker_exists,
    migration_marker_path,
    summarize_auth_json_oauth,
    write_migration_marker,
)


def test_summarize_auth_json_oauth_counts_providers():
    auth = {
        "providers": {"openai-codex": {"access_token": "tok"}},
        "credential_pool": {
            "openai-codex": [{"access_token": "a", "runtime_api_key": "k"}],
        },
        "active_provider": "openai-codex",
    }
    summary = summarize_auth_json_oauth(auth)
    assert summary["provider_count"] == 1
    assert summary["pool_entry_count"] == 1
    assert summary["active_provider"] == "openai-codex"


def test_migrate_auth_json_dry_run(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(
        json.dumps(
            {
                "version": 1,
                "providers": {"openai-codex": {"access_token": "legacy"}},
                "credential_pool": {},
            }
        ),
        encoding="utf-8",
    )
    from intellect_state import SessionDB

    db = SessionDB(tmp_path / "state.db")
    try:
        stats = migrate_auth_json_to_db(db, dry_run=True)
        assert stats["dry_run"] is True
        assert stats["summary"]["singleton_providers"] == 1
        assert not migration_marker_exists()
    finally:
        db.close()


@pytest.mark.no_isolate
def test_migration_marker_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    write_migration_marker(3)
    assert migration_marker_exists()
    assert migration_marker_path().is_file()
