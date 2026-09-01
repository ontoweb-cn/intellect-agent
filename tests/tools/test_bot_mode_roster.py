"""Tests for the Bot Mode roster (BT-01 / B2-1) and DM transport."""

import json

import pytest

from tools import bot_mode_roster as bmr
from tools.bot_mode_dm import (
    BOT_CHAT_SESSION_ID,
    dm_temp_dir,
    ensure_bot_chat_session,
    write_dm_query_file,
)


@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    """Root home + profiles/ layout, roster file under the ROOT home."""
    home_root = tmp_path / "root"
    (home_root / ".intellect").mkdir(parents=True)
    (home_root / ".intellect" / "profiles").mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home_root)
    monkeypatch.setenv("INTELLECT_HOME", str(home_root / ".intellect"))
    monkeypatch.setenv("HOME", str(home_root))
    return home_root / ".intellect"


def _profile(root_home, name):
    pdir = root_home / "profiles" / name
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir


def test_build_roster_defaults_and_liveness(profile_env, monkeypatch):
    _profile(profile_env, "alpha")
    monkeypatch.setattr(
        bmr, "_probe_online",
        lambda home, timeout=1.0: home.name == "alpha",
    )
    roster = {e["name"]: e for e in bmr.build_roster()}
    assert set(roster) == {"default", "alpha"}
    assert roster["alpha"]["online"] is True
    assert roster["default"]["online"] is False  # no control socket → offline


def test_probe_online_false_without_socket(profile_env):
    _profile(profile_env, "alpha")
    assert bmr._probe_online(profile_env / "profiles" / "alpha") is False


def test_persist_roster_writes_and_change_detects(profile_env):
    _profile(profile_env, "alpha")
    path = bmr.persist_roster(force=True)
    assert path is not None and path.exists()
    data = json.loads(path.read_text())
    assert {e["name"] for e in data["roster"]} == {"default", "alpha"}

    # Unchanged roster → no rewrite (mtime stable), force → rewrite.
    before = path.stat().st_mtime_ns
    assert bmr.persist_roster() is None
    assert path.stat().st_mtime_ns == before
    assert bmr.persist_roster(force=True) is not None


def test_load_roster_cached_roundtrip(profile_env):
    _profile(profile_env, "alpha")
    assert bmr.load_roster_cached() is None  # absent → None, no crash
    bmr.persist_roster(force=True)
    cached = bmr.load_roster_cached()
    assert cached and {e["name"] for e in cached["roster"]} == {
        "default", "alpha",
    }


def test_protocol_section_lists_roster_and_caches(profile_env, monkeypatch):
    _profile(profile_env, "alpha")
    calls = []
    monkeypatch.setattr(
        bmr, "build_roster",
        lambda: (calls.append(1) or [
            {"name": "default", "home": "/d", "online": True, "model": ""},
            {"name": "alpha", "home": "/a", "online": False, "model": "m1"},
        ]),
    )
    section_a = bmr.bot_chat_protocol_section("default", profile_env, ["t1"])
    section_b = bmr.bot_chat_protocol_section("default", profile_env, ["t1"])
    assert section_a == section_b  # (pid, home) cache — byte-identical
    assert calls  # built from a live roster read

    assert "## Bot Mode" in section_a
    assert "- default [(you)]" in section_a
    assert "- alpha [offline] model=m1" in section_a
    assert "message_agent" in section_a


def test_capability_epoch_tracks_tool_surface():
    base = bmr.capability_epoch(["terminal", "read_file"])
    assert base == bmr.capability_epoch(["read_file", "terminal"])  # order-free
    assert len(base) == 12
    assert base != bmr.capability_epoch(["terminal", "read_file", "web_search"])


# ── DM transport ────────────────────────────────────────────────────────

def test_ensure_bot_chat_session_is_idempotent_and_titled(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    sid1 = ensure_bot_chat_session(target)
    sid2 = ensure_bot_chat_session(target)
    assert sid1 == sid2 == BOT_CHAT_SESSION_ID

    from intellect_state import SessionDB

    db = SessionDB(db_path=target / "state.db")
    assert db.resolve_session_by_title("Bot Chat") == BOT_CHAT_SESSION_ID
    # Row is reusable — no duplicate sessions across concurrent deliverers.
    assert db.get_session_by_title("Bot Chat")["id"] == BOT_CHAT_SESSION_ID


def test_write_dm_query_file_permissions_and_content(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    (tmp_path / "cache").mkdir()
    path = write_dm_query_file("secret body 🤖")
    try:
        assert not (path.stat().st_mode & 0o077)  # 0o600 — no group/other
        assert not (path.parent.stat().st_mode & 0o077)  # 0o700 dir
        assert path.read_text(encoding="utf-8") == "secret body 🤖"
    finally:
        path.unlink(missing_ok=True)


def test_dm_temp_dir_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    d = dm_temp_dir()
    assert d.exists()
    assert not (d.stat().st_mode & 0o077)  # 0o700
