"""Config loader coverage for gateway.model_overrides (HP-102)."""

from intellect_cli.config import DEFAULT_CONFIG, load_config


def test_default_config_includes_model_overrides():
    assert "model_overrides" in DEFAULT_CONFIG["gateway"]
    assert DEFAULT_CONFIG["gateway"]["model_overrides"] == {}


def test_load_config_merges_model_overrides(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    (home / "config.yaml").write_text(
        "gateway:\n"
        "  model_overrides:\n"
        "    telegram:\n"
        "      model: fast-model\n",
        encoding="utf-8",
    )
    cfg = load_config()
    assert cfg["gateway"]["model_overrides"]["telegram"]["model"] == "fast-model"


def test_gateway_raw_yaml_reads_model_overrides(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    (home / "config.yaml").write_text(
        "gateway:\n"
        "  model_overrides:\n"
        "    discord:\n"
        "      provider: openrouter\n",
        encoding="utf-8",
    )
    from gateway.config_helpers import _load_gateway_config

    raw = _load_gateway_config()
    assert raw["gateway"]["model_overrides"]["discord"]["provider"] == "openrouter"


def test_load_cli_config_reads_model_overrides(tmp_path, monkeypatch):
    home = tmp_path / ".intellect"
    home.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(home))
    (home / "config.yaml").write_text(
        "gateway:\n"
        "  model_overrides:\n"
        "    slack:\n"
        "      model: cli-model\n",
        encoding="utf-8",
    )
    import cli

    monkeypatch.setattr(cli, "_intellect_home", home)
    cfg = cli.load_cli_config()
    assert cfg["gateway"]["model_overrides"]["slack"]["model"] == "cli-model"
