# Intellect WebUI

浏览器端的 Intellect Agent 控制台 —— 管理会话、查看对话、配置系统设置。

## 功能概览

| 功能 | 说明 |
|------|------|
| 🤖 **会话管理** | 查看、搜索、继续、归档 agent 会话 |
| 💬 **实时对话** | SSE 流式传输，实时查看 agent 响应 |
| 👥 **成员管理** | 注册、审批、邀请成员，支持 OAuth/OIDC |
| 🔐 **认证安全** | 密码认证、WebAuthn/Passkey、TOTP 两步验证 |
| ⚙️ **系统配置** | 模型、Provider、工具集、Skills 配置 |
| 📊 **用量统计** | Token 用量、会话统计、成本跟踪 |
| 🗂️ **工作区** | 文件浏览、Git 集成、工作树管理 |
| 📋 **看板** | Agent 任务看板，可视化工作流 |
| 🔌 **扩展** | 插件/扩展管理 |
| 🔄 **Gateway 监控** | Gateway 会话实时同步 |
| 📱 **PWA** | 可安装为桌面/移动端应用 |

## 安装

WebUI 作为 `intellect-agent` 的可选依赖提供：

```bash
pip install intellect-agent[webui]
```

或安装全部可选依赖：

```bash
pip install intellect-agent[all]
```

### 依赖

`webui` extra 仅声明一个额外依赖：`cryptography>=42.0`（用于 WebAuthn/Passkey 认证），其余依赖（FastAPI 不在此包中，因为 WebUI 使用标准库 `http.server`）均为 Python 标准库或 agent 核心依赖。

## 快速开始

### 启动服务

```bash
# 后台启动（默认 127.0.0.1:9119）
intellect webui start

# 自定义地址和端口
intellect webui start --host 0.0.0.0 --port 8080
```

### 管理服务

```bash
intellect webui status     # 查看运行状态和健康检查
intellect webui logs       # 查看最近 100 行日志
intellect webui logs -f    # 实时跟踪日志
intellect webui restart    # 重启服务
intellect webui stop       # 停止服务
```

### 访问

浏览器打开 `http://127.0.0.1:9119`。

## CLI 命令参考

| 命令 | 说明 | 参数 |
|------|------|------|
| `intellect webui start` | 后台启动 WebUI 服务 | `--host` 绑定地址, `--port` 端口 |
| `intellect webui stop` | 停止 WebUI 服务 | - |
| `intellect webui restart` | 重启 WebUI 服务 | `--host`, `--port` |
| `intellect webui status` | 查看运行状态 | - |
| `intellect webui logs` | 查看服务日志 | `-n N` 行数 (默认100), `-f` 实时跟踪 |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `INTELLECT_WEBUI_HOST` | `127.0.0.1` | 绑定地址 |
| `INTELLECT_WEBUI_PORT` | `9119` | 监听端口 |
| `INTELLECT_WEBUI_PASSWORD` | - | 设置 WebUI 登录密码（未设置时无认证） |
| `INTELLECT_WEBUI_CSP_CONNECT_EXTRA` | - | CSP connect-src 额外来源（空格分隔） |
| `INTELLECT_WEBUI_TEST_NETWORK_BLOCK` | - | 测试模式：阻止非本地网络连接 |

## 远程访问

WebUI 默认绑定 `127.0.0.1`，仅供本机访问。远程访问推荐通过 SSH 隧道：

```bash
ssh -N -L 9119:127.0.0.1:9119 user@your-server
```

然后浏览器打开 `http://localhost:9119`。

如需直接绑定外部地址，**务必设置密码**：

```bash
export INTELLECT_WEBUI_PASSWORD="your-strong-password"
intellect webui start --host 0.0.0.0
```

## 文件布局

```
~/.intellect/
├── webui.pid          # 服务 PID
├── webui.log          # 服务日志
├── webui.ctl.env      # 运行时状态（host, port, 启动时间）
├── webui/
│   ├── sessions/      # WebUI 管理的 session 存储
│   ├── settings.json  # WebUI 界面设置和置顶
│   └── state.db       # 成员、认证等持久化状态
```

## 开发

### 直接运行

```bash
python -m webui.server
```

### 架构

参见 [ARCHITECTURE.md](./ARCHITECTURE.md)。

### 相关计划文档

- [WebUI 加固设计](../plans/2026-06-02-members-webui-hardening-design.md) — 成员系统/WebUI 安全加固
- [Teams WebUI 对等](../plans/archive/teams-webui-parity.md) — Teams 功能在 CLI/Gateway/WebUI 间对齐
- [WebUI 协作计划](../plans/archive/webui-collab-plan.md) — 早期协作计划
