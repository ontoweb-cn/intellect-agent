---
sidebar_position: 7
---

# Agent Commands Reference

:::info Renamed from "Profiles"
Canonical command: `intellect agent`. `intellect agent` remains a compatibility alias.
On-disk paths: see [Agents](../user-guide/agents.md).
:::

This page covers all commands related to [Intellect agents](../user-guide/agents.md). For general CLI commands, see [CLI Commands Reference](./cli-commands.md).

## `intellect agent`

```bash
intellect agent <subcommand>
```

Top-level command for managing agents. Running `intellect agent` without a subcommand shows status (alias: `intellect agent`).

| Subcommand | Description |
|------------|-------------|
| `list` | List all agents. |
| `use` | Set the active (default) agent. |
| `create` | Create a new agent. |
| `delete` | Delete an agent. |
| `show` | Show details about an agent. |
| `alias` | Regenerate the shell alias for an agent. |
| `rename` | Rename an agent. |
| `export` | Export an agent to a tar.gz archive. |
| `import` | Import an agent from a tar.gz archive. |
| `install` | Install an agent distribution from a git URL or local directory. See [Agent Distributions](../user-guide/agent-distributions.md). |
| `update` | Re-pull a distribution-managed agent and re-apply its bundle. |
| `info` | Show distribution metadata for an agent (origin URL, commit, last update). |

## `intellect agent list`

```bash
intellect agent list
```

Lists all agents. The currently active agent is marked with `*`.

**Example:**

```bash
$ intellect agent list
  default
* work
  dev
  personal
```

No options.

## `intellect agent use`

```bash
intellect agent use <name>
```

Sets `<name>` as the active profile. All subsequent `intellect` commands (without `-p`) will use this profile.

| Argument | Description |
|----------|-------------|
| `<name>` | Agent name to activate. Use `default` to return to the base profile. |

**Example:**

```bash
intellect agent use work
intellect agent use default
```

## `intellect agent create`

```bash
intellect agent create <name> [options]
```

Creates a new profile.

| Argument / Option | Description |
|-------------------|-------------|
| `<name>` | Name for the new profile. Must be a valid directory name (alphanumeric, hyphens, underscores). |
| `--clone` | Copy `config.yaml`, `.env`, and `SOUL.md` from the current profile. |
| `--clone-all` | Copy everything (config, memories, skills, sessions, state) from the current profile. |
| `--clone-from <profile>` | Clone from a specific agent instead of the current one. Used with `--clone` or `--clone-all`. |
| `--no-alias` | Skip wrapper script creation. |
| `--description "<text>"` | One- or two-sentence description of what this agent is good at. Used by the kanban orchestrator to route tasks based on role instead of agent name alone. Skip and add later via `intellect agent describe`. Persisted in `<profile_dir>/profile.yaml`. |
| `--no-skills` | Create an **empty** agent with zero bundled skills enabled. Writes a `.no-skills` marker into the agent so future `intellect update` runs won't re-seed the bundled set, and refuses to combine with `--clone` / `--clone-all` (which would copy skills in anyway). Useful for narrow orchestrator profiles or sandbox profiles that should not inherit the full skill catalog. |

Creating an agent does **not** make that agent directory the default project/workspace directory for terminal commands. If you want an agent to start in a specific project, set `terminal.cwd` in that profile's `config.yaml`.

**Examples:**

```bash
# Blank agent — needs full setup
intellect agent create mybot

# Clone config only from current profile
intellect agent create work --clone

# Clone everything from current profile
intellect agent create backup --clone-all

# Clone config from a specific profile
intellect agent create work2 --clone --clone-from work
```

## `intellect agent describe`

```bash
intellect agent describe [<name>] [options]
```

Read or set an agent's description. The description is consumed by the kanban orchestrator to route tasks based on what each agent is good at, rather than guessing from the agent name alone. Persisted in `<profile_dir>/profile.yaml` so it survives reboots and is shared with the gateway.

With no flags, prints the current description (or `(no description set for '<name>')` if empty).

| Argument / Option | Description |
|-------------------|-------------|
| `<name>` | Agent to describe. Required unless `--all --auto` is used. |
| `--text "<text>"` | Set the description to this exact text (user-authored). Overwrites any existing description. |
| `--auto` | Auto-generate a 1-2 sentence description via the auxiliary LLM, based on the agent's installed skills, configured model, and name. Configure the model under `auxiliary.profile_describer` in `config.yaml`. Auto-generated descriptions are marked `description_auto: true` so they can be reviewed. |
| `--overwrite` | With `--auto`, replace user-authored descriptions too (default: skip profiles whose description was set explicitly). |
| `--all` | With `--auto`, sweep every agent missing a description. |

**Examples:**

```bash
# Read the current description
intellect agent describe researcher

# Set it explicitly
intellect agent describe researcher --text "Reads source code and writes findings."

# Let the LLM generate one
intellect agent describe researcher --auto

# Fill in descriptions for every agent that doesn't have one
intellect agent describe --all --auto
```

## `intellect agent delete`

```bash
intellect agent delete <name> [options]
```

Deletes an agent and removes its shell alias.

| Argument / Option | Description |
|-------------------|-------------|
| `<name>` | Agent to delete. |
| `--yes`, `-y` | Skip confirmation prompt. |

**Example:**

```bash
intellect agent delete mybot
intellect agent delete mybot --yes
```

:::warning
This permanently deletes the agent's entire directory including all config, memories, sessions, and skills. Cannot delete the currently active profile.
:::

## `intellect agent show`

```bash
intellect agent show <name>
```

Displays details about an agent including its home directory, configured model, gateway status, skills count, and configuration file status.

This shows the agent's Intellect home directory, not the terminal working directory. Terminal commands start from `terminal.cwd` (or the launch directory on the local backend when `cwd: "."`).

| Argument | Description |
|----------|-------------|
| `<name>` | Agent to inspect. |

**Example:**

```bash
$ intellect agent show work
Profile: work
Path:    ~/.intellect/agents/work
Model:   anthropic/claude-sonnet-4 (anthropic)
Gateway: stopped
Skills:  12
.env:    exists
SOUL.md: exists
Alias:   ~/.local/bin/work
```

## `intellect agent alias`

```bash
intellect agent alias <name> [options]
```

Regenerates the shell alias script at `~/.local/bin/<name>`. Useful if the alias was accidentally deleted or if you need to update it after moving your Intellect installation.

| Argument / Option | Description |
|-------------------|-------------|
| `<name>` | Agent to create/update the alias for. |
| `--remove` | Remove the wrapper script instead of creating it. |
| `--name <alias>` | Custom alias name (default: agent name). |

**Example:**

```bash
intellect agent alias work
# Creates/updates ~/.local/bin/work

intellect agent alias work --name mywork
# Creates ~/.local/bin/mywork

intellect agent alias work --remove
# Removes the wrapper script
```

## `intellect agent rename`

```bash
intellect agent rename <old-name> <new-name>
```

Renames an agent. Updates the directory and shell alias.

| Argument | Description |
|----------|-------------|
| `<old-name>` | Current agent name. |
| `<new-name>` | New agent name. |

**Example:**

```bash
intellect agent rename mybot assistant
# ~/.intellect/agents/mybot → ~/.intellect/agents/assistant
# ~/.local/bin/mybot → ~/.local/bin/assistant
```

## `intellect agent export`

```bash
intellect agent export <name> [options]
```

Exports an agent as a compressed tar.gz archive.

| Argument / Option | Description |
|-------------------|-------------|
| `<name>` | Agent to export. |
| `-o`, `--output <path>` | Output file path (default: `<name>.tar.gz`). |

**Example:**

```bash
intellect agent export work
# Creates work.tar.gz in the current directory

intellect agent export work -o ./work-2026-03-29.tar.gz
```

## `intellect agent import`

```bash
intellect agent import <archive> [options]
```

Imports an agent from a tar.gz archive.

| Argument / Option | Description |
|-------------------|-------------|
| `<archive>` | Path to the tar.gz archive to import. |
| `--name <name>` | Name for the imported agent (default: inferred from archive). |

**Example:**

```bash
intellect agent import ./work-2026-03-29.tar.gz
# Infers agent name from the archive

intellect agent import ./work-2026-03-29.tar.gz --name work-restored
```

## Distribution commands

:::tip
**New to distributions?** Start with the [Agent Distributions user guide](../user-guide/agent-distributions.md) — it covers the why, when, and how with full examples. The sections below are a dry CLI reference for when you know what you want.
:::

Distributions turn an agent into a shareable, versioned artifact published
as a **git repository**. A recipient installs the distribution with a single
command and can update it in place later without touching their local
memories, sessions, or credentials.

`auth.json` and `.env` are never part of a distribution — they stay on the
installing user's machine.

The recipient's user data (memories, sessions, auth, their own edits to
`.env`) is always preserved across the initial install and subsequent
updates.

:::info
`intellect agent export` / `import` are still the right commands for
**local backup and restore** of an agent on your own machine. Distribution
(`install` / `update` / `info`) is a separate concept: ship an agent via
git so someone else can install it.
:::

### `intellect agent install`

```bash
intellect agent install <source> [--name <name>] [--alias] [--force] [--yes]
```

Installs an agent distribution from a git URL or a local directory.

| Option | Description |
|--------|-------------|
| `<source>` | Git URL (`github.com/user/repo`, `https://...`, `git@...`, `ssh://`, `git://`) or a local directory containing `distribution.yaml` at its root. |
| `--name NAME` | Override the agent name from the manifest. |
| `--alias` | Also create a shell wrapper (e.g. `telemetry` → `intellect -a telemetry`). |
| `--force` | Overwrite an existing agent of the same name. User data is still preserved. |
| `-y`, `--yes` | Skip the manifest-preview confirmation prompt. |

The installer shows the manifest, lists required env vars, and warns about
cron jobs before asking for confirmation. Required env vars go into a
`.env.EXAMPLE` file you copy to `.env` and fill in.

**Examples:**

```bash
# Install from a GitHub repo (shorthand)
intellect agent install github.com/kyle/telemetry-distribution --alias

# Install from a full HTTPS git URL
intellect agent install https://github.com/kyle/telemetry-distribution.git

# Install from SSH
intellect agent install git@github.com:kyle/telemetry-distribution.git

# Install from a local directory during development
intellect agent install ./telemetry/
```

### `intellect agent update`

```bash
intellect agent update <name> [--force-config] [--yes]
```

Re-clones the distribution from its recorded source and applies updates.
Distribution-owned files (SOUL.md, skills/, cron/, mcp.json) are
overwritten; user data (memories, sessions, auth, .env) is never touched.

`config.yaml` is preserved by default to keep your local overrides.
Pass `--force-config` to reset it to the distribution's shipped config.

### `intellect agent info`

```bash
intellect agent info <name>
```

Prints the agent's distribution manifest — name, version, required
Intellect version, author, env var requirements, the source URL/path, and
the `Installed:` timestamp recorded when the distribution was last
`install`-ed or `update`-d. Useful for checking what a shared profile
needs before installing it, and for spotting "this agent was installed
6 months ago and hasn't been updated."

`intellect agent list` also shows the distribution name and version in a
`Distribution` column, and `intellect agent show <name>` / `delete <name>`
surface the source URL so you can tell at a glance which profiles came
from a git repo vs. were created locally.

### Private distributions

A private git repository works as a distribution source with no extra
configuration — the install shells out to your normal `git` binary, so
whatever authentication your shell is already set up for (SSH key,
`git credential` helper, GitHub CLI's stored HTTPS credentials) applies
transparently.

```bash
# Uses your SSH key, the same as any other `git clone`
intellect agent install git@github.com:your-org/internal-assistant.git

# Uses your git credential helper
intellect agent install https://github.com/your-org/internal-assistant.git
```

If a clone prompts for credentials interactively in your terminal during
install, that prompt flows through. Set up your auth the way you'd
normally use `git clone` against the same repo first, then install.

### Distribution manifest (`distribution.yaml`)

Every distribution has a `distribution.yaml` at the root of its repository:

```yaml
name: telemetry
version: 0.1.0
description: "Compliance monitoring harness"
intellect_requires: ">=0.12.0"
author: "Your Name"
license: "MIT"
env_requires:
  - name: OPENAI_API_KEY
    description: "OpenAI API key"
    required: true
  - name: GRAPHITI_MCP_URL
    description: "Memory graph URL"
    required: false
    default: "http://127.0.0.1:8000/sse"
distribution_owned:   # optional; defaults to SOUL.md, config.yaml,
                      #   mcp.json, skills/, cron/, distribution.yaml
  - SOUL.md
  - skills/compliance/
  - cron/
```

`intellect_requires` supports `>=`, `<=`, `==`, `!=`, `>`, `<`, or a bare
version (treated as `>=`). Install fails with a clear error if the current
Intellect version doesn't satisfy the spec.

`distribution_owned` is optional. If set, only those paths are replaced on
update; anything else in the agent stays user-owned. If omitted, the
defaults above apply.

### Publishing a distribution

Authoring a distribution is just a git push:

1. In your agent directory, create `distribution.yaml` with at least `name`
   and `version`.
2. Initialize a git repo (or use an existing one) and push to GitHub /
   GitLab / any host Intellect can clone from.
3. Tell recipients to run `intellect agent install <your-repo-url>`.

Use git tags for versioned releases — recipients who clone `HEAD` get your
latest state, and you can always bump `version:` in the manifest.

## `intellect -a` / `intellect --agent`

Legacy: `-p` / `--profile`.

```bash
intellect -a <name> <command> [options]
intellect --agent <name> <command> [options]
```

Global flag to run any Intellect command under a specific agent without changing the sticky default. This overrides the active agent for the duration of the command.

| Option | Description |
|--------|-------------|
| `-p <name>`, `--agent <name>` | Agent to use for this command. |

**Examples:**

```bash
intellect -a work chat -q "Check the server status"
intellect --agent dev gateway start
intellect -a personal skills list
intellect -a work config edit
```

## `intellect completion`

```bash
intellect completion <shell>
```

Generates shell completion scripts. Includes completions for agent names and agent subcommands.

| Argument | Description |
|----------|-------------|
| `<shell>` | Shell to generate completions for: `bash`, `zsh`, or `fish`. |

**Examples:**

```bash
# Install completions
intellect completion bash >> ~/.bashrc
intellect completion zsh >> ~/.zshrc
intellect completion fish > ~/.config/fish/completions/intellect.fish

# Reload shell
source ~/.bashrc
```

After installation, tab completion works for:
- `intellect agent <TAB>` — subcommands (list, use, create, etc.)
- `intellect agent use <TAB>` — agent names
- `intellect -a <TAB>` — agent names

## See also

- [Agents User Guide](../user-guide/agents.md)
- [CLI Commands Reference](./cli-commands.md)
- [FAQ — Agents section](./faq.md#profiles)
