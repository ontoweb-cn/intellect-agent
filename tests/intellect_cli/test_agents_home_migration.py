"""Tests for profiles/ → agents/ on-disk migration and the profiles shim."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def profile_env(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default_home = tmp_path / ".intellect"
    default_home.mkdir(exist_ok=True)
    monkeypatch.setenv("INTELLECT_HOME", str(default_home))
    from intellect_cli.agents_home import reset_legacy_agent_homes_migration_state

    reset_legacy_agent_homes_migration_state()
    return tmp_path


class TestMigrateLegacyAgentHomes:
    def test_moves_profiles_into_agents(self, profile_env):
        from intellect_cli.agents_home import (
            migrate_legacy_agent_homes,
            reset_legacy_agent_homes_migration_state,
        )

        reset_legacy_agent_homes_migration_state()
        root = profile_env / ".intellect"
        legacy = root / "profiles" / "coder"
        legacy.mkdir(parents=True)
        (legacy / "SOUL.md").write_text("hi")
        moved = migrate_legacy_agent_homes()
        assert moved == ["coder"]
        dest = root / "agents" / "coder"
        assert dest.is_dir()
        assert (dest / "SOUL.md").read_text() == "hi"
        assert not legacy.exists()
        # Empty legacy root removed
        assert not (root / "profiles").exists()

    def test_skips_when_agents_dest_exists(self, profile_env):
        from intellect_cli.agents_home import migrate_legacy_agent_homes

        root = profile_env / ".intellect"
        (root / "agents" / "coder").mkdir(parents=True)
        (root / "agents" / "coder" / "from_agents").write_text("a")
        legacy = root / "profiles" / "coder"
        legacy.mkdir(parents=True)
        (legacy / "from_profiles").write_text("p")
        moved = migrate_legacy_agent_homes()
        assert moved == []
        assert (root / "agents" / "coder" / "from_agents").exists()
        assert legacy.is_dir()
        assert (legacy / "from_profiles").exists()

    def test_promotes_active_profile_sticky(self, profile_env):
        from intellect_cli.agents_home import migrate_legacy_agent_homes

        root = profile_env / ".intellect"
        (root / "active_profile").write_text("coder\n")
        migrate_legacy_agent_homes()
        assert (root / "active_agent").read_text() == "coder\n"
        assert not (root / "active_profile").exists()

    def test_updates_intellect_home_env_when_current_moved(self, profile_env, monkeypatch):
        from intellect_cli.agents_home import migrate_legacy_agent_homes

        root = profile_env / ".intellect"
        legacy = root / "profiles" / "coder"
        legacy.mkdir(parents=True)
        monkeypatch.setenv("INTELLECT_HOME", str(legacy))
        migrate_legacy_agent_homes()
        import os

        assert Path(os.environ["INTELLECT_HOME"]) == root / "agents" / "coder"

    def test_ensure_runs_once(self, profile_env):
        from intellect_cli.agents_home import (
            ensure_legacy_agent_homes_migrated,
            reset_legacy_agent_homes_migration_state,
        )

        reset_legacy_agent_homes_migration_state()
        root = profile_env / ".intellect"
        (root / "profiles" / "a").mkdir(parents=True)
        first = ensure_legacy_agent_homes_migrated()
        assert first == ["a"]
        (root / "profiles" / "b").mkdir(parents=True)
        second = ensure_legacy_agent_homes_migrated()
        assert second == []
        # Still on disk until force
        assert (root / "profiles" / "b").is_dir()
        third = ensure_legacy_agent_homes_migrated(force=True)
        assert third == ["b"]


class TestProfilesShim:
    def test_profiles_module_is_agents_home(self):
        import intellect_cli.profiles as profiles
        from intellect_cli import agents_home

        assert profiles is agents_home
        assert profiles.get_profile_dir is agents_home.get_profile_dir

    def test_import_from_profiles_still_works(self, profile_env):
        from intellect_cli.profiles import create_profile, get_profile_dir

        path = create_profile("shimbot", no_alias=True, no_skills=True)
        assert path == get_profile_dir("shimbot")
        assert "agents" in str(path)
