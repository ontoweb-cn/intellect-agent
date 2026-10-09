"""WebUI gateway-platform config API tests (GET/PUT /api/gateway/platforms).

Covers the desensitization invariant (secrets never echoed back), the write
routing (env fields → profile .env, yaml fields → config.yaml top-level
platform section, enabled → platforms.<name>.enabled), the enable-validation
gate, and the localhost access gate. See
docs/plans/2026-10-09-webui-gateway-config.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(_WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBUI_DIR))


@pytest.fixture(autouse=True)
def _fresh_profile_modules():
    """Reload api.profiles/api.config per test.

    ``api.profiles`` freezes ``_DEFAULT_INTELLECT_HOME`` at import time, so
    without a reload every test after the first would read/write the FIRST
    test's temp home.
    """
    import importlib

    import api.config as apiconfig
    import api.profiles as profiles

    importlib.reload(profiles)
    importlib.reload(apiconfig)
    yield


@pytest.fixture(autouse=True)
def _gateway_env(monkeypatch):
    """Hermetic credential env for the platform vars the tests assert on."""
    for var in (
        "TELEGRAM_BOT_TOKEN",
        "DINGTALK_CLIENT_ID",
        "DINGTALK_CLIENT_SECRET",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "WECOM_BOT_ID",
        "WECOM_SECRET",
        "WECOM_CALLBACK_CORP_ID",
        "WECOM_CALLBACK_CORP_SECRET",
        "WECOM_CALLBACK_PORT",
        "WEIXIN_TOKEN",
        "WEIXIN_ACCOUNT_ID",
    ):
        monkeypatch.delenv(var, raising=False)


class _Capture:
    def __init__(self):
        self.payload = None
        self.status = None

    def __call__(self, handler, payload, status: int = 200, extra_headers=None):
        self.payload = payload
        self.status = status
        # Mirror the real api.helpers.j: it sends the response as a side
        # effect and returns None. A mock that returned the payload once hid
        # a control-flow bug (a guard checking `if bad(...)` in production
        # receives None).
        return None


@pytest.fixture
def capture(monkeypatch):
    """Replace api.helpers.j so handler responses can be asserted directly."""
    import api.helpers as helpers

    cap = _Capture()
    monkeypatch.setattr(helpers, "j", cap)
    return cap


@pytest.fixture
def loopback_ok(monkeypatch):
    import api.auth as auth

    monkeypatch.setattr(auth, "is_loopback_client", lambda handler: True)


def _write_home_env(monkeypatch, lines: list[str]) -> None:
    import os

    home = os.environ["INTELLECT_HOME"]
    Path(home, ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _get_platform(payload: dict, name: str) -> dict:
    return next(p for p in payload["platforms"] if p["name"] == name)


def test_get_payload_masks_secrets(monkeypatch):
    _write_home_env(
        monkeypatch,
        ["DINGTALK_CLIENT_ID=ding-cid", "DINGTALK_CLIENT_SECRET=ding-supersecret-value"],
    )
    from api.gateway_platform_config import build_payload

    payload = build_payload()
    dingtalk = _get_platform(payload, "dingtalk")
    assert dingtalk["configured"] is True
    assert dingtalk["enabled"] is True and dingtalk["enabled_explicit"] is None

    raw = json.dumps(payload)
    assert "ding-supersecret-value" not in raw
    secret_field = next(f for f in dingtalk["fields"] if f["key"] == "client_secret")
    assert secret_field["set"] is True
    assert "value" not in secret_field


def test_get_payload_unconfigured_and_runtime(monkeypatch):
    from api.gateway_platform_config import build_payload

    payload = build_payload()
    weixin = _get_platform(payload, "weixin")
    assert weixin["configured"] is False
    assert sorted(weixin["missing_required"]) == ["account_id", "token"]
    assert weixin["enabled"] is False
    assert weixin["runtime"] is None


def test_put_writes_env_yaml_and_enabled(monkeypatch, capture, loopback_ok, tmp_path):
    _write_home_env(monkeypatch, ["DINGTALK_CLIENT_ID=ding-cid"])
    from api.gateway_platform_config import save_platform

    resp = save_platform(
        None,
        "dingtalk",
        {
            "fields": {"require_mention": True, "allowed_users": ["u1", "u2"]},
            "secrets": {"client_secret": "brand-new-secret"},
            "enabled": True,
        },
    )
    assert capture.payload["ok"] is True
    assert capture.payload["needs_restart"] is True

    import os

    home = Path(os.environ["INTELLECT_HOME"])
    env_text = (home / ".env").read_text(encoding="utf-8")
    assert "DINGTALK_CLIENT_SECRET=brand-new-secret" in env_text
    yaml_text = (home / "config.yaml").read_text(encoding="utf-8")
    assert "require_mention: true" in yaml_text
    assert "enabled: true" in yaml_text
    assert "platforms:" in yaml_text


def test_put_enable_blocked_without_required_fields(monkeypatch, capture, loopback_ok):
    from api.gateway_platform_config import save_platform

    resp = save_platform(None, "weixin", {"enabled": True})
    assert capture.status == 400
    assert "account_id" in capture.payload["error"]
    assert capture.status == 400


def test_put_clear_secret_writes_empty_value(monkeypatch, capture, loopback_ok):
    _write_home_env(monkeypatch, ["WEIXIN_TOKEN=old-token"])
    from api.gateway_platform_config import save_platform

    save_platform(None, "weixin", {"secrets": {"token": ""}})
    import os

    env_text = Path(os.environ["INTELLECT_HOME"], ".env").read_text(encoding="utf-8")
    assert "WEIXIN_TOKEN=" in env_text
    assert "old-token" not in env_text


def test_put_wecom_callback_int_field(monkeypatch, capture, loopback_ok):
    from api.gateway_platform_config import save_platform

    resp = save_platform(
        None,
        "wecom_callback",
        {
            "fields": {"port": "8645"},
            "secrets": {"corp_secret": "corpsec"},
            "enabled": False,
        },
    )
    assert capture.payload["ok"] is True
    import os

    env_text = Path(os.environ["INTELLECT_HOME"], ".env").read_text(encoding="utf-8")
    assert "WECOM_CALLBACK_PORT=8645" in env_text


def test_put_rejects_unknown_field_and_non_secret(monkeypatch, capture, loopback_ok):
    from api.gateway_platform_config import save_platform

    save_platform(None, "dingtalk", {"fields": {"nope": 1}})
    assert capture.status == 400
    save_platform(None, "dingtalk", {"secrets": {"require_mention": True}})
    assert capture.status == 400


def test_put_rejects_non_loopback(monkeypatch, capture):
    import api.auth as auth

    monkeypatch.setattr(auth, "is_loopback_client", lambda handler: False)
    from api.gateway_platform_config import save_platform

    save_platform(None, "dingtalk", {"enabled": True})
    assert capture.status == 403


def test_put_unknown_platform_404(monkeypatch, capture, loopback_ok):
    from api.gateway_platform_config import save_platform

    save_platform(None, "hyperspace", {})
    assert capture.status == 404


def test_enabled_explicit_toggle_roundtrip(monkeypatch, capture, loopback_ok):
    _write_home_env(
        monkeypatch,
        ["WEIXIN_TOKEN=wtoken", "WEIXIN_ACCOUNT_ID=acct"],
    )
    from api.gateway_platform_config import build_payload, save_platform

    payload = build_payload()
    weixin = _get_platform(payload, "weixin")
    assert weixin["enabled"] is True and weixin["enabled_explicit"] is None

    save_platform(None, "weixin", {"enabled": False})
    payload = build_payload()
    weixin = _get_platform(payload, "weixin")
    assert weixin["enabled"] is False
    assert weixin["enabled_explicit"] is False
    assert weixin["configured"] is True


def test_get_enabled_top_level_section_wins_over_nested(monkeypatch):
    # Loader precedence: the top-level <name>: section overwrites the
    # platforms.<name>: block. The GET payload must mirror it, else the UI
    # toggle would show a state a gateway restart wouldn't honor.
    import os

    home = Path(os.environ["INTELLECT_HOME"])
    (home / "config.yaml").write_text(
        "weixin:\n  enabled: false\nplatforms:\n  weixin:\n    enabled: true\n",
        encoding="utf-8",
    )
    _write_home_env(monkeypatch, ["WEIXIN_TOKEN=wtoken", "WEIXIN_ACCOUNT_ID=acct"])
    from api.gateway_platform_config import build_payload

    weixin = _get_platform(build_payload(), "weixin")
    assert weixin["enabled"] is False
    assert weixin["enabled_explicit"] is False


def test_put_refused_when_config_yaml_broken(monkeypatch, capture, loopback_ok):
    import os

    home = Path(os.environ["INTELLECT_HOME"])
    (home / "config.yaml").write_text("telegram: [unclosed\n  bad yaml: :\n", encoding="utf-8")
    from api.gateway_platform_config import save_platform

    save_platform(None, "dingtalk", {"fields": {"require_mention": True}})
    assert capture.status == 400
    assert "syntax" in capture.payload["error"]
    # The broken file must be untouched — no clobbering.
    assert (home / "config.yaml").read_text(encoding="utf-8").startswith("telegram: [unclosed")


def test_put_warns_when_required_credential_cleared(monkeypatch, capture, loopback_ok):
    _write_home_env(monkeypatch, ["WEIXIN_TOKEN=wtoken", "WEIXIN_ACCOUNT_ID=acct"])
    from api.gateway_platform_config import save_platform

    resp = save_platform(None, "weixin", {"secrets": {"token": ""}})
    assert capture.payload["ok"] is True
    assert any("token" in w for w in capture.payload["warnings"])


# ── P1 platform schemas ──────────────────────────────────────────────────────


def test_qqbot_schema_configured_with_pair(monkeypatch):
    _write_home_env(monkeypatch, ["QQ_APP_ID=qqid", "QQ_CLIENT_SECRET=qqsec"])
    from api.gateway_platform_config import build_payload

    qq = _get_platform(build_payload(), "qqbot")
    assert qq["configured"] is True
    assert qq["enabled"] is True


def test_email_requires_all_four_envs(monkeypatch):
    _write_home_env(
        monkeypatch,
        ["EMAIL_ADDRESS=a@b.c", "EMAIL_PASSWORD=pw", "EMAIL_IMAP_HOST=imap.x"],
        # EMAIL_SMTP_HOST missing
    )
    from api.gateway_platform_config import build_payload

    email = _get_platform(build_payload(), "email")
    assert email["configured"] is False
    assert email["missing_required"] == ["smtp_host"]

    import os

    home = Path(os.environ["INTELLECT_HOME"])
    env_text = (home / ".env").read_text(encoding="utf-8") + "EMAIL_SMTP_HOST=smtp.x\n"
    (home / ".env").write_text(env_text, encoding="utf-8")
    email = _get_platform(build_payload(), "email")
    assert email["configured"] is True


def test_whatsapp_needs_no_credentials(monkeypatch):
    from api.gateway_platform_config import build_payload

    wa = _get_platform(build_payload(), "whatsapp")
    # Auth is handled by the bridge (QR pairing at gateway start) — the
    # connected checker always passes; only behavior keys are configurable.
    assert wa["configured"] is True
    assert wa["fields"] and all(f["target"] == "yaml" for f in wa["fields"])


def test_slack_discord_token_field_maps_to_token(monkeypatch):
    _write_home_env(monkeypatch, ["SLACK_BOT_TOKEN=xoxb-abc", "DISCORD_BOT_TOKEN=dtok"])
    from api.gateway_platform_config import build_payload

    payload = build_payload()
    slack = _get_platform(payload, "slack")
    discord = _get_platform(payload, "discord")
    assert slack["configured"] is True and discord["configured"] is True
    # The secret never leaks even though the checker saw it.
    raw = json.dumps(payload)
    assert "xoxb-abc" not in raw and "dtok" not in raw


# ── generic extra editor ─────────────────────────────────────────────────────


def test_get_includes_generic_platforms_with_extra(monkeypatch):
    import os

    home = Path(os.environ["INTELLECT_HOME"])
    (home / "config.yaml").write_text(
        "platforms:\n  signal:\n    extra:\n      http_url: http://127.0.0.1:8123\n",
        encoding="utf-8",
    )
    from api.gateway_platform_config import build_payload

    payload = build_payload()
    signal = _get_platform(payload, "signal")
    assert signal["generic"] is True
    assert signal["fields"] == []
    assert signal["extra"] == {"http_url": "http://127.0.0.1:8123"}


def test_put_generic_extra_writes_yaml_with_coercion(monkeypatch, capture, loopback_ok):
    from api.gateway_platform_config import save_platform

    resp = save_platform(
        None,
        "signal",
        {"extra": {"http_url": "http://127.0.0.1:8123", "port": "8645", "ignore_stories": "true"}},
    )
    assert capture.payload["ok"] is True
    import os

    yaml_text = Path(os.environ["INTELLECT_HOME"], "config.yaml").read_text(encoding="utf-8")
    assert "port: 8645" in yaml_text
    assert "ignore_stories: true" in yaml_text
    assert "http_url: http://127.0.0.1:8123" in yaml_text


def test_put_generic_rejects_reserved_keys(monkeypatch, capture, loopback_ok):
    from api.gateway_platform_config import save_platform

    save_platform(None, "signal", {"extra": {"enabled": True}})
    assert capture.status == 400
    save_platform(None, "signal", {"extra": {"token": "x"}})
    assert capture.status == 400


def test_put_generic_unknown_platform_404(monkeypatch, capture, loopback_ok):
    from api.gateway_platform_config import save_platform

    save_platform(None, "hyperspace-relay", {"extra": {"a": 1}})
    assert capture.status == 404


# ── managed installs ─────────────────────────────────────────────────────────


def test_put_refused_on_managed_install(monkeypatch, capture, loopback_ok):
    import os

    import intellect_cli.config as cli_config

    monkeypatch.setattr(cli_config, "is_managed", lambda: True)
    from api.gateway_platform_config import save_platform

    home = Path(os.environ["INTELLECT_HOME"])
    (home / ".env").write_text("DINGTALK_CLIENT_ID=keep\n", encoding="utf-8")
    env_before = (home / ".env").read_text(encoding="utf-8")

    save_platform(
        None,
        "dingtalk",
        {"fields": {"require_mention": True}, "secrets": {"client_secret": "newsec"}},
    )
    assert capture.status == 403
    # The refusal must actually stop the save — with a broken guard the
    # response was sent but the writes still happened.
    assert (home / ".env").read_text(encoding="utf-8") == env_before
    assert not (home / "config.yaml").exists()


# ── legacy gateway.json base layer ───────────────────────────────────────────


def test_get_reads_legacy_gateway_json(monkeypatch):
    import json
    import os

    home = Path(os.environ["INTELLECT_HOME"])
    (home / "gateway.json").write_text(
        json.dumps({"platforms": {"telegram": {"enabled": False, "token": "legacytok"}}}),
        encoding="utf-8",
    )
    from api.gateway_platform_config import build_payload

    telegram = _get_platform(build_payload(), "telegram")
    assert telegram["configured"] is True
    assert telegram["enabled"] is False
    # Legacy enabled is NOT explicit — env creds would auto-enable per D1.
    assert telegram["enabled_explicit"] is None
    assert "legacytok" not in json.dumps(_get_platform(build_payload(), "telegram"))


# ── second-round review fixes (P1-1..P1-5) ──────────────────────────────────


def test_weixin_mixed_source_pair_shows_enabled(monkeypatch):
    # Loader rule (gateway/config.py weixin block): token+account_id from ANY
    # source mix completes the pair and auto-enables. UI must mirror it.
    import os

    (Path(os.environ["INTELLECT_HOME"]) / "config.yaml").write_text(
        "platforms:\n  weixin:\n    extra:\n      account_id: fromyaml\n",
        encoding="utf-8",
    )
    _write_home_env(monkeypatch, ["WEIXIN_TOKEN=wtoken"])
    from api.gateway_platform_config import build_payload

    weixin = _get_platform(build_payload(), "weixin")
    assert weixin["enabled"] is True


def test_string_enabled_values_coerced_like_loader(monkeypatch):
    # Hand-written yaml `enabled: "false"` must read as disabled (the loader
    # coerces via _coerce_bool); bool("false") would show enabled.
    _write_home_env(monkeypatch, ["TELEGRAM_BOT_TOKEN=123:authtoken"])
    import os

    (Path(os.environ["INTELLECT_HOME"]) / "config.yaml").write_text(
        'telegram:\n  enabled: "false"\n', encoding="utf-8"
    )
    from api.gateway_platform_config import build_payload

    telegram = _get_platform(build_payload(), "telegram")
    assert telegram["enabled"] is False
    assert telegram["enabled_explicit"] is False


def test_put_enabled_clears_top_level_enabled(monkeypatch, capture, loopback_ok):
    # A hand-written top-level `<name>.enabled` wins over platforms.<name> at
    # load time — the toggle must remove it or it's a silent no-op.
    import os

    (Path(os.environ["INTELLECT_HOME"]) / "config.yaml").write_text(
        "weixin:\n  enabled: false\nplatforms:\n  weixin:\n    enabled: false\n",
        encoding="utf-8",
    )
    _write_home_env(monkeypatch, ["WEIXIN_TOKEN=wtoken", "WEIXIN_ACCOUNT_ID=acct"])
    from api.gateway_platform_config import save_platform

    save_platform(None, "weixin", {"enabled": True})
    assert capture.payload["ok"] is True
    import os

    yaml_text = Path(os.environ["INTELLECT_HOME"], "config.yaml").read_text(encoding="utf-8")
    assert "weixin:" not in yaml_text.split("platforms:")[0]
    assert "enabled: true" in yaml_text


def test_legacy_token_user_can_enable(monkeypatch, capture, loopback_ok):
    # Regression for the enable gate: a user configured only via legacy
    # gateway.json is "configured" in the GET view and must not be refused.
    import json
    import os

    (Path(os.environ["INTELLECT_HOME"]) / "gateway.json").write_text(
        json.dumps({"platforms": {"telegram": {"token": "legacytok"}}}), encoding="utf-8"
    )
    from api.gateway_platform_config import save_platform

    save_platform(None, "telegram", {"enabled": True})
    assert capture.status is None or capture.status == 200
    assert capture.payload["ok"] is True


def test_generic_secret_keys_masked_and_kept(monkeypatch, capture, loopback_ok):
    import os

    (Path(os.environ["INTELLECT_HOME"]) / "config.yaml").write_text(
        "platforms:\n  bluebubbles:\n    extra:\n      password: hush-hush\n      server_url: http://x\n",
        encoding="utf-8",
    )
    from api.gateway_platform_config import build_payload, save_platform

    payload = build_payload()
    raw = json.dumps(payload)
    assert "hush-hush" not in raw
    bb = _get_platform(payload, "bluebubbles")
    assert bb["extra"]["password"] == "(set)"
    assert "password" in bb["extra_masked"]
    assert bb["extra"]["server_url"] == "http://x"

    # Saving "(set)" back keeps the stored value; a new value replaces it.
    save_platform(
        None,
        "bluebubbles",
        {"extra": {"password": "(set)", "server_url": "http://y", "newkey": "1"}},
    )
    yaml_text = Path(os.environ["INTELLECT_HOME"], "config.yaml").read_text(encoding="utf-8")
    assert "password: hush-hush" in yaml_text
    assert "server_url: http://y" in yaml_text
    assert "newkey: '1'" in yaml_text or "newkey: 1" in yaml_text


def test_put_refused_on_non_dict_config_root(monkeypatch, capture, loopback_ok):
    import os

    (Path(os.environ["INTELLECT_HOME"]) / "config.yaml").write_text(
        "- just\n- a\n- list\n", encoding="utf-8"
    )
    from api.gateway_platform_config import save_platform

    save_platform(None, "dingtalk", {"fields": {"require_mention": True}})
    assert capture.status == 400
    assert "mapping" in capture.payload["error"]


# ── polish round (review follow-up) ─────────────────────────────────────────


def test_qqbot_mixed_source_pair_shows_enabled(monkeypatch):
    # Loader qqbot block: app_id + client_secret from any source mix completes
    # the pair and auto-enables — mirror it.
    import os

    (Path(os.environ["INTELLECT_HOME"]) / "config.yaml").write_text(
        "platforms:\n  qqbot:\n    extra:\n      client_secret: qqsec\n",
        encoding="utf-8",
    )
    _write_home_env(monkeypatch, ["QQ_APP_ID=qqid"])
    from api.gateway_platform_config import build_payload

    qq = _get_platform(build_payload(), "qqbot")
    assert qq["enabled"] is True


def test_gateway_platforms_nested_layer_visible(monkeypatch):
    # The loader reads gateway.platforms.<name> between gateway.json and
    # platforms.<name> — the GET view must see it too.
    import os

    (Path(os.environ["INTELLECT_HOME"]) / "config.yaml").write_text(
        "gateway:\n  platforms:\n    signal:\n      extra:\n        http_url: http://127.0.0.1:8123\n",
        encoding="utf-8",
    )
    from api.gateway_platform_config import build_payload

    signal = _get_platform(build_payload(), "signal")
    assert signal["extra"].get("http_url") == "http://127.0.0.1:8123"


def test_gateway_platforms_enabled_is_explicit(monkeypatch):
    # Since D1, gateway.platforms.<name>.enabled carries the loader's explicit
    # marker — the UI must treat it as an explicit off.
    _write_home_env(monkeypatch, ["TELEGRAM_BOT_TOKEN=123:authtoken"])
    import os

    (Path(os.environ["INTELLECT_HOME"]) / "config.yaml").write_text(
        "gateway:\n  platforms:\n    telegram:\n      enabled: false\n",
        encoding="utf-8",
    )
    from api.gateway_platform_config import build_payload

    telegram = _get_platform(build_payload(), "telegram")
    assert telegram["enabled"] is False
    assert telegram["enabled_explicit"] is False


def test_connected_check_timeout_is_bounded(monkeypatch):
    import time

    import api.gateway_platform_config as c
    import gateway.config as gc

    monkeypatch.setattr(c, "_CHECK_TIMEOUT_S", 0.2)

    def _slow(platform, cfg):
        time.sleep(1.0)
        return True

    monkeypatch.setattr(gc, "platform_is_connected", _slow)
    started = time.monotonic()
    result = c._platform_connected("signal", {}, {}, {}, {})
    elapsed = time.monotonic() - started
    assert result is False
    assert elapsed < 0.9
