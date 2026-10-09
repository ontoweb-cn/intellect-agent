"""Explicit-enabled semantics for gateway platform config (D1).

An explicit ``enabled:`` key in a platform's config.yaml section — top-level
``<name>:`` or nested ``platforms.<name>:`` — must win over env-credential
auto-enable for EVERY platform, not just Slack/WhatsApp. Without an explicit
key, a credential env var still auto-enables. See
docs/plans/2026-10-09-webui-gateway-config.md (decision D1) and
``gateway.config._env_auto_enable``.
"""

from __future__ import annotations

import os

import pytest

from gateway.config import Platform, load_gateway_config


@pytest.fixture(autouse=True)
def _clean_credential_env(monkeypatch):
    # Belt-and-suspenders on top of the conftest hermetic blanking: these are
    # the exact vars the assertions below depend on being absent/present.
    for var in (
        "TELEGRAM_BOT_TOKEN",
        "DISCORD_BOT_TOKEN",
        "SLACK_BOT_TOKEN",
        "DINGTALK_CLIENT_ID",
        "DINGTALK_CLIENT_SECRET",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "WECOM_BOT_ID",
        "WECOM_SECRET",
        "WECOM_CALLBACK_CORP_ID",
        "WECOM_CALLBACK_CORP_SECRET",
        "WEIXIN_TOKEN",
        "WEIXIN_ACCOUNT_ID",
        "WHATSAPP_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)


def _write_yaml(text: str) -> None:
    """Write config.yaml into the conftest-provided per-test INTELLECT_HOME."""
    home = os.environ["INTELLECT_HOME"]
    with open(os.path.join(home, "config.yaml"), "w", encoding="utf-8") as fh:
        fh.write(text)


def test_env_token_enables_platform_without_yaml(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:authtoken")
    cfg = load_gateway_config()
    assert cfg.platforms[Platform.TELEGRAM].enabled is True


def test_nested_yaml_explicit_disable_overrides_env_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:authtoken")
    _write_yaml("platforms:\n  telegram:\n    enabled: false\n")
    cfg = load_gateway_config()
    tg = cfg.platforms[Platform.TELEGRAM]
    assert tg.enabled is False
    # Token is still stored so skills can use it without the gateway adapter.
    assert tg.token == "123:authtoken"


def test_top_level_yaml_explicit_disable_overrides_env_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:authtoken")
    _write_yaml("telegram:\n  enabled: false\n  require_mention: true\n")
    cfg = load_gateway_config()
    tg = cfg.platforms[Platform.TELEGRAM]
    assert tg.enabled is False
    assert tg.extra.get("require_mention") is True


def test_yaml_behavior_key_alone_does_not_block_env_enable(monkeypatch):
    # Top-level settings such as allowed_chats must not turn an env-token
    # setup into a disabled platform — only an explicit enabled: false should.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:authtoken")
    _write_yaml("telegram:\n  allowed_chats:\n    - -100123\n")
    cfg = load_gateway_config()
    assert cfg.platforms[Platform.TELEGRAM].enabled is True


def test_yaml_enabled_true_with_env_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:authtoken")
    _write_yaml("telegram:\n  enabled: true\n")
    cfg = load_gateway_config()
    assert cfg.platforms[Platform.TELEGRAM].enabled is True


def test_dingtalk_explicit_disable_keeps_credentials(monkeypatch):
    monkeypatch.setenv("DINGTALK_CLIENT_ID", "cid")
    monkeypatch.setenv("DINGTALK_CLIENT_SECRET", "csec")
    _write_yaml("dingtalk:\n  enabled: false\n")
    cfg = load_gateway_config()
    dt = cfg.platforms[Platform.DINGTALK]
    assert dt.enabled is False
    assert dt.extra.get("client_id") == "cid"
    assert dt.extra.get("client_secret") == "csec"


def test_weixin_nested_explicit_disable(monkeypatch):
    monkeypatch.setenv("WEIXIN_TOKEN", "wtoken")
    monkeypatch.setenv("WEIXIN_ACCOUNT_ID", "acct")
    _write_yaml("platforms:\n  weixin:\n    enabled: false\n")
    cfg = load_gateway_config()
    assert cfg.platforms[Platform.WEIXIN].enabled is False


def test_weixin_half_credentials_stay_disabled(monkeypatch):
    # Token without account_id: the credential is stored (skills may use it)
    # but the adapter must not auto-enable — a half-credentialed weixin
    # would just retry-fail noisily. Matches the connected checker contract.
    monkeypatch.setenv("WEIXIN_TOKEN", "wtoken")
    cfg = load_gateway_config()
    weixin = cfg.platforms[Platform.WEIXIN]
    assert weixin.enabled is False
    assert weixin.token == "wtoken"


def test_weixin_yaml_account_id_completes_env_pair(monkeypatch):
    # account_id from yaml + token from env = complete pair → auto-enable.
    monkeypatch.setenv("WEIXIN_TOKEN", "wtoken")
    _write_yaml("platforms:\n  weixin:\n    extra:\n      account_id: fromyaml\n")
    cfg = load_gateway_config()
    assert cfg.platforms[Platform.WEIXIN].enabled is True


def test_feishu_partial_env_does_not_auto_enable(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "aid")
    # FEISHU_APP_SECRET missing — the env block requires the pair.
    cfg = load_gateway_config()
    feishu = cfg.platforms.get(Platform.FEISHU)
    assert feishu is None or feishu.enabled is False


def test_no_enabled_explicit_marker_leaks_into_extra(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:authtoken")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-no-env-run")
    _write_yaml("telegram:\n  enabled: true\n  require_mention: true\n")
    cfg = load_gateway_config()
    for platform, pconfig in cfg.platforms.items():
        assert "_enabled_explicit" not in pconfig.extra, platform


def test_whatsapp_explicit_env_disable_still_wins(monkeypatch):
    # WhatsApp keeps its dedicated WHATSAPP_ENABLED tri-state; make sure the
    # generalization didn't break it.
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    _write_yaml("platforms:\n  whatsapp:\n    enabled: true\n")
    cfg = load_gateway_config()
    assert cfg.platforms[Platform.WHATSAPP].enabled is False


def test_qqbot_half_credentials_stay_disabled(monkeypatch):
    # Same pair-complete rule as weixin: app_id without client_secret is
    # stored but must not auto-enable a retry-failing adapter.
    monkeypatch.setenv("QQ_APP_ID", "qqid")
    cfg = load_gateway_config()
    qq = cfg.platforms[Platform.QQBOT]
    assert qq.enabled is False
    assert qq.extra.get("app_id") == "qqid"


def test_qqbot_yaml_secret_completes_env_pair(monkeypatch):
    monkeypatch.setenv("QQ_APP_ID", "qqid")
    _write_yaml("platforms:\n  qqbot:\n    extra:\n      client_secret: qqsec\n")
    cfg = load_gateway_config()
    assert cfg.platforms[Platform.QQBOT].enabled is True


def test_plugin_platform_explicit_disable_skips_env_enablement(monkeypatch):
    from types import SimpleNamespace

    import gateway.config as gc

    try:
        line = Platform("line")
    except Exception:
        pytest.skip("line plugin platform not registered")

    entry = SimpleNamespace(
        name="line",
        check_fn=lambda: True,
        env_enablement_fn=None,
        is_connected=None,
        validate_config=None,
    )
    fake_registry = SimpleNamespace(
        plugin_entries=lambda: [entry],
        all_entries=lambda: [],
        get=lambda name: None,
    )
    monkeypatch.setattr("gateway.platform_registry.platform_registry", fake_registry)

    # Explicitly disabled: env enablement must be skipped entirely.
    cfg = gc.GatewayConfig()
    cfg.platforms[line] = gc.PlatformConfig(enabled=False, extra={"_enabled_explicit": True})
    gc._apply_env_overrides(cfg)
    assert cfg.platforms[line].enabled is False
