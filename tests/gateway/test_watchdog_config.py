"""Tests for gateway watchdog config wiring (TODO-013)."""

from gateway.config import GatewayConfig, WatchdogConfig, load_gateway_config


def test_watchdog_defaults_match_module_constants():
    cfg = GatewayConfig()
    wd = cfg.watchdog
    assert wd.enabled is True
    assert wd.heartbeat_interval_s == 5.0
    assert wd.stall_threshold_s == 30.0
    assert wd.max_strikes == 3
    assert wd.shutdown_grace_s == 30.0


def test_watchdog_from_dict_overrides_and_clamps():
    wd = WatchdogConfig.from_dict({
        "enabled": False,
        "heartbeat_interval_s": 2.0,
        "stall_threshold_s": 1.0,   # below heartbeat → clamped up to heartbeat
        "max_strikes": 99,
        "shutdown_grace_s": 0,      # clamped to the 1.0 floor
    })
    assert wd.enabled is False
    assert wd.heartbeat_interval_s == 2.0
    assert wd.stall_threshold_s == 2.0   # max(heartbeat, stall)
    assert wd.max_strikes == 99
    assert wd.shutdown_grace_s == 1.0


def test_gateway_config_yaml_section(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(
        "gateway:\n"
        "  watchdog:\n"
        "    enabled: false\n"
        "    heartbeat_interval_s: 2.5\n"
        "    stall_threshold_s: 10\n"
        "    max_strikes: 5\n"
        "    shutdown_grace_s: 12\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))

    cfg = load_gateway_config()
    assert cfg.watchdog.enabled is False
    assert cfg.watchdog.heartbeat_interval_s == 2.5
    assert cfg.watchdog.stall_threshold_s == 10
    assert cfg.watchdog.max_strikes == 5
    assert cfg.watchdog.shutdown_grace_s == 12


def test_gateway_config_defaults_when_section_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("gateway:\n  strict: false\n",
                                          encoding="utf-8")
    cfg = load_gateway_config()
    assert cfg.watchdog.enabled is True
    assert cfg.watchdog.heartbeat_interval_s == 5.0
