"""Builtin catalog credential_fields index."""

from __future__ import annotations


def test_credential_fields_for_wecom():
    from agent.oauth.builtin_catalog import credential_fields_for

    fields = credential_fields_for("wecom")
    keys = [f["key"] for f in fields]
    assert keys == ["corp_id", "agent_id", "client_secret"]


def test_credential_fields_alias_feishu_lark():
    from agent.oauth.builtin_catalog import credential_fields_for

    assert credential_fields_for("lark") == credential_fields_for("feishu")
