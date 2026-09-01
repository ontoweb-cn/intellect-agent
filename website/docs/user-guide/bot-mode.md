---
sidebar_position: 6
title: Bot Mode
description: Let your profiles talk to each other — DM-able bots with a local roster.
---

# Bot Mode

Bot Mode (opt-in) treats your [profiles](./profiles.md) as DM-able agents:
one roster, one `message_agent` tool, and a fire-and-forget DM transport —
so `alpha` can hand work to `coder`, and the answer comes back as a later
message. Same single-owner boundary as multiplex: bots are YOUR profiles,
not a multi-user system.

## Enable

```yaml
# config.yaml (per profile — enable on profiles that should send DMs)
bot_mode:
  enabled: true
  max_dm_depth: 3   # chained bot→bot DM hop cap (loop safety)
```

## Roster

```bash
intellect bots
```

Lists every profile in the multiplex serve set with its online status
(live gateway control socket), configured model, and a deterministic blob
avatar — the same name always renders the same face. The roster is
materialized to `~/.intellect/bot_mode/roster.json` by the gateway
supervisor. Offline is normal: offline bots still receive DMs.

## DMs: `message_agent`

Inside a session titled **Bot Chat**, the agent gets the `message_agent`
tool:

```
message_agent(target="coder", message="please review PR #123")
```

- The message lands in `coder`'s own Bot Chat session with a server-side
  attribution prefix. The target does NOT need to be online — its session
  holds the message, and a background `chat` run is what wakes it.
- The reply (if the target sends one) arrives as a LATER message in your
  session — fire-and-forget; the tool result only confirms delivery.
- Message bodies never pass through shell/argv: they travel via a 0o600
  file in a 0o700 directory, deleted after reading.
- Dispatch is double-gated: the tool only exists in Bot Chat sessions, and
  execution re-validates the session title and owning profile — forged
  calls from other sessions get a structured error, never a delivery.
- `max_dm_depth` caps chained bot→bot DMs (depth 3 = A→B→C, who may not
  DM further) so two bots can never loop forever.

## Security model

- Profiles are isolated from each other (own home, own keys, own sessions);
  Bot Mode opens exactly one narrow door between them: the Bot Chat
  session, with an attribution prefix the receiving model is told to keep.
- Per-profile WS/API auth is unchanged (`TUI_AUTH_TOKEN_<PROFILE>`, API
  keys stay per-profile `.env`). Bots are your profiles — this is not a
  multi-user feature.
