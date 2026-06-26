


# Intellect Agent (社区版)

<p align="center">
  <a href="https://intellect.ontoweb.cn/docs/"><img src="https://img.shields.io/badge/Docs-intellect.ontoweb.cn-FFD700?style=for-the-badge" alt="Documentation"></a>
  <a href="https://discord.gg/ontoweb"><img src="https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://gitee.com/ontoweb/intellect-agent/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://ontoweb.cn"><img src="https://img.shields.io/badge/Built%20by-OntoWeb-blueviolet?style=for-the-badge" alt="Built by ONTOWEB"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/Lang-English-lightgrey?style=for-the-badge" alt="English"></a>
</p>

**由 [ONTOWEB](https://ontoweb.cn) 构建的自进化 AI 代理。** 它是一个基于PYTHON与RUST的面向个人和小团队使用的多智能调度引擎，同时集成了第三方优秀的Web界面工具。


---

## 快速安装

### Linux, macOS, WSL2

```bash
curl -fsSL https://raw.githubusercontent.com/ontoweb/intellect-agent/main/scripts/install.sh | bash
```

> 安装程序创建虚拟环境、安装 Python 依赖，并编译 Rust 扩展（`intellect_community_core`）。

### Windows（原生 PowerShell）

```powershell
iex (irm https://raw.githubusercontent.com/ontoweb/intellect-agent/main/scripts/install.ps1)
```

安装程序自动处理：uv、Python 3.12、Node.js、ripgrep、ffmpeg 和便携式 Git Bash。

> **Windows Rust 扩展：** 安装程序优先从 Gitee Releases 下载预编译 wheel，失败则通过 `maturin` 本地编译。

### Docker

```bash
# Docker Hub
docker pull ontoweb/intellect-agent:latest
docker run -v intellect-data:/opt/data ontoweb/intellect-agent:latest

# 阿里云容器镜像（国内加速）
docker pull crpi-okdl7kgk1p2exqcm.cn-hangzhou.personal.cr.aliyuncs.com/ontoweb/intellect-agent:latest
```

### Homebrew（macOS）

```bash
brew install intellect-agent
```

安装后：

```bash
source ~/.bashrc    # 重新加载 shell（或: source ~/.zshrc）
intellect              # 开始对话！
```

---

## 快速入门

```bash
intellect              # 交互式 CLI — 开始对话
intellect model        # 选择 LLM 提供商和模型
intellect tools        # 配置启用的工具
intellect config set   # 设置单个配置项
intellect gateway      # 启动消息网关（Telegram、Discord 等）
intellect setup        # 运行完整设置向导（一次性配置所有内容）
intellect claw migrate # 从 OpenClaw 迁移（如果来自 OpenClaw）
intellect update       # 更新到最新版本
intellect doctor       # 诊断问题
```

📖 **[完整文档 →](https://intellect.ontoweb.cn/docs/)**

---

## 更新

| 安装方式 | 命令 | 说明 |
|---------|------|------|
| Git clone | `intellect update` | 拉取最新源码 + 重建 Rust 扩展 |
| pip/uv | `intellect update` | 升级 Python 包 + Rust wheel |
| Docker | `docker pull` | 拉取最新镜像 |
| Homebrew | `brew upgrade intellect-agent` | 由 Homebrew 管理 |

`intellect update` 自动保持 Rust 扩展同步：
- **Git 安装**：每次 pull 后通过 `maturin develop --release` 重建
- **pip 安装**：升级主包时同步升级 `intellect_community_core` wheel

---

## CLI 与消息平台 快速对照

Intellect 有两种入口：用 `intellect` 启动终端 UI，或运行网关从 Telegram、Discord、Slack、WhatsApp、Signal 或 Email 与之对话。进入对话后，许多斜杠命令在两种界面中通用。

| 操作 | CLI | 消息平台 |
|------|-----|----------|
| 开始对话 | `intellect` | 运行 `intellect gateway setup` + `intellect gateway start`，然后给机器人发消息 |
| 开始新对话 | `/new` 或 `/reset` | `/new` 或 `/reset` |
| 更换模型 | `/model [provider:model]` | `/model [provider:model]` |
| 设置人格 | `/personality [name]` | `/personality [name]` |
| 重试或撤销上一轮 | `/retry`、`/undo` | `/retry`、`/undo` |
| 压缩上下文 / 查看用量 | `/compress`、`/usage`、`/insights [--days N]` | `/compress`、`/usage`、`/insights [days]` |
| 浏览技能 | `/skills` 或 `/<skill-name>` | `/skills` 或 `/<skill-name>` |
| 中断当前工作 | `Ctrl+C` 或发送新消息 | `/stop` 或发送新消息 |
| 平台特定状态 | `/platforms` | `/status`、`/sethome` |

完整命令列表请参阅 [CLI 指南](https://intellect.ontoweb.cn/docs/user-guide/cli) 和 [消息网关指南](https://intellect.ontoweb.cn/docs/user-guide/messaging)。

---

## 文档

所有文档位于 **[intellect.ontoweb.cn/docs](https://intellect.ontoweb.cn/docs/)**：

| 章节 | 内容 |
|------|------|
| [快速开始](https://intellect.ontoweb.cn/docs/getting-started/quickstart) | 安装 → 设置 → 2 分钟内开始首次对话 |
| [CLI 使用](https://intellect.ontoweb.cn/docs/user-guide/cli) | 命令、快捷键、人格、会话 |
| [配置](https://intellect.ontoweb.cn/docs/user-guide/configuration) | 配置文件、提供商、模型、所有选项 |
| [消息网关](https://intellect.ontoweb.cn/docs/user-guide/messaging) | Telegram、Discord、Slack、WhatsApp、Signal、Home Assistant |
| [安全](https://intellect.ontoweb.cn/docs/user-guide/security) | 命令审批、DM 配对、容器隔离 |
| [工具与工具集](https://intellect.ontoweb.cn/docs/user-guide/features/tools) | 40+ 工具、工具集系统、终端后端 |
| [技能系统](https://intellect.ontoweb.cn/docs/user-guide/features/skills) | 过程记忆、技能中心、创建技能 |
| [记忆](https://intellect.ontoweb.cn/docs/user-guide/features/memory) | 持久记忆、用户画像、最佳实践 |
| [MCP 集成](https://intellect.ontoweb.cn/docs/user-guide/features/mcp) | 连接任意 MCP 服务器扩展能力 |
| [定时调度](https://intellect.ontoweb.cn/docs/user-guide/features/cron) | 定时任务与平台投递 |
| [上下文文件](https://intellect.ontoweb.cn/docs/user-guide/features/context-files) | 影响每次对话的项目上下文 |
| [架构](https://intellect.ontoweb.cn/docs/developer-guide/architecture) | 项目结构、代理循环、关键类 |
| [贡献](https://intellect.ontoweb.cn/docs/developer-guide/contributing) | 开发设置、PR 流程、代码风格 |
| [CLI 参考](https://intellect.ontoweb.cn/docs/reference/cli-commands) | 所有命令和标志 |
| [环境变量](https://intellect.ontoweb.cn/docs/reference/environment-variables) | 完整环境变量参考 |

---

## 贡献

欢迎贡献！请参阅 [贡献指南](https://intellect.ontoweb.cn/docs/developer-guide/contributing) 了解开发设置、代码风格和 PR 流程。

贡献者快速开始——克隆并使用 `setup-intellect.sh`：

```bash
git clone https://gitee.com/ontoweb/intellect-agent.git
cd intellect-agent
./setup-intellect.sh     # 安装 uv、创建 venv、安装 .[all]、创建符号链接 ~/.local/bin/intellect
./intellect              # 自动检测 venv，无需先 source
```

手动安装（等效于上述命令）：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv venv --python 3.11
source venv/bin/activate
uv pip install -e ".[all,dev]"
python -m pytest tests/ -q
```

---

## 许可证

MIT — 详见 [LICENSE](LICENSE)。

由 [ONTOWEB](https://ontoweb.cn) 构建。
