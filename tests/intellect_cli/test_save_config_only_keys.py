"""Regression tests for the only_keys config fast path."""

import os
import stat

import yaml


def test_only_keys_preserves_comments_and_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    from intellect_cli import config as cfg

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "# keep this header\n"
        "model:\n"
        "  default: old-model  # inline comment\n"
        "agent:\n"
        "  max_turns: 9\n",
        encoding="utf-8",
    )
    os.chmod(config_path, 0o644)

    cfg.save_config({"model": {"default": "new-model"}}, only_keys=["model"])

    text = config_path.read_text(encoding="utf-8")
    assert "# keep this header" in text
    assert "agent:" in text
    data = yaml.safe_load(text)
    assert data["model"]["default"] == "new-model"
    assert data["agent"]["max_turns"] == 9
    if os.name != "nt":
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
