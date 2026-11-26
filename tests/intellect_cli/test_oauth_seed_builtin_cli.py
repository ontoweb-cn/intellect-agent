"""`intellect oauth seed-builtin` must seed the real state.db."""

from types import SimpleNamespace

from intellect_state import SessionDB


def test_seed_builtin_cli_seeds_state_db(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))

    # Start from an initialized but empty catalog so the command itself is
    # responsible for inserting the rows.
    db = SessionDB(tmp_path / "state.db")
    try:
        db._conn.execute("DELETE FROM oauth_providers")
        db._conn.commit()
    finally:
        db.close()

    from intellect_cli.main import cmd_oauth

    rc = cmd_oauth(SimpleNamespace(oauth_command="seed-builtin", refresh_metadata=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "providers" in out

    db = SessionDB(tmp_path / "state.db")
    try:
        row = db._conn.execute(
            "SELECT COUNT(*) AS n FROM oauth_providers WHERE is_builtin=1"
        ).fetchone()
    finally:
        db.close()
    assert row["n"] >= 15
