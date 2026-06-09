"""Tests for ``intellect doctor --storage``."""

from __future__ import annotations

from argparse import Namespace

import pytest


def test_doctor_storage_sqlite_backend(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / ".intellect"))
    home = tmp_path / ".intellect"
    home.mkdir()
    (home / "config.yaml").write_text(
        "storage:\n  backend: sqlite\nmembers:\n  enabled: true\n",
        encoding="utf-8",
    )
    import intellect_cli.doctor as doctor_mod

    monkeypatch.setenv("INTELLECT_HOME", str(home))
    monkeypatch.setattr(doctor_mod, "INTELLECT_HOME", home)
    monkeypatch.setattr(doctor_mod, "_DHH", str(home))

    rc = doctor_mod.run_doctor_storage()
    out = capsys.readouterr().out
    assert rc == 0
    assert "storage.backend = 'sqlite'" in out


def test_doctor_storage_pg_warns_active_state_db(tmp_path, monkeypatch, capsys):
    home = tmp_path / ".intellect"
    home.mkdir()
    (home / "config.yaml").write_text(
        "storage:\n  backend: postgresql\n"
        "  postgresql:\n    dsn: postgresql://localhost/test\n"
        "members:\n  enabled: true\n",
        encoding="utf-8",
    )
    (home / "state.db").write_bytes(b"sqlite")
    import intellect_cli.doctor as doctor_mod

    monkeypatch.setenv("INTELLECT_HOME", str(home))
    monkeypatch.setattr(doctor_mod, "INTELLECT_HOME", home)
    monkeypatch.setattr(doctor_mod, "_DHH", str(home))

    monkeypatch.setattr(
        "agent.storage.postgres_backend.PGStorageBackend.initialize",
        lambda self: None,
    )
    monkeypatch.setattr(
        "agent.storage.postgres_backend.PGStorageBackend.fetchone",
        lambda self, sql: {"ok": 1},
    )
    monkeypatch.setattr(
        "agent.storage.postgres_backend.PGStorageBackend.close",
        lambda self: None,
    )

    class _FakeSessionDB:
        def __init__(self):
            self._conn = self

        def execute(self, sql):
            class _Row:
                def fetchone(inner):
                    if "oauth_providers" in sql:
                        return {"c": 0}
                    if "oauth_tokens" in sql:
                        return {"c": 0}
                    if "members" in sql:
                        return {"c": 0}
                    return {"c": 0}

            return _Row()

        def close(self):
            pass

    monkeypatch.setattr(
        "intellect_state.SessionDB",
        _FakeSessionDB,
    )

    rc = doctor_mod.run_doctor_storage()
    out = capsys.readouterr().out
    assert rc != 0
    assert "Dual-write / split-brain" in out
    assert "Dual-write risk" in out or "Split-brain risk" in out
