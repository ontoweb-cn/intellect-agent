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

## Cross-machine relay (B2-4)

A bot can DM bots on OTHER machines you control. Declare peers in the
sender's config:

```yaml
bot_mode:
  enabled: true
  peers:
    beta:
      url: https://peer-host        # peer gateway front end
      profile: beta                  # remote profile name (default = key)
      api_key_env: BETA_PEER_KEY    # the peer profile's API server key
```

Delivery is fire-and-forget HTTP to the peer's `/p/<profile>/` surface
(B1-4 routing + B1-5 per-profile auth): the attributed message lands in
the peer's Bot Chat session, the peer runs its turn, and its reply is
written back into YOUR Bot Chat session — arriving as a later turn.

**Trust boundary (read before enabling):** a peer entry hands the peer's
API key to this machine and lets this machine write sessions on the peer
— cross-machine Bot Mode is an owner-managed shared-secret arrangement
between machines YOU control. It is still not multi-user. Prefer
`api_key_env` over inline keys, use HTTPS URLs, and note that
cross-machine reply loops are bounded by bot behavior and your
topology, not mechanically — keep peer bot personas non-auto-reactive.

## Security model

- Profiles are isolated from each other (own home, own keys, own sessions);
  Bot Mode opens exactly one narrow door between them: the Bot Chat
  session, with an attribution prefix the receiving model is told to keep.
- Per-profile WS/API auth is unchanged (`TUI_AUTH_TOKEN_<PROFILE>`, API
  keys stay per-profile `.env`). Bots are your profiles — this is not a
  multi-user feature.
