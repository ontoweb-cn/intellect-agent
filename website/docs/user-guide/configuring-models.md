---
sidebar_position: 3
---

# Configuring Models

Intellect uses two kinds of model slots:

- **Main model** — what the agent thinks with. Every user message, every tool-call loop, every streamed response goes through this model.
- **Auxiliary models** — smaller side-jobs the agent offloads. Context compression, vision (image analysis), web-page summarization, approval scoring, MCP tool routing, session-title generation, and skill search. Each has its own slot and can be overridden independently.

This page covers configuring both from the CLI and config files.

:::tip Fastest path: ONTOWEB Portal
[ONTOWEB Portal](/user-guide/features/tool-gateway) provides 300+ models under one subscription. On a fresh install, run `intellect setup --portal` to log in and set OntoWeb as your provider in one command. Inspect what's wired up with `intellect portal status`.

- Portal subscribers also get **10% off token-billed providers**.
:::

:::note `model:` schema — empty string vs. mapping
On a brand-new install the bundled default config has `model: ""` (an empty string sentinel meaning "not configured yet"). The first time you run `intellect setup` or `intellect model`, that key is upgraded in-place to a mapping with `provider`, `default`, `base_url`, and `api_mode` sub-keys — the shape shown throughout this page and in [`profiles.md`](./profiles.md) / [`configuration.md`](./configuration.md). If you ever see an empty string in `config.yaml`, run `intellect model` and Intellect will write the dict form for you.
:::

## Setting the main model

### `intellect model` — the canonical way

```bash
intellect model            # Interactive provider + model picker
```

`intellect model` walks you through picking a provider, authenticating (OAuth flows open a browser; API-key providers prompt for the key), and then choosing a specific model from that provider's curated catalog. The choice is written to `model.provider` and `model.model` in `~/.intellect/config.yaml`.

### `/model` slash command (mid-session)

Inside any `intellect chat` session:

```
/model gpt-5.4 --provider openrouter             # session-only
/model gpt-5.4 --provider openrouter --global    # also persists to config.yaml
```

`--global` persists the change to `config.yaml` and also switches the running session in-place.

### `intellect setup`

The full interactive setup wizard also covers model configuration:

```bash
intellect setup model   # Just the model section
intellect setup         # Full wizard
```

### Direct config edit

Edit `~/.intellect/config.yaml` and restart whatever reads it:

```yaml
model:
  provider: openrouter
  default: anthropic/claude-opus-4.7
  base_url: ''        # cleared on provider switch
  api_mode: chat_completions
```

See the [Configuration reference](./configuration.md) for the full schema.

## Setting auxiliary models

Every auxiliary task defaults to `auto` — meaning Intellect uses your main model for that job too. Override a specific task when you want a cheaper or faster model for a side-job.

### Running `intellect model` for auxiliaries

Instead of hand-editing YAML, run `intellect model` and pick **"Configure auxiliary models"** from the menu. You'll get an interactive per-task picker:

```
$ intellect model
→ Configure auxiliary models

[ ] vision               currently: auto / main model
[ ] web_extract          currently: auto / main model
[ ] title_generation     currently: openrouter / google/gemini-3-flash-preview
[ ] compression          currently: auto / main model
[ ] approval             currently: auto / main model
[ ] triage_specifier     currently: auto / main model
[ ] kanban_decomposer    currently: auto / main model
[ ] profile_describer    currently: auto / main model
```

Select a task, pick a provider (OAuth flows open a browser; API-key providers prompt), pick a model. The change persists to `auxiliary.<task>.*` in `config.yaml`.

### Common override patterns

| Task | When to override |
|---|---|
| **Title Gen** | Almost always. A $0.10/M flash model writes session titles as well as Opus. Default config sets this to `google/gemini-3-flash-preview` on OpenRouter. |
| **Vision** | When your main model lacks vision support. Point it at `google/gemini-2.5-flash` or `gpt-4o-mini`. |
| **Compression** | When you're burning reasoning tokens on Opus/M2.7 just to summarize context. A fast chat model does the job at 1/50th the cost. |
| **Approval** | For `approval_mode: smart` — a fast/cheap model (haiku, flash, gpt-5-mini) decides whether to auto-approve low-risk commands. Expensive models here are waste. |
| **Web Extract** | When you use `web_extract` heavily. Same logic as compression — summarization doesn't need reasoning. |
| **Skills Hub** | `intellect skills search` uses this. Usually fine at `auto`. |
| **MCP** | MCP tool routing. Usually fine at `auto`. |

### Direct config: auxiliary override

**Main model:**
```yaml
model:
  provider: openrouter
  default: anthropic/claude-opus-4.7
  base_url: ''        # cleared on provider switch
  api_mode: chat_completions
```

**Auxiliary override (example — vision on gemini-flash):**
```yaml
auxiliary:
  vision:
    provider: openrouter
    model: google/gemini-2.5-flash
    base_url: ''
    api_key: ''
    timeout: 120
    extra_body: {}
    download_timeout: 30
```

**Auxiliary on auto (default):**
```yaml
auxiliary:
  compression:
    provider: auto
    model: ''
    base_url: ''
    # ... other fields unchanged
```

`provider: auto` with `model: ''` tells Intellect to use the main model for that task.

## When does it take effect?

- **CLI** (`intellect chat`): next `intellect chat` invocation.
- **Gateway** (Telegram, Discord, Slack, etc.): next *new* session. Existing sessions keep their model. Restart the gateway (`intellect gateway restart`) if you want to force all sessions to pick up the change.

Changes never invalidate prompt caches on running sessions. That's deliberate: swapping the main model inside a session requires a cache reset (the system prompt contains model-specific content), and we reserve that for the explicit `/model` slash command inside chat.

## Troubleshooting

### Main model didn't change in my running chat

Expected. Config changes apply to new sessions only. The currently-open chat is a live agent process — it keeps whatever model it was spawned with. Use `/model <name>` inside the chat to hot-swap that specific session.

### Auxiliary override "didn't take effect"

Three things to check:

1. **Did you start a new session?** Existing chats don't re-read config.
2. **Is `provider` set to something other than `auto`?** If `provider: auto`, the task is still using your main model. Set a real provider explicitly.
3. **Is the provider authenticated?** If you assigned `minimax` to a task but don't have a MiniMax API key, that task falls back to the openrouter default and logs a warning in `agent.log`.

### I picked a model but Intellect switched providers on me

On OpenRouter (or any aggregator), bare model names resolve *within* the aggregator first. So `claude-sonnet-4` on OpenRouter becomes `anthropic/claude-sonnet-4.6`, staying on your OpenRouter auth. But if you typed `claude-sonnet-4` on a native Anthropic auth, it would stay as `claude-sonnet-4-6`. If you see an unexpected provider switch, check that your current provider is what you expect.

## Custom aliases

Define your own short names for models you reach for often, then use `/model <alias>` in the CLI or any messaging platform. There are two equivalent formats — pick whichever fits your workflow.

**Canonical (top-level `model_aliases:`)** — full control over provider + base_url:

```yaml
# ~/.intellect/config.yaml
model_aliases:
  fav:
    model: claude-sonnet-4.6
    provider: anthropic
  grok:
    model: grok-4
    provider: x-ai
```

**Short string form (`model.aliases.<name>: provider/model`)** — convenient from the shell because `intellect config set` only writes scalar values, but it can't carry a custom `base_url`:

```bash
intellect config set model.aliases.fav anthropic/claude-opus-4.6
intellect config set model.aliases.grok x-ai/grok-4
```

Both paths feed the same loader (`intellect_cli/model_switch.py`). Entries declared in `model_aliases:` take precedence over `model.aliases:` entries with the same name.

Then `/model fav` or `/model grok` in chat. User aliases shadow built-in short names (`sonnet`, `kimi`, `opus`, etc.). See [Custom model aliases](/reference/slash-commands#custom-model-aliases) for the full reference.

To inspect what the CLI will actually use right now: `intellect config show | grep '^model\.'` and `intellect status`.
