"""Tests for build_session_key with project/member/team extensions."""
from __future__ import annotations

import pytest
from gateway.session import SessionSource, build_session_key
from gateway.platforms.base import Platform


class TestBuildSessionKeyProject:
    """Tests for the project_id extension on build_session_key."""

    @pytest.fixture
    def dm_source(self):
        return SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="12345",
            chat_type="dm",
            user_id="67890",
            user_name="testuser",
        )

    @pytest.fixture
    def group_source(self):
        return SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="-1001234567890",
            chat_type="group",
            chat_name="Test Group",
            user_id="67890",
        )

    # ── Backward compatibility (no extensions) ────────────────────────────

    def test_dm_key_unchanged(self, dm_source):
        """Without project_id, DM keys are identical to before."""
        key = build_session_key(dm_source)
        assert key == "agent:main:telegram:dm:12345"
        assert ":project:" not in key
        assert ":member:" not in key

    def test_group_key_unchanged(self, group_source):
        """Without project_id, group keys are identical to before."""
        key = build_session_key(group_source, group_sessions_per_user=True)
        assert key == "agent:main:telegram:group:-1001234567890:67890"
        assert ":project:" not in key

    # ── Project extension ─────────────────────────────────────────────────

    def test_project_suffix_appended(self, dm_source):
        """project_id is appended to the key."""
        key = build_session_key(dm_source, project_id="web-app")
        assert key.endswith(":project:web-app")
        assert key == "agent:main:telegram:dm:12345:project:web-app"

    def test_project_suffix_with_group(self, group_source):
        """project_id works with group keys too."""
        key = build_session_key(group_source, project_id="mobile-app")
        assert key.endswith(":project:mobile-app")

    def test_project_none_not_appended(self, dm_source):
        """None project_id should not affect the key."""
        key = build_session_key(dm_source, project_id=None)
        assert ":project:" not in key

    def test_project_empty_string_not_appended(self, dm_source):
        """Empty string project_id should not be appended (falsy check)."""
        key = build_session_key(dm_source, project_id="")
        assert ":project:" not in key

    # ── Member extension ──────────────────────────────────────────────────

    def test_member_suffix(self, dm_source):
        key = build_session_key(dm_source, member_id="alice")
        assert key.endswith(":member:alice")
        assert key == "agent:main:telegram:dm:12345:member:alice"

    # ── Team extension ────────────────────────────────────────────────────

    def test_team_suffix(self, dm_source):
        key = build_session_key(dm_source, team_id="kitchen")
        assert key.endswith(":team:kitchen")
        assert key == "agent:main:telegram:dm:12345:team:kitchen"

    # ── Combined extensions ───────────────────────────────────────────────

    def test_all_extensions_combined(self, dm_source):
        """Order: member, team, project."""
        key = build_session_key(
            dm_source,
            member_id="alice",
            team_id="kitchen",
            project_id="web-app",
        )
        assert key.endswith(":member:alice:team:kitchen:project:web-app")
        assert key == "agent:main:telegram:dm:12345:member:alice:team:kitchen:project:web-app"

    def test_member_and_project(self, dm_source):
        key = build_session_key(dm_source, member_id="alice", project_id="web-app")
        assert key.endswith(":member:alice:project:web-app")

    def test_team_and_project(self, group_source):
        key = build_session_key(group_source, team_id="kitchen", project_id="web-app")
        assert key.endswith(":team:kitchen:project:web-app")

    # ── Thread isolation still works ──────────────────────────────────────

    def test_thread_with_project(self):
        source = SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="111111",
            chat_type="group",
            thread_id="topic-42",
            user_id="99999",
        )
        # Shared thread (default): no user isolation
        key_shared = build_session_key(source, project_id="web-app")
        assert "topic-42" in key_shared
        assert ":project:web-app" in key_shared
        assert "99999" not in key_shared  # shared thread — user_id not appended

        # Per-user thread
        key_per_user = build_session_key(
            source,
            thread_sessions_per_user=True,
            project_id="web-app",
        )
        assert "topic-42" in key_per_user
        assert "99999" in key_per_user  # per-user isolation
        assert ":project:web-app" in key_per_user

    # ── Key stability ─────────────────────────────────────────────────────

    def test_key_is_deterministic(self, dm_source):
        """Same inputs produce the same key."""
        key1 = build_session_key(dm_source, member_id="alice", project_id="web-app")
        key2 = build_session_key(dm_source, member_id="alice", project_id="web-app")
        assert key1 == key2

    def test_different_projects_produce_different_keys(self, dm_source):
        key_a = build_session_key(dm_source, project_id="project-a")
        key_b = build_session_key(dm_source, project_id="project-b")
        assert key_a != key_b

    # ── WhatsApp canonicalization still works ─────────────────────────────

    def test_whatsapp_with_project(self):
        source = SessionSource(
            platform=Platform.WHATSAPP,
            chat_id="12345@s.whatsapp.net",
            chat_type="dm",
            user_id="67890@s.whatsapp.net",
        )
        key = build_session_key(source, project_id="web-app")
        assert key.startswith("agent:main:whatsapp:dm:")
        assert key.endswith(":project:web-app")
