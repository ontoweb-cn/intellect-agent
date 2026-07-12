"""Fail-closed membership stubs and gateway gate honesty."""

from __future__ import annotations

from agent.membership import MembershipStore, ProjectDB, is_members_enabled


def test_is_members_enabled_always_false():
    assert is_members_enabled({"members": {"enabled": True}}) is False
    assert is_members_enabled({}) is False


def test_membership_store_list_and_verify_fail_closed():
    store = MembershipStore()
    assert store.list_members() == []
    assert store.list_projects() == []
    assert store.verify_token("imt_anything") is False
    assert store.is_team_member("t1", "m1") is False
    assert store.is_project_member("p1", "m1") is False
    assert store.count_pending_wiki_contributions() == 0
    store.close()


def test_project_db_list_iterable():
    db = ProjectDB()
    projects = db.list_projects()
    assert projects == []
    for _ in projects:
        raise AssertionError("should be empty")
    db.close()


def test_members_config_gates_ignored_when_stubbed(monkeypatch):
    """Stale members.*.enabled yaml must not open gateway slash commands."""
    from intellect_cli.commands import registry as reg
    import intellect_cli.config as cfg_mod

    monkeypatch.setattr(
        cfg_mod,
        "read_raw_config",
        lambda: {
            "members": {
                "enabled": True,
                "teams": {"enabled": True},
                "projects": {"enabled": True},
            },
            "display": {"tool_progress_command": True},
        },
    )
    opened = reg._resolve_config_gates()
    assert "team" not in opened
    assert "teams" not in opened
    assert "project" not in opened
    assert "projects" not in opened
    assert "login" not in opened
    assert "logout" not in opened
    assert "join" not in opened
    assert "join-project" not in opened
    assert "verbose" in opened


def test_member_slash_commanddefs_removed():
    from intellect_cli.commands import (
        GATEWAY_KNOWN_COMMANDS,
        gateway_help_lines,
        resolve_command,
    )

    for name in (
        "team",
        "teams",
        "project",
        "projects",
        "join",
        "join-project",
        "join_project",
        "login",
        "logout",
    ):
        assert resolve_command(name) is None
        assert name not in GATEWAY_KNOWN_COMMANDS
    help_text = "\n".join(gateway_help_lines())
    for needle in ("/team", "/teams", "/project", "/projects", "/login", "/logout", "/join"):
        assert needle not in help_text
