"""Pool metadata must not override encrypted credential fields."""

import json

from agent.oauth.pool_storage import pool_entry_to_credential_dict


def _row(metadata, *, base_url="https://real.example/v1"):
    return {
        "id": "cred-1",
        "label": "label",
        "priority": 0,
        "source": "manual",
        "access_token_encrypted": "",
        "refresh_token_encrypted": "",
        "base_url": base_url,
        "metadata": json.dumps(metadata),
    }


def test_extra_metadata_cannot_override_tokens_or_base_url():
    out = pool_entry_to_credential_dict(
        _row({
            "extra": {
                "access_token": "attacker-token",
                "refresh_token": "attacker-refresh",
                "agent_key": "attacker-agent-key",
                "base_url": "https://attacker.example",
                "portal_base_url": "https://portal.example.com",
            }
        })
    )
    assert out["access_token"] == ""
    assert "refresh_token" not in out
    assert "agent_key" not in out
    assert out["base_url"] == "https://real.example/v1"
    assert out["portal_base_url"] == "https://portal.example.com"


def test_top_level_metadata_cannot_inject_tokens():
    out = pool_entry_to_credential_dict(
        _row({
            "access_token": "attacker-token",
            "refresh_token": "attacker-refresh",
            "portal_base_url": "https://portal.example.com",
        })
    )
    assert out["access_token"] == ""
    assert "refresh_token" not in out
    assert out["portal_base_url"] == "https://portal.example.com"


def test_legacy_base_url_fills_empty_column_only():
    out = pool_entry_to_credential_dict(
        _row({"extra": {"base_url": "https://legacy.example"}}, base_url="")
    )
    assert out["base_url"] == "https://legacy.example"


def test_malformed_metadata_is_ignored():
    row = _row({})
    row["metadata"] = "[]"
    out = pool_entry_to_credential_dict(row)
    assert out["base_url"] == "https://real.example/v1"

    row["metadata"] = '{"extra": "not-a-dict"}'
    out = pool_entry_to_credential_dict(row)
    assert out["base_url"] == "https://real.example/v1"


def test_agent_key_is_not_stored_plaintext_in_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    from agent.oauth.pool_storage import (
        credential_dict_to_pool_row,
        pool_entry_to_credential_dict,
    )

    row = credential_dict_to_pool_row(
        "ontoweb",
        {
            "access_token": "access-token",
            "agent_key": "agent-secret-token",
            "auth_type": "oauth",
            "source": "manual:dashboard_device_code",
        },
        profile_scope="",
    )
    assert "agent-secret-token" not in row["metadata"]
    out = pool_entry_to_credential_dict(row)
    assert out["agent_key"] == "agent-secret-token"
