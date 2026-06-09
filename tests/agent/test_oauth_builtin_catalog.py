"""Tests for built-in OAuth provider catalog loading and DB seeding."""

import pytest


class TestBuiltinCatalogLoad:
    def test_load_catalog(self):
        from agent.oauth.builtin_catalog import load_builtin_catalog

        data = load_builtin_catalog()
        assert data["schema_version"] == 1
        assert data["catalog_id"] == "intellect-oauth-builtin-v1"
        ids = [p["id"] for p in data["providers"]]
        assert "github" in ids
        assert "wecom" in ids
        assert "dingtalk" in ids
        assert "feishu" in ids

    def test_all_icon_paths_exist(self):
        from agent.oauth.builtin_catalog import catalog_dir, load_builtin_catalog

        base = catalog_dir()
        for record in load_builtin_catalog()["providers"]:
            path = (record.get("icon") or {}).get("path")
            if path:
                assert (base / path).is_file(), f"missing icon for {record['id']}: {path}"


class TestBuiltinCatalogSeed:
    def test_seed_inserts_enterprise_providers(self, tmp_path, monkeypatch):
        monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
        from intellect_state import SessionDB

        db = SessionDB(tmp_path / "state.db")
        try:
            rows = db._conn.execute(
                "SELECT id FROM oauth_providers WHERE id IN ('wecom', 'dingtalk', 'feishu')"
            ).fetchall()
            assert {r["id"] for r in rows} == {"wecom", "dingtalk", "feishu"}
        finally:
            db.close()

    def test_seed_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
        from agent.oauth.builtin_catalog import seed_builtin_oauth_providers
        from intellect_state import SessionDB

        db = SessionDB(tmp_path / "state.db")
        try:
            cur = db._conn.cursor()
            seed_builtin_oauth_providers(cur)
            db._conn.commit()
            second = seed_builtin_oauth_providers(cur)
            assert second["inserted"] == 0
            count = db._conn.execute(
                "SELECT COUNT(*) AS n FROM oauth_providers WHERE is_builtin=1"
            ).fetchone()["n"]
            assert count >= 15
        finally:
            db.close()

    def test_seed_preserves_enabled_on_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
        import time
        from agent.oauth.builtin_catalog import seed_builtin_oauth_providers
        from intellect_state import SessionDB

        db = SessionDB(tmp_path / "state.db")
        now = time.time()
        try:
            db._conn.execute(
                "UPDATE oauth_providers SET enabled=0, client_id='keep-me' WHERE id='github'"
            )
            db._conn.commit()
            seed_builtin_oauth_providers(db._conn.cursor())
            db._conn.commit()
            row = db._conn.execute(
                "SELECT enabled, client_id FROM oauth_providers WHERE id='github'"
            ).fetchone()
            assert row["enabled"] == 0
            assert row["client_id"] == "keep-me"
        finally:
            db.close()

    def test_refresh_metadata_updates_urls(self, tmp_path, monkeypatch):
        monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
        from agent.oauth.builtin_catalog import seed_builtin_oauth_providers
        from intellect_state import SessionDB

        db = SessionDB(tmp_path / "state.db")
        try:
            db._conn.execute(
                "UPDATE oauth_providers SET authorize_url='http://stale' WHERE id='github'"
            )
            db._conn.commit()
            seed_builtin_oauth_providers(
                db._conn.cursor(), force_metadata=True
            )
            db._conn.commit()
            row = db._conn.execute(
                "SELECT authorize_url FROM oauth_providers WHERE id='github'"
            ).fetchone()
            assert "github.com" in row["authorize_url"]
        finally:
            db.close()

    def test_github_has_logo_svg(self, tmp_path, monkeypatch):
        monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
        from intellect_state import SessionDB

        db = SessionDB(tmp_path / "state.db")
        try:
            row = db._conn.execute(
                "SELECT logo_svg, logo_type FROM oauth_providers WHERE id='github'"
            ).fetchone()
            assert row["logo_type"] == "svg"
            assert "<path" in (row["logo_svg"] or "")
        finally:
            db.close()
