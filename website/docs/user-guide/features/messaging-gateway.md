---
sidebar_position: 13
title: "Messaging Gateway (WebUI Configuration)"
description: "Connect Telegram, DingTalk, Feishu, WeCom, WeChat, Slack and more from the WebUI Settings → Gateway panel"
---

# Messaging Gateway — WebUI Configuration

The [gateway](/docs/user-guide/features/overview) connects Intellect to messaging platforms (Telegram, DingTalk, Feishu, WeCom, personal WeChat, Slack, Discord, QQ, Email, …). The WebUI's **Settings → Gateway** panel lets you configure platforms — credentials, behavior switches, and enable/disable — without hand-editing `config.yaml` and `.env`.

## Where to find it

Open the WebUI → **Settings** → **Gateway** (the radio-tower icon in the settings sidebar). The panel has two sections:

- **Gateway daemon** — whether the gateway process is running, with Start / Stop / Restart buttons.
- **Platforms** — one card per supported platform, with a connection dot, a configured badge, and an *enabled* toggle. Click a card to edit its settings.

## How configuration is stored

Everything the panel writes lands in the **active profile's** home (`~/.intellect`, or `~/.intellect/agents/<name>` when you run named agents):

| Data | Stored in | Example |
|---|---|---|
| Credentials (tokens, secrets, ids) | `.env` | `TELEGRAM_BOT_TOKEN`, `FEISHU_APP_SECRET` |
| Behavior keys | `config.yaml` top-level platform section | `dingtalk.require_mention`, `telegram.allowed_chats` |
| Enabled toggle | `config.yaml` `platforms.<name>.enabled` | `platforms.weixin.enabled: false` |

This is exactly where the gateway loader reads from — the panel never stores gateway state inside the WebUI itself. Hand-written config and panel edits stay in sync because they are the same files.

**Secrets are write-only.** Saved values are never sent back to the browser — a configured secret shows a green "configured" marker and an empty input that says *leave blank to keep current*. Leave it blank to keep the stored value; type a new value to replace it; press the **✕ clear** button next to a configured credential to remove it on the next save.

Platforms without a dedicated form (Signal, Matrix, Mattermost, Home Assistant, plugin platforms, …) show a **generic key-value editor** that edits `platforms.<name>.extra` directly. Values are parsed as JSON when possible (`8645` → number, `true` → boolean, `[1, 2]` → list), so adapters see the types they expect. Two things to know about the generic editor:

- **Saving replaces the whole `extra` block** with what the form shows — deleting a row deletes that key.
- **Secret-shaped keys** (`password`, `token`, `secret`, …) are masked as `(set)` in the editor. Saving `(set)` back keeps the stored value; type over it to replace; delete the row to remove.

## Changes take effect on restart

Saving writes the files immediately; the running gateway picks them up on its next start. Use **Save & restart gateway** to do both in one click, or save now and restart later from the daemon section (or `intellect gateway restart`).

## Platform cheat sheet

| Platform | Where to get credentials |
|---|---|
| Telegram | [@BotFather](https://t.me/BotFather) → `/newbot` → bot token |
| DingTalk | [DingTalk Open Platform](https://open.dingtalk.com/) → app → Client ID (AppKey) + Client Secret (AppSecret), enable the robot |
| Feishu / Lark | [Feishu Open Platform](https://open.feishu.cn/) → custom app → App ID + App Secret; choose long-connection (websocket) or webhook mode |
| WeCom (smart bot) | WeCom admin → bot → Bot ID + Secret |
| WeCom callback (self-built app) | WeCom admin → self-built app → Corp ID, Corp Secret, Agent ID, callback Token + EncodingAESKey; the gateway binds a local HTTP port for the callback URL |
| WeChat (personal, iLink Bot) | iLink Bot API token + account ID — both are required before the adapter starts |
| Slack | Slack app → Bot User OAuth Token (`xoxb-…`); add App-Level Token (`xapp-…`) for Socket Mode |
| Discord | [Discord developer portal](https://discord.com/developers/applications) → bot token |
| WhatsApp | No API keys — scan the QR code when the gateway first starts; `dm_policy`/`group_policy` control who can talk to it |
| QQ Bot | [QQ Open Platform](https://q.qq.com/) → App ID + AppSecret — both are required before the adapter starts |
| Email | Mailbox address + (app) password + IMAP/SMTP hosts |

Platforms without a dedicated form (Signal, Matrix, Mattermost, Home Assistant, plugin platforms, …) show a **generic key-value editor** that edits `platforms.<name>.extra` directly. Values are parsed as JSON when possible (`8645` → number, `true` → boolean, `[1, 2]` → list), so adapters see the types they expect.## Enabled vs configured vs connected

Each platform card shows three independent facts:

- **Enabled toggle** — will the gateway *try* this platform on next start? An explicit off always wins: even if a credential exists in `.env`, a platform you disabled stays disabled (credentials are kept, so re-enabling doesn't need re-entry).
- **Configured badge** — are all required credentials present? For WeChat, that means *both* the token and the account ID: a half-credentialed platform stays disabled rather than starting an adapter that can only fail.
- **Connection dot** — live state from the running gateway: green = connected, amber = disconnected/reconnecting, red = fatal error (hover for the message, with anything token-shaped masked).

## FAQ

**I enabled a platform but it shows "not configured".**
At least one required credential is missing — open the card; required fields are marked with a red asterisk. The panel won't enable a platform whose required fields are empty.

**I disabled a platform but it came back after a restart.**
This usually means a credential env var is set somewhere the panel can't see (a shell export in the gateway's systemd unit, for example). The panel's disable writes `platforms.<name>.enabled: false`, which overrides `.env` credentials — but an env var exported *into the gateway process* with a separate enable switch (e.g. `WHATSAPP_ENABLED`) can still win. Remove the export or flip it to `false`.

**The gateway is managed by NixOS / Home Manager and saves fail.**
Managed installations refuse WebUI writes with a clear error — change the settings in your flake/configuration instead. This prevents a half-applied save where `.env` updates are silently dropped.

**I broke `config.yaml` by hand and now saves are refused.**
Good — that's deliberate. A save against a file that doesn't parse would replace your whole config. Fix the YAML syntax (or restore a backup) and try again.

**Do I need to restart the WebUI?**
No. Gateway settings only affect the gateway process; the WebUI reads them fresh on every page load.
