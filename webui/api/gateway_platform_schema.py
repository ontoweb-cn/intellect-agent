"""Declarative field schemas for the WebUI gateway-platform config UI.

Each supported platform declares its credential fields (written to the
profile's ``.env``) and behavior fields (written to the top-level platform
section of config.yaml — the same place ``gateway/config.py``'s loader
bridges from, e.g. ``dingtalk.require_mention``). The WebUI API in
``gateway_platform_config.py`` renders/validates/saves purely from these
declarations; the frontend renders forms generically from the GET payload.

Security invariant: secrets are write-only. Nothing in this module (or the
API built on it) ever returns a secret value — GET responses carry only
``set: true/false`` markers for env-backed fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

FIELD_TYPES = ("text", "secret", "bool", "int", "list", "select")


@dataclass(frozen=True)
class PlatformField:
    """One form field on a platform's config form.

    ``target`` routes the write: ``env:ENV_NAME`` → ``.env`` via
    ``save_env_value`` (regardless of secret/text type — client ids and app
    keys live there too, mirroring what the gateway loader reads);
    ``yaml:<section>.<key>`` → top-level platform section of config.yaml.
    """

    key: str
    label: str
    target: str
    type: str = "text"
    required: bool = False
    options: Tuple[str, ...] = ()
    placeholder: str = ""
    help: str = ""


@dataclass(frozen=True)
class PlatformSchema:
    name: str
    label: str
    description: str = ""
    docs_url: str = ""
    fields: Tuple[PlatformField, ...] = ()
    # Env vars that alone auto-enable the platform at load time (tri-state
    # switches like WHATSAPP_ENABLED) — used by the WebUI to mirror the
    # loader's implicit-enabled behavior for platforms whose credentials
    # aren't env-driven (WhatsApp pairs via QR at gateway start).
    enable_env: Tuple[str, ...] = ()

    @property
    def env_fields(self) -> List[PlatformField]:
        return [f for f in self.fields if f.target.startswith("env:")]

    @property
    def yaml_fields(self) -> List[PlatformField]:
        return [f for f in self.fields if f.target.startswith("yaml:")]

    @property
    def required_fields(self) -> List[PlatformField]:
        return [f for f in self.fields if f.required]

    def field(self, key: str) -> Optional[PlatformField]:
        for f in self.fields:
            if f.key == key:
                return f
        return None


_TELEGRAM = PlatformSchema(
    name="telegram",
    label="Telegram",
    description="Bot API via @BotFather token.",
    docs_url="https://core.telegram.org/bots/tutorial#obtain-your-bot-token",
    fields=(
        PlatformField(
            key="token",
            label="Bot Token",
            target="env:TELEGRAM_BOT_TOKEN",
            type="secret",
            required=True,
            placeholder="123456:ABC-DEF...",
            help="Create via @BotFather /newbot",
        ),
        PlatformField(
            key="require_mention",
            label="Require @mention in groups",
            target="yaml:telegram.require_mention",
            type="bool",
        ),
        PlatformField(
            key="allowed_chats",
            label="Allowed chats (whitelist)",
            target="yaml:telegram.allowed_chats",
            type="list",
            help="Chat IDs; empty = all chats",
        ),
        PlatformField(
            key="group_allowed_chats",
            label="Allowed group chats",
            target="yaml:telegram.group_allowed_chats",
            type="list",
        ),
        PlatformField(
            key="allow_from",
            label="Allowed users",
            target="yaml:telegram.allow_from",
            type="list",
            help="Usernames or IDs; empty = everyone",
        ),
    ),
)

_DINGTALK = PlatformSchema(
    name="dingtalk",
    label="DingTalk",
    description="Enterprise robot via DingTalk Open Platform.",
    docs_url="https://open.dingtalk.com/document/",
    fields=(
        PlatformField(
            key="client_id",
            label="Client ID (AppKey)",
            target="env:DINGTALK_CLIENT_ID",
            type="text",
            required=True,
        ),
        PlatformField(
            key="client_secret",
            label="Client Secret (AppSecret)",
            target="env:DINGTALK_CLIENT_SECRET",
            type="secret",
            required=True,
        ),
        PlatformField(
            key="require_mention",
            label="Require @mention in groups",
            target="yaml:dingtalk.require_mention",
            type="bool",
        ),
        PlatformField(
            key="allowed_chats",
            label="Allowed group chats (whitelist)",
            target="yaml:dingtalk.allowed_chats",
            type="list",
            help="Conversation IDs; empty = all",
        ),
        PlatformField(
            key="allowed_users",
            label="Allowed users",
            target="yaml:dingtalk.allowed_users",
            type="list",
            help="User IDs; empty = everyone",
        ),
    ),
)

_FEISHU = PlatformSchema(
    name="feishu",
    label="Feishu / Lark",
    description="App via Feishu Open Platform (long connection or webhook).",
    docs_url="https://open.feishu.cn/document/home/introduction-to-custom-app-protocols/self-built-application-development-process",
    fields=(
        PlatformField(
            key="app_id",
            label="App ID",
            target="env:FEISHU_APP_ID",
            type="text",
            required=True,
        ),
        PlatformField(
            key="app_secret",
            label="App Secret",
            target="env:FEISHU_APP_SECRET",
            type="secret",
            required=True,
        ),
        PlatformField(
            key="domain",
            label="Domain",
            target="env:FEISHU_DOMAIN",
            type="select",
            options=("feishu", "lark"),
        ),
        PlatformField(
            key="connection_mode",
            label="Connection mode",
            target="env:FEISHU_CONNECTION_MODE",
            type="select",
            options=("websocket", "webhook"),
        ),
        PlatformField(
            key="encrypt_key",
            label="Encrypt Key",
            target="env:FEISHU_ENCRYPT_KEY",
            type="secret",
            help="Needed for webhook mode event encryption",
        ),
        PlatformField(
            key="verification_token",
            label="Verification Token",
            target="env:FEISHU_VERIFICATION_TOKEN",
            type="secret",
        ),
    ),
)

_WECOM = PlatformSchema(
    name="wecom",
    label="WeCom (Enterprise WeChat)",
    description="WeCom bot via websocket smart bot.",
    docs_url="https://developer.work.weixin.qq.com/document/",
    fields=(
        PlatformField(
            key="bot_id",
            label="Bot ID",
            target="env:WECOM_BOT_ID",
            type="text",
            required=True,
        ),
        PlatformField(
            key="secret",
            label="Secret",
            target="env:WECOM_SECRET",
            type="secret",
            required=True,
        ),
        PlatformField(
            key="websocket_url",
            label="Websocket URL",
            target="env:WECOM_WEBSOCKET_URL",
            type="text",
        ),
    ),
)

_WECOM_CALLBACK = PlatformSchema(
    name="wecom_callback",
    label="WeCom Callback (self-built app)",
    description="WeCom self-built app via HTTP callback; binds a local port.",
    docs_url="https://developer.work.weixin.qq.com/document/path/90930",
    fields=(
        PlatformField(
            key="corp_id",
            label="Corp ID",
            target="env:WECOM_CALLBACK_CORP_ID",
            type="text",
            required=True,
        ),
        PlatformField(
            key="corp_secret",
            label="Corp Secret",
            target="env:WECOM_CALLBACK_CORP_SECRET",
            type="secret",
            required=True,
        ),
        PlatformField(
            key="agent_id",
            label="Agent ID",
            target="env:WECOM_CALLBACK_AGENT_ID",
            type="text",
        ),
        PlatformField(
            key="token",
            label="Callback Token",
            target="env:WECOM_CALLBACK_TOKEN",
            type="secret",
        ),
        PlatformField(
            key="encoding_aes_key",
            label="EncodingAESKey",
            target="env:WECOM_CALLBACK_ENCODING_AES_KEY",
            type="secret",
        ),
        PlatformField(
            key="host",
            label="Listen host",
            target="env:WECOM_CALLBACK_HOST",
            type="text",
            placeholder="127.0.0.1",
        ),
        PlatformField(
            key="port",
            label="Listen port",
            target="env:WECOM_CALLBACK_PORT",
            type="int",
            placeholder="8645",
        ),
    ),
)

_WEIXIN = PlatformSchema(
    name="weixin",
    label="WeChat (personal, iLink Bot)",
    description="Personal WeChat via the iLink Bot API.",
    docs_url="https://open.weixin.qq.com/",
    fields=(
        PlatformField(
            key="token",
            label="Bot Token",
            target="env:WEIXIN_TOKEN",
            type="secret",
            required=True,
        ),
        PlatformField(
            key="account_id",
            label="Account ID",
            target="env:WEIXIN_ACCOUNT_ID",
            type="text",
            required=True,
        ),
        PlatformField(
            key="dm_policy",
            label="DM policy",
            target="yaml:weixin.dm_policy",
            type="select",
            options=("open", "allowlist", "disabled"),
        ),
        PlatformField(
            key="allow_from",
            label="DM allowlist",
            target="yaml:weixin.allow_from",
            type="list",
            help="Used when DM policy is allowlist",
        ),
        PlatformField(
            key="group_policy",
            label="Group policy",
            target="yaml:weixin.group_policy",
            type="select",
            options=("open", "allowlist", "disabled"),
        ),
    ),
)

_SLACK = PlatformSchema(
    name="slack",
    label="Slack",
    description="Bot via Slack app (bot token; app token for Socket Mode).",
    docs_url="https://api.slack.com/quickstart",
    fields=(
        PlatformField(
            key="token",
            label="Bot Token (xoxb-...)",
            target="env:SLACK_BOT_TOKEN",
            type="secret",
            required=True,
            placeholder="xoxb-...",
            help="OAuth Bot User OAuth Token from the Slack app settings",
        ),
        PlatformField(
            key="app_token",
            label="App-Level Token (xapp-...)",
            target="env:SLACK_APP_TOKEN",
            type="secret",
            placeholder="xapp-...",
            help="Needed for Socket Mode connections",
        ),
        PlatformField(
            key="require_mention",
            label="Require @mention in channels",
            target="yaml:slack.require_mention",
            type="bool",
        ),
        PlatformField(
            key="free_response_channels",
            label="Free-response channels",
            target="yaml:slack.free_response_channels",
            type="list",
            help="Channel IDs where the bot responds without @mention",
        ),
    ),
)

_DISCORD = PlatformSchema(
    name="discord",
    label="Discord",
    description="Bot via Discord developer application.",
    docs_url="https://discord.com/developers/applications",
    fields=(
        PlatformField(
            key="token",
            label="Bot Token",
            target="env:DISCORD_BOT_TOKEN",
            type="secret",
            required=True,
            help="Bot token from the Discord developer portal",
        ),
        PlatformField(
            key="require_mention",
            label="Require @mention in channels",
            target="yaml:discord.require_mention",
            type="bool",
        ),
        PlatformField(
            key="free_response_channels",
            label="Free-response channels",
            target="yaml:discord.free_response_channels",
            type="list",
            help="Channel IDs where the bot responds without @mention",
        ),
        PlatformField(
            key="allowed_users",
            label="Allowed users",
            target="env:DISCORD_ALLOWED_USERS",
            type="list",
            help="User IDs; empty = everyone",
        ),
    ),
)

_WHATSAPP = PlatformSchema(
    name="whatsapp",
    label="WhatsApp",
    description="Paired via the bridge at gateway start (QR scan); no API keys.",
    docs_url="https://web.whatsapp.com/",
    enable_env=("WHATSAPP_ENABLED",),
    fields=(
        PlatformField(
            key="require_mention",
            label="Require @mention in groups",
            target="yaml:whatsapp.require_mention",
            type="bool",
        ),
        PlatformField(
            key="dm_policy",
            label="DM policy",
            target="yaml:whatsapp.dm_policy",
            type="select",
            options=("open", "allowlist", "disabled"),
        ),
        PlatformField(
            key="allow_from",
            label="DM allowlist",
            target="yaml:whatsapp.allow_from",
            type="list",
            help="Sender JIDs; used when DM policy is allowlist",
        ),
        PlatformField(
            key="group_policy",
            label="Group policy",
            target="yaml:whatsapp.group_policy",
            type="select",
            options=("open", "allowlist", "disabled"),
        ),
        PlatformField(
            key="group_allow_from",
            label="Group allowlist",
            target="yaml:whatsapp.group_allow_from",
            type="list",
            help="Group JIDs; used when group policy is allowlist",
        ),
    ),
)

_QQBOT = PlatformSchema(
    name="qqbot",
    label="QQ Bot",
    description="QQ official bot (Open Platform).",
    docs_url="https://q.qq.com/",
    fields=(
        PlatformField(
            key="app_id",
            label="App ID",
            target="env:QQ_APP_ID",
            type="text",
            required=True,
        ),
        PlatformField(
            key="client_secret",
            label="Client Secret (AppSecret)",
            target="env:QQ_CLIENT_SECRET",
            type="secret",
            required=True,
        ),
        PlatformField(
            key="allow_from",
            label="Allowed users",
            target="yaml:qqbot.allow_from",
            type="list",
            help="User IDs; empty = everyone",
        ),
        PlatformField(
            key="group_allow_from",
            label="Allowed group users",
            target="yaml:qqbot.group_allow_from",
            type="list",
        ),
    ),
)

_EMAIL = PlatformSchema(
    name="email",
    label="Email",
    description="IMAP inbox polling + SMTP replies.",
    docs_url="",
    fields=(
        PlatformField(
            key="address",
            label="Email address",
            target="env:EMAIL_ADDRESS",
            type="text",
            required=True,
        ),
        PlatformField(
            key="password",
            label="Password / app password",
            target="env:EMAIL_PASSWORD",
            type="secret",
            required=True,
        ),
        PlatformField(
            key="imap_host",
            label="IMAP host",
            target="env:EMAIL_IMAP_HOST",
            type="text",
            required=True,
            placeholder="imap.gmail.com",
        ),
        PlatformField(
            key="smtp_host",
            label="SMTP host",
            target="env:EMAIL_SMTP_HOST",
            type="text",
            required=True,
            placeholder="smtp.gmail.com",
        ),
        PlatformField(
            key="imap_port",
            label="IMAP port",
            target="env:EMAIL_IMAP_PORT",
            type="int",
            placeholder="993",
        ),
        PlatformField(
            key="smtp_port",
            label="SMTP port",
            target="env:EMAIL_SMTP_PORT",
            type="int",
            placeholder="587",
        ),
        PlatformField(
            key="poll_interval",
            label="Poll interval (seconds)",
            target="env:EMAIL_POLL_INTERVAL",
            type="int",
            placeholder="15",
        ),
    ),
)

# P0+P1 batches — see docs/plans/2026-10-09-webui-gateway-config.md (D4).
# Platforms without a schema here fall back to the generic extra editor in
# gateway_platform_config.py.
PLATFORM_SCHEMAS: Dict[str, PlatformSchema] = {
    s.name: s
    for s in (
        _TELEGRAM,
        _DINGTALK,
        _FEISHU,
        _WECOM,
        _WECOM_CALLBACK,
        _WEIXIN,
        _SLACK,
        _DISCORD,
        _WHATSAPP,
        _QQBOT,
        _EMAIL,
    )
}


def get_schema(name: str) -> Optional[PlatformSchema]:
    return PLATFORM_SCHEMAS.get(str(name or "").strip().lower())


def normalize_value(field_def: PlatformField, value: Any) -> Tuple[Optional[Any], Optional[str]]:
    """Coerce and validate an incoming field value.

    Returns ``(normalized, error)`` — ``error is None`` on success. ``None``
    normalized means "clear the stored value" (empty string / null input).
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    if field_def.type == "bool":
        if isinstance(value, bool):
            return value, None
        low = str(value).strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True, None
        if low in ("false", "0", "no", "off"):
            return False, None
        return None, f"{field_def.key}: expected a boolean"
    if field_def.type == "int":
        try:
            return int(str(value).strip()), None
        except (TypeError, ValueError):
            return None, f"{field_def.key}: expected an integer"
    if field_def.type == "list":
        if isinstance(value, (list, tuple)):
            items = [str(v).strip() for v in value if str(v).strip()]
            return items, None
        items = [p.strip() for p in str(value).replace("\n", ",").split(",") if p.strip()]
        return items, None
    if field_def.type == "select":
        low = str(value).strip().lower()
        if low not in field_def.options:
            return None, f"{field_def.key}: must be one of {', '.join(field_def.options)}"
        return low, None
    # text / secret
    text = str(value).replace("\n", "").replace("\r", "").strip()
    if not text:
        return None, None
    return text, None
