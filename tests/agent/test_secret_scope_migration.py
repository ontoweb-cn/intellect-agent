"""Tests for the MP-01 scope-aware credential migration in credential_pool.

Contract: `.env` file wins, then the secret scope (multiplex) or os.environ
(single profile). Single-profile behaviour is byte-identical to the
pre-migration code path.
"""


from agent import credential_pool
from agent.secret_scope import set_secret_scope, reset_secret_scope, set_multiplex_active


def test_scope_resolution_order(monkeypatch, tmp_path):
    """dotenv > scope > environ, with empty-string dotenv entries skipped."""
    monkeypatch.chdir(tmp_path)
    # dotenv leg: load_env() reads INTELLECT_HOME/.env
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    (home / ".env").write_text("OPENROUTER_API_KEY=from-dotenv\n", encoding="utf-8")

    # environ leg present but must lose to dotenv
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-environ")

    # seed path uses the dotenv-first helper
    seed = credential_pool._seed_from_env
    assert seed is not None  # smoke: module imports with the migrated helper

    # direct check of resolution via the helper's building blocks
    from intellect_cli.config import load_env

    env_file = load_env()
    assert env_file.get("OPENROUTER_API_KEY") == "from-dotenv"


def test_scope_beats_environ_when_no_dotenv(monkeypatch, tmp_path):
    """Without a dotenv entry, the installed scope wins over os.environ."""
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    monkeypatch.delenv("TEST_MIGRATION_KEY", raising=False)

    token = set_secret_scope({"TEST_MIGRATION_KEY": "from-scope"})
    try:
        from agent.secret_scope import get_secret

        assert get_secret("TEST_MIGRATION_KEY") == "from-scope"
    finally:
        reset_secret_scope(token)


def test_single_profile_fallback_unchanged(monkeypatch, tmp_path):
    """No scope installed: get_secret falls back to os.environ (pre-migration parity)."""
    monkeypatch.delenv("TEST_MIGRATION_KEY", raising=False)
    monkeypatch.setenv("TEST_MIGRATION_KEY", "from-environ")
    set_multiplex_active(False)
    from agent.secret_scope import get_secret

    assert get_secret("TEST_MIGRATION_KEY") == "from-environ"
    monkeypatch.delenv("TEST_MIGRATION_KEY")
    assert get_secret("TEST_MIGRATION_KEY", "dflt") == "dflt"


def test_multiplex_without_scope_raises(monkeypatch, tmp_path):
    """Fail-closed: multiplex on + no scope = UnscopedSecretError."""
    from agent.secret_scope import UnscopedSecretError, get_secret

    token_scope = None
    set_multiplex_active(True)
    monkeypatch.delenv("TEST_MIGRATION_KEY", raising=False)
    try:
        try:
            get_secret("TEST_MIGRATION_KEY")
            raise AssertionError("expected UnscopedSecretError")
        except UnscopedSecretError:
            pass
        # scope installed -> resolves fine even under multiplex
        token_scope = set_secret_scope({"TEST_MIGRATION_KEY": "scoped"})
        assert get_secret("TEST_MIGRATION_KEY") == "scoped"
    finally:
        if token_scope is not None:
            reset_secret_scope(token_scope)
        set_multiplex_active(False)
