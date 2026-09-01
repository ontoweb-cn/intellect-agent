"""Tests for intellect doctor's multiplex check (MP-06)."""

from pathlib import Path

import pytest

from intellect_cli.doctor import _check_gateway_multiplex


@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    return home


def _profile(home: Path, name: str, config_yaml: str | None = None) -> None:
    pdir = home / "profiles" / name
    pdir.mkdir(parents=True)
    if config_yaml:
        (pdir / "config.yaml").write_text(config_yaml, encoding="utf-8")


def test_single_profile_is_info_only(profile_env):
    issues: list = []
    _check_gateway_multiplex(issues)
    assert issues == []


def test_pinned_listener_secondary_flagged(profile_env):
    _profile(
        profile_env,
        "coder",
        "platforms:\n  api_server:\n    enabled: true\n    port: 8642\n",
    )
    issues: list = []
    _check_gateway_multiplex(issues)
    assert any("coder" in issue for issue in issues)
    assert any("host/port" in issue for issue in issues)


def test_unpinned_listener_secondary_not_flagged(profile_env):
    """B1-4: a listener platform without a pinned binding is legitimately
    served under the front end's prefix — no issue, no warning."""
    _profile(
        profile_env,
        "coder",
        "platforms:\n  api_server:\n    enabled: true\n",
    )
    issues: list = []
    _check_gateway_multiplex(issues)
    assert issues == []


def test_duplicate_credentials_flagged(profile_env):
    cfg = "platforms:\n  telegram:\n    token: same-token-123\n"
    _profile(profile_env, "alpha", cfg)
    _profile(profile_env, "beta", cfg)
    issues: list = []
    _check_gateway_multiplex(issues)
    assert any("platforms.telegram.token" in issue for issue in issues)
    assert any("alpha" in issue and "beta" in issue for issue in issues)


def test_distinct_credentials_not_flagged(profile_env):
    _profile(
        profile_env,
        "alpha",
        "platforms:\n  telegram:\n    token: token-alpha\n",
    )
    _profile(
        profile_env,
        "beta",
        "platforms:\n  telegram:\n    token: token-beta\n",
    )
    issues: list = []
    _check_gateway_multiplex(issues)
    assert issues == []


def test_allowlist_narrows_the_check(profile_env):
    """Pinned-listener issues outside the allowlist are not reported."""
    _profile(
        profile_env,
        "coder",
        "platforms:\n  api_server:\n    enabled: true\n    port: 8642\n",
    )
    _profile(profile_env, "quiet")
    cfg = profile_env / "config.yaml"
    cfg.write_text(
        "gateway:\n  multiplex_profile_allowlist:\n    - quiet\n",
        encoding="utf-8",
    )
    issues: list = []
    _check_gateway_multiplex(issues)
    assert issues == []
