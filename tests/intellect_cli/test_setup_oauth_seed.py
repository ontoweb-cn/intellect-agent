"""Setup wizard seeds built-in OAuth providers into state.db."""

import pytest


@pytest.mark.no_isolate
def test_setup_seeds_oauth_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    from intellect_cli.setup import _seed_oauth_builtin_catalog

    stats = _seed_oauth_builtin_catalog()
    assert stats.get("total", 0) >= 15

    from intellect_state import SessionDB

    db = SessionDB(tmp_path / "state.db")
    try:
        for pid in ("feishu", "wecom", "dingtalk"):
            row = db._conn.execute(
                "SELECT id, is_builtin FROM oauth_providers WHERE id=?",
                (pid,),
            ).fetchone()
            assert row is not None, pid
            assert row["is_builtin"] == 1
        feishu = db._conn.execute(
            "SELECT auth_flow FROM oauth_providers WHERE id='feishu'"
        ).fetchone()
        assert feishu["auth_flow"] == "oauth2_feishu"
        marker = tmp_path / ".oauth_builtin_catalog_seeded"
        assert marker.is_file()
    finally:
        db.close()
