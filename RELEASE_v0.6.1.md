# v0.6.1 — WebUI 控制台 + Rust 完善

## Overview

Intellect Agent v0.6.1 引入了完整的 WebUI 浏览器控制台，集成自独立仓库
`intellect-webui`，提供会话管理、实时流式对话、成员系统和系统配置的图形界面。
同时包含 Rust 核心层的多项修复和完善。

## WebUI 控制台

### 功能概览

| 功能 | 说明 |
|------|------|
| 🤖 会话管理 | 查看、搜索、继续、归档 agent 会话 |
| 💬 实时对话 | SSE 流式传输，实时查看 agent 响应 |
| 👥 成员管理 | 注册、审批、邀请成员，支持 OAuth/OIDC |
| 🔐 认证安全 | 密码认证、WebAuthn/Passkey、TOTP 两步验证 |
| ⚙️ 系统配置 | 模型、Provider、工具集、Skills 配置 |
| 📊 用量统计 | Token 用量、会话统计、成本跟踪 |
| 🗂️ 工作区 | 文件浏览、Git 集成、工作树管理 |
| 📋 看板 | Agent 任务看板，可视化工作流 |
| 📱 PWA | 可安装为桌面/移动端应用 |

### CLI 命令

```bash
intellect webui start          # 后台启动服务 (默认 127.0.0.1:9119)
intellect webui start --port 8080 --host 0.0.0.0
intellect webui stop           # 停止服务
intellect webui restart        # 重启服务
intellect webui status         # 运行状态 + 健康检查
intellect webui logs           # 查看日志 (-f 实时跟踪)
```

### 架构

- **后端**: `webui/server.py` — 基于标准库 `ThreadingHTTPServer`，零额外依赖
- **API**: `webui/api/` — 51 个路由/业务模块，集中路由分发 (`routes.py`)
- **前端**: `webui/static/` — 原生 JavaScript SPA，PWA 支持
- **进程管理**: `intellect_cli/webui.py` — PID 文件管理，优雅关闭 (SIGTERM → SIGKILL)

详见 `docs/webui/`。

### 文件统计

| 类别 | 文件数 | 行数 |
|------|--------|------|
| `webui/` 核心 (Python) | 53 | ~57,000 |
| `webui/static/` 前端 (JS/CSS/HTML) | 37 | ~12,000 |
| `webui/static/vault/` Wiki 知识库 | 180+ | — |
| `webui/static/vendor/` 第三方库 | 4 | — |

## Rust 核心层完善

| 模块 | 变更 |
|------|------|
| `crypto.rs` | Stage 5c: JWT claims decode with optional `exp` validation (+72/-5 行) |
| `stream.rs` | StreamAccumulator tool call argument repair — JSON 修复 (+62/-4 行) |
| `lib.rs` | 集中化 Rust imports (+3) |
| `intellect_rust.py` | Python 侧 Rust 绑定更新 (+97 行) |
| `intellect_state.py` | 状态管理更新 (+65/-1 行) |
| 其他 | 修复 Rust 编译器 warnings (unused variable, dead field) |

## 新增依赖

`webui` extras (可选):
- `cryptography>=42.0` — WebAuthn/Passkey 认证支持

## 文档

- `docs/webui/README.md` — WebUI 用户指南
- `docs/webui/ARCHITECTURE.md` — WebUI 架构设计
- `docs/plans/hindsight-graphiti-hybrid-memory-plan.md` — 混合记忆方案设计
- `docs/plans/rag-memory-collaboration-architecture.md` — RAG+Memory 协同架构
- 更新 `README.md`、`docs/plans/README.md` 交叉引用

## Breaking Changes

None. WebUI 为可选功能，通过 `pip install intellect-agent[webui]` 按需安装。

## Contributors

Co-Authored-By: Claude Opus 4.8
