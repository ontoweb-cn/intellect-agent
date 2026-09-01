---
sidebar_position: 5
title: Multiplex Gateways
description: Serve multiple profiles from one supervisor process with per-profile URL routing.
---

# Multiplex Gateways

Multiplex lets one supervisor process serve **several profiles at once**:
each profile keeps a fully isolated `INTELLECT_HOME` (own config, API keys,
sessions, skills, memory), while a single front end owns the only external
HTTP/WebSocket listener and routes traffic by profile URL prefix.

This is a single-owner feature: profiles are isolated **from each other**,
not from each other's administrator. Multi-user members/teams remain out of
scope. See [Profiles](./profiles.md) for profile basics.

## Quick start

```bash
# serve the default profile plus every valid profile under ~/.intellect/profiles/
intellect gateway run --multiplex

# restrict secondaries (the default profile is always served)
# gateway.multiplex_profile_allowlist in config.yaml:
#   gateway:
#     multiplex_profile_allowlist: [coder, writer]
intellect gateway run --multiplex
```

The supervisor spawns one gateway child per profile, waits for each child's
control socket to answer, and restarts dead children with exponential
backoff — one child's crash never affects the others. `--multiplex` conflicts
with `--replace` and `--quiet`.

## URL routing

| URL | Served by |
|---|---|
| `/v1/...`, `/webhooks/...` (no prefix) | the **default** profile |
| `/p/<name>/v1/...` | profile `<name>` (prefix stripped before forwarding) |
| `/p/<unknown>/...` | structured 404 |
| known profile, listener not ready | structured 503 |

Two sites are hosted when the corresponding platform is enabled anywhere in
the serve set: the **api site** (the default profile's `platforms.api_server`
host/port, default `127.0.0.1:8642`) and the **webhook site** (default port
`8644`). Webhook providers for different profiles point at the **same port**
with different prefixes — `/p/alpha/webhooks/github` and
`/p/beta/webhooks/github` fan out to the right profile.

Children bind only internal loopback ephemeral ports; the supervisor
discovers them automatically. A secondary that pins an explicit `port` or a
non-loopback `host` for a listener platform is **rejected at startup** —
remove the pin so the front end can serve it.

## Ports, credentials and locks

- **Ports:** only the front end binds externally (the default profile's
  configured host/port). Children are loopback-internal by construction.
- **Credentials:** every profile uses its own `.env` and config inside its
  own home. The front end holds no secrets — each child enforces its own
  `API_SERVER_KEY`, webhook HMAC secrets, and WebSocket token.
- **Platform locks:** connecting two profiles with the SAME platform identity
  (e.g. one Telegram bot token) is blocked by the machine-local scoped lock —
  only one profile can own a given bot identity. Give each profile its own
  bot/token. `intellect doctor` reports these conflicts up front.

## Auth model (explicit tradeoff)

- **HTTP:** authenticated exactly like a standalone gateway — per profile.
  Each child checks its own `API_SERVER_KEY`; the front end adds nothing.
- **WebSocket:** `TUI_AUTH_TOKEN_<PROFILE>` (e.g. `TUI_AUTH_TOKEN_CODER`) is
  checked first per child, falling back to the global `TUI_AUTH_TOKEN`.
  Under multiplex, profile A's token cannot open profile B's endpoint.
  The guarded default (no tokens set) remains open-local — the same trust
  model as a standalone gateway. This is a deliberate single-owner tradeoff:
  tokens isolate profiles from each other, not people from the machine.

## Observability

- `intellect gateway status` — under the supervisor's profile this renders
  the multiplex topology: per-profile state, pid, restarts, and bound
  listener ports.
- `GET /multiplex/status` on the api site — live topology as JSON.
- Control socket `identify`/`status` — the supervisor's socket answers with
  `role: "supervisor"` plus the live `served_profiles` snapshot; each child's
  `identify` carries its own `profile` name.
- `intellect doctor` — validates the serve set: pinned listener bindings
  (startup-rejected), duplicate platform credentials across profiles.

## Running without multiplex

Nothing changes: `intellect gateway run` (no `--multiplex`) never spawns a
supervisor, never sets the child env flag, and behaves byte-for-byte as
before. The fail-closed WS guard for `/p/` paths remains the default.
