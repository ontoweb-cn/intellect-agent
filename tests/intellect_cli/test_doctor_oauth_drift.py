"""Doctor auth.json OAuth drift checks (auth-json §9 / item 7)."""

from __future__ import annotations

import json

from agent.oauth.auth_json_drift import check_auth_json_oauth_drift


def test_doctor_flags_unmigrated_auth_json(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    (home / "auth.json").write_text(
        json.dumps(
            {
                "version": 1,
                "providers": {"openai-codex": {"access_token": "tok"}},
                "credential_pool": {},
            }
        ),
        encoding="utf-8",
    )

    issues: list[str] = []
    check_auth_json_oauth_drift(issues)
    assert any("migrate-from-auth-json" in item for item in issues)


def test_doctor_silent_when_no_auth_json(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))

    issues: list[str] = []
    check_auth_json_oauth_drift(issues)
    assert issues == []
