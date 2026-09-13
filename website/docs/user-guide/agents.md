---
sidebar_position: 2
---

# Agents: Running Multiple Isolated Homes

:::info Renamed from "Profiles"
As of the profile→agent rename, the isolation unit is called an **agent**
(agent home). CLI: `intellect agent`. On-disk: `~/.intellect/agents/<name>/`.
Legacy `intellect agent`, `-p`/`--profile`, and `~/.intellect/agents/` still work
(legacy homes migrate to `agents/`).
:::

Run multiple independent Intellect agents on the same machine — each with its own config, API keys, memory, sessions, skills, and gateway state.

## What are agents?

An **agent** (historically called a *profile*) is a separate Intellect home directory. Each agent gets its own directory containing its own `config.yaml`, `.env`, `SOUL.md`, memories, sessions, skills, cron jobs, and state database. Agents let you run separate instances for different purposes — a coding assistant, a personal bot, a research agent — without mixing up Intellect state.

When you create an agent, it automatically becomes its own command. Create an agent called `coder` and you immediately have `coder chat`, `coder setup`, `coder gateway start`, etc.

## Quick start

```bash
intellect agent create coder       # creates agent + "coder" command alias
coder setup                        # configure API keys and model
coder chat                         # start chatting
```

That's it. `coder` is now its own Intellect agent with its own config, memory, and state.

:::caution Temporary: agent management disabled by default
As of 2026-06, **`agents.management_enabled` defaults to `false`** (legacy key `profiles.management_enabled` is still honored). While disabled:

- **CLI:** `intellect agent create|use|delete|rename|import|install|alias` are blocked; **`intellect -a <existing>`** still works.
- **WebUI:** Agents panel and agent switch/create/delete are hidden; sessions are scoped to the **default** agent only.

To re-enable management: set `agents.management_enabled: true` in `config.yaml` and restart the gateway/WebUI.

**Gateway `/journey`:** always uses the process agent home (`intellect -a` / how the gateway was started), not messaging-session switching.

**Single-user:** Intellect Agent does not provide multi-user members/teams. Isolation is agents only. Config key `members.enabled` is ignored.

**Restore inventory (all files and functions):** `docs/plans/2026-06-profile-management-disabled-restore.md` in the intellect-agent repo (WebUI index: `intellect-webui/docs/plans/profile-management-disabled-restore.md`).
:::

## Creating an agent

:::tip
Quickest setup: run `intellect setup --portal` inside the new agent to wire up models + tools at once. See [ONTOWEB Portal](/integrations/ontoweb-portal).
:::

### Blank agent

```bash
intellect agent create mybot
```

Creates a fresh agent with bundled skills seeded. Run `mybot setup` to configure API keys, model, and gateway tokens.

If you plan to use this agent as a kanban worker (or want the kanban orchestrator to route work to it), pass `--description "<role>"` at create time so the orchestrator knows what it's good at:

```bash
intellect agent create researcher --description "Reads source code and external docs, writes findings."
```

You can also set or auto-generate the description later with `intellect agent describe` — see the [Kanban guide](./features/kanban#auto-vs-manual-orchestration) for the full routing model.

### Clone config only (`--clone`)

```bash
intellect agent create work --clone
```

Copies your current agent's `config.yaml`, `.env`, and `SOUL.md` into the new agent. Same API keys and model, but fresh sessions and memory. Edit `~/.intellect/agents/work/.env` for different API keys, or `~/.intellect/agents/work/SOUL.md` for a different personality.

### Clone everything (`--clone-all`)

```bash
intellect agent create backup --clone-all
```

Copies **everything** — config, API keys, personality, all memories, full session history, skills, cron jobs, plugins. A complete snapshot. Useful for backups or forking an agent that already has context.

### Clone from a specific profile

```bash
intellect agent create work --clone --clone-from coder
```

:::tip Honcho memory + profiles
When Honcho is enabled, `--clone` automatically creates a dedicated AI peer for the new agent while sharing the same user workspace. Each profile builds its own observations and identity. See [Honcho -- Multi-agent / Profiles](./features/memory-providers.md#honcho) for details.
:::

## Using profiles

### Command aliases

Every profile automatically gets a command alias at `~/.local/bin/<name>`:

```bash
coder chat                    # chat with the coder agent
coder setup                   # configure coder's settings
coder gateway start           # start coder's gateway
coder doctor                  # check coder's health
coder skills list             # list coder's skills
coder config set model.default anthropic/claude-sonnet-4
```

The alias works with every intellect subcommand — it's just `intellect -a <name>` under the hood.

### The `-p` flag

You can also target an agent explicitly with any command:

```bash
intellect -a coder chat
intellect --profile=coder doctor
intellect chat -p coder -q "hello"    # works in any position
```

### Sticky default (`intellect agent use`)

```bash
intellect agent use coder
intellect chat                   # now targets coder
intellect tools                  # configures coder's tools
intellect agent use default    # switch back
```

Sets a default so plain `intellect` commands target that profile. Like `kubectl config use-context`.

### Knowing where you are

The CLI always shows which profile is active:

- **Prompt**: `coder ❯` instead of `❯`
- **Banner**: Shows `Profile: coder` on startup
- **`intellect agent`**: Shows current agent name, path, model, gateway status

## Profiles vs workspaces vs sandboxing

Profiles are often confused with workspaces or sandboxes, but they are different things:

- A **profile** gives Intellect its own state directory: `config.yaml`, `.env`, `SOUL.md`, sessions, memory, logs, cron jobs, and gateway state.
- A **workspace** or **working directory** is where terminal commands start. That is controlled separately by `terminal.cwd`.
- A **sandbox** is what limits filesystem access. Agents do **not** sandbox the agent.

On the default `local` terminal backend, the agent still has the same filesystem access as your user account. An agent does not stop it from accessing folders outside the agent directory.

If you want an agent to start in a specific project folder, set an explicit absolute `terminal.cwd` in that agent's `config.yaml`:

```yaml
terminal:
  backend: local
  cwd: /absolute/path/to/project
```

Using `cwd: "."` on the local backend means "the directory Intellect was launched from", not "the agent directory".

Also note:

- `SOUL.md` can guide the model, but it does not enforce a workspace boundary.
- Changes to `SOUL.md` take effect cleanly on a new session. Existing sessions may still be using the old prompt state.
- Asking the model "what directory are you in?" is not a reliable isolation test. If you need a predictable starting directory for tools, set `terminal.cwd` explicitly.

## Running gateways

Each profile runs its own gateway as a separate process with its own bot token:

```bash
coder gateway start           # starts coder's gateway
assistant gateway start       # starts assistant's gateway (separate process)
```

### Different bot tokens

Each profile has its own `.env` file. Configure a different Telegram/Discord/Slack bot token in each:

```bash
# Edit coder's tokens
nano ~/.intellect/agents/coder/.env

# Edit assistant's tokens
nano ~/.intellect/agents/assistant/.env
```

### Safety: token locks

If two profiles accidentally use the same bot token, the second gateway will be blocked with a clear error naming the conflicting profile. Supported for Telegram, Discord, Slack, WhatsApp, and Signal.

### Persistent services

```bash
coder gateway install         # creates intellect-gateway-coder systemd/launchd service
assistant gateway install     # creates intellect-gateway-assistant service
```

Each profile gets its own service name. They run independently.

:::note Inside the official Docker image
Per-profile gateways are supervised by [s6-overlay](https://github.com/just-containers/s6-overlay) (PID 1 in the container), so `intellect agent create <name>` automatically registers an s6 service slot at `/run/service/gateway-<name>/`. `intellect -a <name> gateway start/stop/restart` dispatches to `s6-svc` instead of spawning a bare process — crashes are auto-restarted and `docker restart` preserves the previously-running set of gateways. See [Per-profile gateway supervision](/user-guide/docker#per-profile-gateway-supervision) for details.
:::

## Configuring profiles

Each profile has its own:

- **`config.yaml`** — model, provider, toolsets, all settings
- **`.env`** — API keys, bot tokens
- **`SOUL.md`** — personality and instructions

```bash
coder config set model.default anthropic/claude-sonnet-4
echo "You are a focused coding assistant." > ~/.intellect/agents/coder/SOUL.md
```

If you want this profile to work in a specific project by default, also set its own `terminal.cwd`:

```bash
coder config set terminal.cwd /absolute/path/to/project
```

## Updating

`intellect update` pulls code once (shared) and syncs new bundled skills to **all** profiles automatically:

```bash
intellect update
# → Code updated (12 commits)
# → Skills synced: default (up to date), coder (+2 new), assistant (+2 new)
```

User-modified skills are never overwritten.

## Managing profiles

```bash
intellect agent list           # show all profiles with status
intellect agent show coder     # detailed info for one profile
intellect agent rename coder dev-bot   # rename (updates alias + service)
intellect agent export coder   # export to coder.tar.gz
intellect agent import coder.tar.gz   # import from archive
```

## Deleting an agent

```bash
intellect agent delete coder
```

This stops the gateway, removes the systemd/launchd service, removes the command alias, and deletes all agent data. You'll be asked to type the agent name to confirm.

Use `--yes` to skip confirmation: `intellect agent delete coder --yes`

:::note
You cannot delete the default profile (`~/.intellect`). To remove everything, use `intellect uninstall`.
:::

## Tab completion

```bash
# Bash
eval "$(intellect completion bash)"

# Zsh
eval "$(intellect completion zsh)"
```

Add the line to your `~/.bashrc` or `~/.zshrc` for persistent completion. Completes profile names after `-p`, profile subcommands, and top-level commands.

## How it works

Agents use the `INTELLECT_HOME` environment variable. When you run `coder chat`, the wrapper script sets `INTELLECT_HOME=~/.intellect/agents/coder` before launching intellect. Since 119+ files in the codebase resolve paths via `get_intellect_home()`, Intellect state automatically scopes to the agent's directory — config, sessions, memory, skills, state database, gateway PID, logs, and cron jobs.

This is separate from terminal working directory. Tool execution starts from `terminal.cwd` (or the launch directory when `cwd: "."` on the local backend), not automatically from `INTELLECT_HOME`.

The default profile is simply `~/.intellect` itself. No migration needed — existing installs work identically.

## Sharing profiles as distributions

A profile you built on one machine can be packaged as a **git repository** and installed with one command on another machine — your own workstation, a teammate's laptop, or a community user's environment. The shared package includes the SOUL, config, skills, cron jobs, and MCP connections. Credentials, memories, and sessions stay per-machine.

```bash
# Install a whole agent from a git repo
intellect agent install github.com/you/research-bot --alias

# Update later when the author ships a new version (keeps your memories + .env)
intellect agent update research-bot
```

See **[Profile Distributions: Share a Whole Agent](./agent-distributions.md)** for the full guide — authoring, publishing, update semantics, security model, and use cases.
