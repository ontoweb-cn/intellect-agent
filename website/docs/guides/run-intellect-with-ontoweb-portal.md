---
sidebar_position: 1
title: "Run Intellect Agent with ONTOWEB Portal"
description: "Start-to-finish walkthrough: subscribe, set up, switch models, enable gateway tools, and verify routing"
---

# Run Intellect Agent with ONTOWEB Portal

This guide walks you through running Intellect Agent on a [ONTOWEB Portal](https://portal.ontoweb.cn) subscription end to end — from signing up to verifying that every tool routes correctly. If you just want the overview of what the Portal is and what's in the subscription, see the [ONTOWEB Portal integration page](/integrations/ontoweb-portal). This page is the task script.

## Prerequisites

- Intellect Agent installed ([Quickstart](/getting-started/quickstart))
- A web browser on the machine you're setting up (or SSH port forwarding — see [OAuth over SSH](/guides/oauth-over-ssh))
- About 5 minutes

You do **not** need: an OpenAI key, an Anthropic key, a Firecrawl account, a FAL account, a Browser Use account, or any other per-vendor credential. That's the whole point.

## 1. Get a subscription

Open [portal.ontoweb.cn/manage-subscription](https://portal.ontoweb.cn/manage-subscription), sign up, and pick a plan.

Already subscribed? Skip to step 2.

## 2. Run the one-shot setup

```bash
intellect setup --portal
```

This single command does five things:

1. Opens your browser to portal.ontoweb.cn for OAuth login
2. Stores the refresh token at `~/.intellect/auth.json`
3. Sets `model.provider: ontoweb` in `~/.intellect/config.yaml`
4. Picks a default agentic model (`anthropic/claude-sonnet-4.6` or similar)
5. Turns on the Tool Gateway for web search, image generation, TTS, and browser automation

When it finishes, you're back at your terminal ready to chat.

### What if I'm SSH'd into a server?

OAuth needs a browser, but the loopback callback runs on the machine where Intellect is running. Two options:

```bash
# Option A: SSH port forwarding (preferred)
ssh -N -L 8642:127.0.0.1:8642 user@remote-host    # in a local terminal
intellect setup --portal                              # on the remote, open the printed URL in your local browser

# Option B: manual paste (for Cloud Shell, Codespaces, EC2 Instance Connect)
intellect auth add ontoweb --type oauth --manual-paste
# Then re-run `intellect setup --portal` to wire the provider + gateway
```

See [OAuth over SSH / Remote Hosts](/guides/oauth-over-ssh) for the full walkthrough including ProxyJump chains, mosh/tmux, and ControlMaster gotchas.

## 3. Verify it worked

```bash
intellect portal status
```

You should see:

```
  ONTOWEB Portal
  ───────────
  Auth:    ✓ logged in
  Portal:  https://portal.ontoweb.cn
  Model:   ✓ using OntoWeb as inference provider

  Tool Gateway
  ────────────
  Web search & extract  via ONTOWEB Portal
  Image generation      via ONTOWEB Portal
  Text-to-speech        via ONTOWEB Portal
  Browser automation    via ONTOWEB Portal
```

If any line shows something other than "via ONTOWEB Portal" or the auth line says "not logged in", jump to [Troubleshooting](#troubleshooting) below.

## 4. Run your first conversation

```bash
intellect chat
```

Try something that exercises both the model and the Tool Gateway:

```
Hey, search the web for "Intellect Agent release notes" and summarize the top 3 hits.
```

You should see Intellect call `web_search` (Firecrawl-backed, through the gateway) and respond with a summary. If the search runs and the response makes sense, you're done — the Portal is wired up end to end.

## 5. Pick the model you actually want

The default after `intellect setup --portal` is a sensible general-purpose model, but the whole point of the subscription is access to the full catalog. Switch with `/model` mid-session:

```bash
/model anthropic/claude-sonnet-4.6     # best general-purpose agentic
/model openai/gpt-5.4                  # strong reasoning + tool calling
/model google/gemini-2.5-pro           # huge context window
/model deepseek/deepseek-v3.2          # cost-effective coder
/model anthropic/claude-opus-4.6       # heavyweight for hard problems
```

Or pop the picker to browse:

```bash
/model
```

Pick a different default permanently:

```bash
# in your terminal, outside any session
intellect config set model.default anthropic/claude-sonnet-4.6
```

### Don't pick Intellect-4 for agent work

Intellect-4-70B and Intellect-4-405B are available on the Portal at deep discounts, but they're **chat/reasoning models**, not tool-call-tuned. They will struggle with multi-step agent loops. Use them via [OntoWeb Chat](https://chat.ontoweb.cn) for conversation/research work, or through the [subscription proxy](/user-guide/features/subscription-proxy) from non-agent tools. For Intellect Agent itself, stick to the frontier agentic models above.

The Portal's own [info page](https://portal.ontoweb.cn/info) carries this warning too — it's the official OntoWeb guidance, not just an Intellect-side opinion.

## 6. (Optional) Customize Tool Gateway routing

The gateway is opt-in per tool, not all-or-nothing. If you already have a Browserbase account and want to keep using it while routing web search and image generation through OntoWeb, that's supported:

```bash
intellect tools
# → Web search       → "OntoWeb Subscription"     (recommended)
# → Image generation → "OntoWeb Subscription"     (recommended)
# → Browser          → "Browserbase"           (your existing key)
# → TTS              → "OntoWeb Subscription"     (recommended)
```

These rows appear in `hermes tools` even before you've logged into Nous Portal — if you pick "Nous Subscription" without an active session, Hermes runs the Portal login inline (without changing your inference provider or your other tools).

Verify your mix with:

```bash
intellect portal tools
```

You'll see per-tool routing — `via ONTOWEB Portal` for the ones routed through the subscription, and the partner name (`browserbase`, `firecrawl`, etc.) for the ones using your own keys.

## 7. (Optional) Enable voice mode

Because the Tool Gateway includes OpenAI TTS, [voice mode](/user-guide/features/voice-mode) works without a separate OpenAI key:

```bash
intellect setup voice
# → pick "OntoWeb Subscription" for TTS
# → pick a speech-to-text backend (local faster-whisper is free, no setup)
```

Then in any messaging-platform session (Telegram, Discord, Signal, etc.), send a voice message and Intellect will transcribe it, respond, and reply with synthesized voice — all on your Portal subscription.

## 8. (Optional) Cron + always-on workflows

The Portal subscription works for [cron jobs](/user-guide/features/cron) and [batch processing](/user-guide/features/batch-processing) the same way it works for interactive chat — the OAuth refresh token is reused automatically. No additional setup; just schedule cron jobs and they'll bill against your subscription.

```bash
intellect cron create "every day at 9am" \
  "Search the web for top AI news and summarize the 5 most important stories" \
  --name "Daily AI news"
```

The cron job runs unattended, calls the model + web search + summarization all through your Portal subscription.

## Profiles and multi-user setups

If you use [Intellect profiles](/user-guide/profiles) (e.g. a separate config per project), the Portal refresh token is automatically shared across all profiles via a shared token store. Sign in once on any profile, and the rest pick it up automatically.

For team setups where multiple humans share a machine, each human has their own Portal account → each home directory holds its own `~/.intellect/auth.json` → no token sharing across users. This is the right boundary.

## Troubleshooting

### `intellect portal status` shows "not logged in" after `intellect setup --portal`

The OAuth flow didn't complete. Re-run it:

```bash
intellect auth add ontoweb --type oauth
```

If your browser doesn't open or the callback fails, you're likely on a remote/headless host — see [OAuth over SSH](/guides/oauth-over-ssh) for the port-forwarding and manual-paste workarounds.

### "Model: currently openrouter" (or some other provider) instead of "using OntoWeb as inference provider"

Your local config drifted. The OAuth worked but `model.provider` is still pointing at a different provider. Fix:

```bash
intellect config set model.provider ontoweb
```

Or interactively:

```bash
intellect model
# pick ONTOWEB Portal
```

Re-verify with `intellect portal status`.

### Tool Gateway tools showing partner names instead of "via ONTOWEB Portal"

Per-tool config is overriding the gateway. Run:

```bash
intellect tools
# pick "OntoWeb Subscription" for any tool you want gateway-routed
```

Some users intentionally mix — e.g. routing web through OntoWeb but using their own Browserbase key for browser. If that's intentional, leave it alone. If not, this command fixes it.

### "Re-authentication required" mid-session

Your Portal refresh token was invalidated (password change, manual revoke, session expiry). The token is now quarantined locally so Intellect doesn't replay it endlessly. Just log in again:

```bash
intellect auth add ontoweb
```

The quarantine clears automatically on successful re-login.

### Model I want isn't in the `/model` picker

The Portal catalog mirrors OpenRouter's model list (300+). If a model is missing, try typing the OpenRouter-style slug directly:

```bash
/model anthropic/claude-opus-4.6
/model openai/o1-2025-12-17
```

If a model is genuinely unavailable, [open an issue](https://gitee.com/ontoweb/intellect-agent/issues) — most gaps are routing config we can update.

### Billing not appearing on my Portal account

`intellect portal status` will tell you whether you're actually routing through the Portal or some other provider. Common causes:

- `model.provider` set to `openrouter`/`anthropic`/etc. instead of `ontoweb`
- An OAuth refresh failure that fell back to a different configured provider
- Multiple Intellect profiles where you're using the wrong one (check `intellect profile current`)

### Want to revoke and start clean

```bash
intellect auth remove ontoweb       # wipes the local refresh token
# Then re-run setup or remove the subscription from the Portal web UI
```

## What this gets you, in plain numbers

| Without Portal | With Portal |
|----------------|-------------|
| 1× OpenRouter / Anthropic / OpenAI key in `.env` | 1× OAuth refresh token, no `.env` keys |
| 1× Firecrawl key for web | Web routed through gateway |
| 1× FAL key for image gen | Image gen routed through gateway |
| 1× Browser Use / Browserbase key for browser | Browser routed through gateway |
| 1× OpenAI key for TTS / voice mode | TTS routed through gateway |
| 5 separate dashboards, top-ups, invoices | 1 subscription, 1 invoice |
| Cross-machine: replicate all 5 keys | Cross-machine: re-OAuth once |

That's the deal. If you're using more than two of those backends anyway, the subscription pays for itself.

## See also

- **[ONTOWEB Portal integration page](/integrations/ontoweb-portal)** — Overview of what's in the subscription
- **[Tool Gateway](/user-guide/features/tool-gateway)** — Full details on every gateway-routed tool
- **[Subscription proxy](/user-guide/features/subscription-proxy)** — Use your Portal subscription from non-Intellect tools
- **[Voice mode](/user-guide/features/voice-mode)** — Set up voice conversations on the Portal subscription
- **[OAuth over SSH](/guides/oauth-over-ssh)** — Remote / headless login patterns
- **[Profiles](/user-guide/profiles)** — Share one Portal login across multiple Intellect configurations
