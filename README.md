


# Intellect Agent ⚕

<p align="center">
  <a href="https://intellect.ontoweb.cn/docs/"><img src="https://img.shields.io/badge/Docs-intellect--agent.ontoweb.cn-FFD700?style=for-the-badge" alt="Documentation"></a>
  <a href="https://discord.gg/ONTOWEB"><img src="https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://gitee.com/ontoweb/intellect-agent/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://ontoweb.cn"><img src="https://img.shields.io/badge/Built%20by-ONTOWEB-blueviolet?style=for-the-badge" alt="Built by ONTOWEB"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Lang-中文-red?style=for-the-badge" alt="中文"></a>
</p>
**The self-improving AI agent built by [ONTOWEB](https://ontoweb.cn).** It's the only agent with a built-in learning loop — it creates skills from experience, improves them during use, nudges itself to persist knowledge, searches its own past conversations, and builds a deepening model of who you are across sessions. Run it on a $5 VPS, a GPU cluster, or serverless infrastructure that costs nearly nothing when idle. It's not tied to your laptop — talk to it from Telegram while it works on a cloud VM.

Use any model you want — [ONTOWEB Portal](https://portal.ontoweb.cn), [OpenRouter](https://openrouter.ai) (200+ models), [NovitaAI](https://novita.ai) (AI-native cloud for Model API, Agent Sandbox, and GPU Cloud), [NVIDIA NIM](https://build.nvidia.com) (Nemotron), [Xiaomi MiMo](https://platform.xiaomimimo.com), [z.ai/GLM](https://z.ai), [Kimi/Moonshot](https://platform.moonshot.ai), [MiniMax](https://www.minimax.io), [Hugging Face](https://huggingface.co), OpenAI, or your own endpoint. Switch with `intellect model` — no code changes, no lock-in.

<table>
<tr><td><b>A real terminal interface</b></td><td>Full TUI with multiline editing, slash-command autocomplete, conversation history, interrupt-and-redirect, and streaming tool output.</td></tr>
<tr><td><b>Lives where you do</b></td><td>Telegram, Discord, Slack, WhatsApp, Signal, and CLI — all from a single gateway process. Voice memo transcription, cross-platform conversation continuity.</td></tr>
<tr><td><b>A closed learning loop</b></td><td>Agent-curated memory with periodic nudges. Autonomous skill creation after complex tasks. Skills self-improve during use. FTS5 session search with LLM summarization for cross-session recall. <a href="https://github.com/plastic-labs/honcho">Honcho</a> dialectic user modeling. Compatible with the <a href="https://agentskills.io">agentskills.io</a> open standard.</td></tr>
<tr><td><b>Scheduled automations</b></td><td>Built-in cron scheduler with delivery to any platform. Daily reports, nightly backups, weekly audits — all in natural language, running unattended.</td></tr>
<tr><td><b>Delegates and parallelizes</b></td><td>Spawn isolated subagents for parallel workstreams. Write Python scripts that call tools via RPC, collapsing multi-step pipelines into zero-context-cost turns.</td></tr>
<tr><td><b>Runs anywhere, not just your laptop</b></td><td>Six terminal backends — local, Docker, SSH, Singularity, Modal, and Daytona. Daytona and Modal offer serverless persistence — your agent's environment hibernates when idle and wakes on demand, costing nearly nothing between sessions. Run it on a $5 VPS or a GPU cluster.</td></tr>
<tr><td><b>Research-ready</b></td><td>Batch trajectory generation, trajectory compression for training the next generation of tool-calling models.</td></tr>
</table>

---

## Quick Install

### Linux, macOS, WSL2, Termux

```bash
curl -fsSL https://raw.githubusercontent.com/ONTOWEB/intellect-agent/main/scripts/install.sh | bash
```

> The installer creates a virtual environment and installs all dependencies. The Rust extension (`intellect_community_core`) is recommended for full performance but **optional** — the agent will run with pure-Python fallbacks if it cannot be installed.

### Windows (native, PowerShell)

Run this in PowerShell:

```powershell
iex (irm https://raw.githubusercontent.com/ONTOWEB/intellect-agent/main/scripts/install.ps1)
```

The installer handles everything: uv, Python 3.12, Node.js, ripgrep, ffmpeg, **and a portable Git Bash** (MinGit, unpacked to `%LOCALAPPDATA%\intellect\git` — no admin required, completely isolated from any system Git install). Intellect uses this bundled Git Bash to run shell commands.

> **Rust extension on Windows:** The installer tries to download a pre-built wheel from Gitee Releases, falling back to compiling locally via `maturin`. If neither works, the agent continues with pure-Python fallbacks (reduced performance for storage, sandbox, crypto, and stream acceleration).

### Docker

```bash
docker pull ghcr.io/ontoweb/intellect-agent:latest
docker run -v intellect-data:/opt/data ghcr.io/ontoweb/intellect-agent:latest
```

See [Docker deployment guide](https://intellect.ontoweb.cn/docs/deployment/docker) for compose files and configuration.

> **Updating Docker:** Use `docker pull` to update the image — `intellect update` inside the container prints the correct pull command.

### Homebrew (macOS)

```bash
brew install intellect-agent
```

> Homebrew-managed installs receive updates via `brew upgrade`. The `intellect update` command is disabled for managed installs.

> **Android / Termux:** The tested manual path is documented in the [Termux guide](https://intellect.ontoweb.cn/docs/getting-started/termux). On Termux, Intellect installs a curated `.[termux]` extra.
>
> **WSL2:** The Linux one-liner above works inside WSL2. Native Windows install lives under `%LOCALAPPDATA%\intellect`; WSL2 installs under `~/.intellect` as on Linux.

After installation:

```bash
source ~/.bashrc    # reload shell (or: source ~/.zshrc)
intellect              # start chatting!
```

---

## Getting Started

```bash
intellect              # Interactive CLI — start a conversation
intellect model        # Choose your LLM provider and model
intellect tools        # Configure which tools are enabled
intellect config set   # Set individual config values
intellect gateway      # Start the messaging gateway (Telegram, Discord, etc.)
intellect webui start  # Start the WebUI dashboard in background
intellect setup        # Run the full setup wizard (configures everything at once)
intellect claw migrate # Migrate from OpenClaw (if coming from OpenClaw)
intellect update       # Update to the latest version
intellect doctor       # Diagnose any issues
```

📖 **[Full documentation →](https://intellect.ontoweb.cn/docs/)**

---

## Rust Acceleration (optional)

Intellect Agent uses a Rust native extension (`intellect_community_core`) for high-performance storage, sandbox detection, stream parsing, and cryptographic operations. The Rust extension is **optional** — the agent includes pure-Python fallbacks for all functionality.

| Feature | With Rust | Without Rust (fallback) |
|---------|-----------|------------------------|
| Storage backend | Accelerated SQLite | Standard SQLite |
| Sandbox detection | Native regex engine | Python regex |
| Stream accumulation | Parallel Rust parser | Serial Python parser |
| Token normalization | Native pass-through | Python identity |
| Cryptography (FTS, Fernet) | Rust crypto | `NotImplementedError` |

To install the Rust extension after initial setup:

```bash
cd rust-core && maturin develop --release
```

> The agent prints a warning at startup when the Rust extension is missing but continues normally.

---

## Updating

| Install method | Command | Notes |
|---------------|---------|-------|
| Git clone | `intellect update` | Pulls latest source + rebuilds Rust extension |
| pip/uv | `intellect update` | Upgrades Python package + Rust wheel |
| Docker | `docker pull` | Pull latest image |
| Homebrew | `brew upgrade intellect-agent` | Managed by Homebrew |

`intellect update` automatically keeps the Rust extension in sync:
- **Git installs**: Rebuilds via `maturin develop --release` after each pull
- **pip installs**: Upgrades `intellect_community_core` wheel alongside the main package
- Both paths are best-effort — the agent works without the extension

---

## Skip the API-key collection — ONTOWEB Portal

Intellect works with whatever provider you want — that's not changing. But if you'd rather not collect five separate API keys for the model, web search, image generation, TTS, and a cloud browser, **[ONTOWEB Portal](https://portal.ontoweb.cn)** covers all of them under one subscription:

- **300+ models** — pick any of them with `/model <name>`
- **Tool Gateway** — web search (Firecrawl), image generation (FAL), text-to-speech (OpenAI), cloud browser (Browser Use), all routed through your sub. No extra accounts.

One command from a fresh install:

```bash
intellect setup --portal
```

That logs you in via OAuth, sets ONTOWEB as your provider, and turns on the Tool Gateway. Check what's wired up any time with `intellect portal status`. Full details on the [Tool Gateway docs page](https://intellect.ontoweb.cn/docs/user-guide/features/tool-gateway).

You can still bring your own keys per-tool whenever you want — the gateway is per-backend, not all-or-nothing.

---

## CLI vs Messaging Quick Reference

Intellect has two entry points: start the terminal UI with `intellect`, or run the gateway and talk to it from Telegram, Discord, Slack, WhatsApp, Signal, or Email. Once you're in a conversation, many slash commands are shared across both interfaces.

| Action | CLI | Messaging platforms |
|---------|-----|---------------------|
| Start chatting | `intellect` | Run `intellect gateway setup` + `intellect gateway start`, then send the bot a message |
| Start fresh conversation | `/new` or `/reset` | `/new` or `/reset` |
| Change model | `/model [provider:model]` | `/model [provider:model]` |
| Set a personality | `/personality [name]` | `/personality [name]` |
| Retry or undo the last turn | `/retry`, `/undo` | `/retry`, `/undo` |
| Compress context / check usage | `/compress`, `/usage`, `/insights [--days N]` | `/compress`, `/usage`, `/insights [days]` |
| Browse skills | `/skills` or `/<skill-name>` | `/<skill-name>` |
| Interrupt current work | `Ctrl+C` or send a new message | `/stop` or send a new message |
| Platform-specific status | `/platforms` | `/status`, `/sethome` |

For the full command lists, see the [CLI guide](https://intellect.ontoweb.cn/docs/user-guide/cli) and the [Messaging Gateway guide](https://intellect.ontoweb.cn/docs/user-guide/messaging).

---

## Documentation

All documentation lives at **[intellect.ontoweb.cn/docs](https://intellect.ontoweb.cn/docs/)**:

| Section | What's Covered |
|---------|---------------|
| [Quickstart](https://intellect.ontoweb.cn/docs/getting-started/quickstart) | Install → setup → first conversation in 2 minutes |
| [CLI Usage](https://intellect.ontoweb.cn/docs/user-guide/cli) | Commands, keybindings, personalities, sessions |
| [Configuration](https://intellect.ontoweb.cn/docs/user-guide/configuration) | Config file, providers, models, all options |
| [Messaging Gateway](https://intellect.ontoweb.cn/docs/user-guide/messaging) | Telegram, Discord, Slack, WhatsApp, Signal, Home Assistant |
| [Security](https://intellect.ontoweb.cn/docs/user-guide/security) | Command approval, DM pairing, container isolation |
| [Tools & Toolsets](https://intellect.ontoweb.cn/docs/user-guide/features/tools) | 40+ tools, toolset system, terminal backends |
| [Skills System](https://intellect.ontoweb.cn/docs/user-guide/features/skills) | Procedural memory, Skills Hub, creating skills |
| [Memory](https://intellect.ontoweb.cn/docs/user-guide/features/memory) | Persistent memory, user profiles, best practices |
| [WebUI Dashboard](https://intellect.ontoweb.cn/docs/user-guide/features/webui) | Browser-based session management, real-time streaming, settings |
| [MCP Integration](https://intellect.ontoweb.cn/docs/user-guide/features/mcp) | Connect any MCP server for extended capabilities |
| [Cron Scheduling](https://intellect.ontoweb.cn/docs/user-guide/features/cron) | Scheduled tasks with platform delivery |
| [Context Files](https://intellect.ontoweb.cn/docs/user-guide/features/context-files) | Project context that shapes every conversation |
| [Architecture](https://intellect.ontoweb.cn/docs/developer-guide/architecture) | Project structure, agent loop, key classes |
| [Contributing](https://intellect.ontoweb.cn/docs/developer-guide/contributing) | Development setup, PR process, code style |
| [CLI Reference](https://intellect.ontoweb.cn/docs/reference/cli-commands) | All commands and flags |
| [Environment Variables](https://intellect.ontoweb.cn/docs/reference/environment-variables) | Complete env var reference |

---

## Migrating from OpenClaw

If you're coming from OpenClaw, Intellect can automatically import your settings, memories, skills, and API keys.

**During first-time setup:** The setup wizard (`intellect setup`) automatically detects `~/.openclaw` and offers to migrate before configuration begins.

**Anytime after install:**

```bash
intellect claw migrate              # Interactive migration (full preset)
intellect claw migrate --dry-run    # Preview what would be migrated
intellect claw migrate --preset user-data   # Migrate without secrets
intellect claw migrate --overwrite  # Overwrite existing conflicts
```

What gets imported:
- **SOUL.md** — persona file
- **Memories** — MEMORY.md and USER.md entries
- **Skills** — user-created skills → `~/.intellect/skills/openclaw-imports/`
- **Command allowlist** — approval patterns
- **Messaging settings** — platform configs, allowed users, working directory
- **API keys** — allowlisted secrets (Telegram, OpenRouter, OpenAI, Anthropic, ElevenLabs)
- **TTS assets** — workspace audio files
- **Workspace instructions** — AGENTS.md (with `--workspace-target`)

See `intellect claw migrate --help` for all options, or use the `openclaw-migration` skill for an interactive agent-guided migration with dry-run previews.

---

## Contributing

We welcome contributions! See the [Contributing Guide](https://intellect.ontoweb.cn/docs/developer-guide/contributing) for development setup, code style, and PR process.

Quick start for contributors — clone and go with `setup-intellect.sh`:

```bash
git clone https://gitee.com/ontoweb/intellect-agent.git
cd intellect-agent
./setup-intellect.sh     # installs uv, creates venv, installs .[all], symlinks ~/.local/bin/intellect
./intellect              # auto-detects the venv, no need to `source` first
```

Manual path (equivalent to the above):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[all,dev]"
scripts/run_tests.sh
```

---

## Community

- 💬 [Discord](https://discord.gg/ontoweb)
- 📚 [Skills Hub](https://agentskills.io)
- 🐛 [Issues](https://gitee.com/ontoweb/intellect-agent/issues)
- 🔌 [computer-use-linux](https://github.com/avifenesh/computer-use-linux) — Linux desktop-control MCP server for Intellect and other MCP hosts, with AT-SPI accessibility trees, Wayland/X11 input, screenshots, and compositor window targeting.

---

## License

MIT — see [LICENSE](LICENSE).

Built by [ONTOWEB](https://ontoweb.cn).
