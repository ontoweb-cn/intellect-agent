"""Doctor checks for gateway.model_overrides platform keys (HP-102g)."""

from __future__ import annotations

from intellect_cli.doctor import _check_gateway_model_overrides


def test_model_overrides_unknown_platform_warns(monkeypatch, tmp_path):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    (home / "config.yaml").write_text(
        "gateway:\n  model_overrides:\n    telegrm:\n      model: gpt-4\n",
        encoding="utf-8",
    )

    issues: list[str] = []
    _check_gateway_model_overrides(issues)
    assert issues
    assert "gateway.model_overrides" in issues[0]


def test_model_overrides_valid_platform_silent(monkeypatch, tmp_path):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    (home / "config.yaml").write_text(
        "gateway:\n  model_overrides:\n    telegram:\n      model: gpt-4\n",
        encoding="utf-8",
    )

    issues: list[str] = []
    _check_gateway_model_overrides(issues)
    assert issues == []


def test_model_overrides_channel_suffix_validates_platform_prefix(monkeypatch, tmp_path):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    (home / "config.yaml").write_text(
        "gateway:\n  model_overrides:\n    telegrm:dm:123:\n      model: gpt-4\n",
        encoding="utf-8",
    )

    issues: list[str] = []
    _check_gateway_model_overrides(issues)
    assert issues


def test_model_overrides_empty_skips(monkeypatch, tmp_path):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    (home / "config.yaml").write_text("gateway:\n  model_overrides: {}\n", encoding="utf-8")

    issues: list[str] = []
    _check_gateway_model_overrides(issues)
    assert issues == []
