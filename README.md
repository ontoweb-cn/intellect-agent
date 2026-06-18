


# Intellect Agent (Community Version)

<p align="center">
  <a href="https://intellect.ontoweb.cn/docs/"><img src="https://img.shields.io/badge/Docs-intellect.ontoweb.cn-FFD700?style=for-the-badge" alt="Documentation"></a>
  <a href="https://discord.gg/ONTOWEB"><img src="https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://gitee.com/ontoweb/intellect-agent/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://ontoweb.cn"><img src="https://img.shields.io/badge/Built%20by-ONTOWEB-blueviolet?style=for-the-badge" alt="Built by ONTOWEB"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Lang-中文-red?style=for-the-badge" alt="中文"></a>
</p>

**The self-improving AI agent built by [ONTOWEB](https://ontoweb.cn).** It is a multi-intelligent scheduling engine based on PYTHON and RUST, designed for individuals and small teams, and integrates excellent third-party Web interface tools.

## Quick Install

### Linux, macOS, WSL2

```bash
curl -fsSL https://raw.githubusercontent.com/ONTOWEB/intellect-agent/main/scripts/install.sh | bash
```

> The installer creates a virtual environment, installs Python dependencies, and builds the Rust extension (`intellect_community_core`).

### Windows (native, PowerShell)

Run this in PowerShell:

```powershell
iex (irm https://raw.githubusercontent.com/ONTOWEB/intellect-agent/main/scripts/install.ps1)
```

The installer handles everything: uv, Python 3.12, Node.js, ripgrep, ffmpeg, **and a portable Git Bash** (MinGit, unpacked to `%LOCALAPPDATA%\intellect\git` — no admin required, completely isolated from any system Git install). Intellect uses this bundled Git Bash to run shell commands.

> **Rust extension on Windows:** The installer downloads a pre-built wheel from Gitee Releases, falling back to compiling locally via `maturin`.

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

## License

MIT — see [LICENSE](LICENSE).

Built by [ONTOWEB](https://ontoweb.cn).
