# Intellect WebUI — 架构设计

## 整体架构

```
┌─────────────────────────────────────────────────────┐
│                    浏览器 (Browser)                   │
│  ┌─────────────────────────────────────────────────┐ │
│  │  static/ — SPA 前端                              │ │
│  │  index.html → boot.js → sessions.js / ...       │ │
│  │  SSE streaming, JSON API calls                  │ │
│  └──────────────────┬──────────────────────────────┘ │
└─────────────────────┼───────────────────────────────┘
                      │ HTTP (optional TLS)
┌─────────────────────┼───────────────────────────────┐
│              webui/server.py                         │
│  ┌──────────────────┴──────────────────────────┐    │
│  │  QuietHTTPServer (ThreadingHTTPServer)       │    │
│  │  - daemon_threads, queue_size=64             │    │
│  │  - IPv4/IPv6 auto-detect                     │    │
│  │  - Accept-loop heartbeat → /health           │    │
│  └──────────────────┬──────────────────────────┘    │
│                     │                                │
│  ┌──────────────────┴──────────────────────────┐    │
│  │  Handler (BaseHTTPRequestHandler)            │    │
│  │  - HTTP/1.1, keep-alive, TCP_NODELAY         │    │
│  │  - CSP Report-Only header                    │    │
│  │  - Structured JSON access logs               │    │
│  │  - Auth → Member Context → Route dispatch     │    │
│  └──────────────────┬──────────────────────────┘    │
│                     │                                │
│  ┌──────────────────┴──────────────────────────┐    │
│  │  api/routes.py — 路由分发中枢 (650KB)         │    │
│  │  handle_get / handle_post / handle_put ...   │    │
│  │  → 静态文件 / 会话CRUD / SSE / 配置 / ...     │    │
│  └──────────────────┬──────────────────────────┘    │
│                     │                                │
│  ┌──────────────────┴──────────────────────────┐    │
│  │  api/*.py — 业务逻辑模块 (51 个模块)          │    │
│  │  auth, members, sessions, config, ...        │    │
│  └─────────────────────────────────────────────┘    │
│                     │                                │
│  ┌──────────────────┴──────────────────────────┐    │
│  │  intellect_cli/webui.py — CLI 进程管理        │    │
│  │  start/stop/restart/status/logs              │    │
│  └─────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

## 核心设计决策

### 1. 标准库 HTTP 服务

WebUI 使用 Python 标准库 `http.server.ThreadingHTTPServer`，而非 FastAPI/Flask。

**原因：**
- **零额外依赖** — 无需安装 Web 框架，降低部署复杂度
- **与 agent 共享进程** — 单进程即可提供 API + 静态文件服务
- **足够的需求** — WebUI 是本地/局域网单用户工具，不需要 ASGI 的高并发能力
- **标准库可控** — 线程模型简单，行为可预测，无框架升级风险

### 2. 前后端分离 SPA

前端是纯静态 SPA（`webui/static/`），通过 JSON API 和 SSE 与后端通信。

**数据流：**
- **页面加载** → `GET /` → `index.html` → JS 加载 → 调用 API
- **实时通信** → SSE (`text/event-stream`) 长连接推送 agent 响应
- **操作** → JSON API (`POST/PUT/PATCH/DELETE`)
- **认证** → Cookie-based session token

### 3. 单文件路由中枢

所有 HTTP 路由集中在 `api/routes.py`（~650KB）。每个 HTTP 方法的入口函数（`handle_get`, `handle_post`, ...）负责 URL 分发。

**路由模式：**
- `GET /` → 静态首页 `index.html`
- `GET /static/*` → 静态资源
- `GET /api/sessions` → JSON API
- `GET /api/sessions/<id>/stream` → SSE 流
- `POST /api/...` → 写操作 JSON API

### 4. 进程管理

WebUI 作为独立后台进程运行，由 `intellect webui` CLI 命令管理：

- **PID 文件** (`~/.intellect/webui.pid`) — 进程标识
- **状态文件** (`~/.intellect/webui.ctl.env`) — 运行时元数据
- **日志文件** (`~/.intellect/webui.log`) — 标准输出/错误重定向
- **健康检查** — `GET /health` 返回 JSON `{"status":"ok","sessions":N,"active_streams":M}`
- **优雅关闭** — SIGTERM (5s grace) → SIGKILL

## API 模块一览

| 模块 | 职责 |
|------|------|
| `routes.py` | HTTP 路由分发中枢 |
| `config.py` | 全局配置、路径发现、常量 |
| `auth.py` | 密码认证、session token |
| `members.py` | 成员注册/审批/邀请/管理 |
| `agent_sessions.py` | 会话列表、搜索、元数据 |
| `session_lifecycle.py` | 会话生命周期（创建/归档/删除） |
| `session_ops.py` | 会话操作（继续/停止/重命名） |
| `session_events.py` | SSE 事件推送（会话变更通知） |
| `session_recovery.py` | 会话数据恢复（#1558 .bak 恢复） |
| `session_visibility.py` | 会话隔离/可见性控制 |
| `streaming.py` | SSE 流式传输 agent 输出 |
| `profiles.py` | 多 Profile 管理 |
| `providers.py` | LLM Provider 配置 |
| `models.py` | 模型配置和数据模型 |
| `config.py` | 系统设置读写 |
| `workspace.py` | 工作区文件浏览/编辑 |
| `workspace_git.py` | Git 操作（status/diff/commit/branch） |
| `worktrees.py` | Git worktree 管理 |
| `terminal.py` | 终端模拟（执行命令） |
| `onboarding.py` | 新用户引导流程 |
| `oauth.py` | OAuth/OIDC 认证流程 |
| `oauth_providers.py` | OAuth Provider 配置 |
| `passkeys.py` | WebAuthn/Passkey 注册和认证 |
| `kanban_bridge.py` | Agent 看板数据桥接 |
| `kanban_events.py` | 看板事件 SSE 推送 |
| `gateway_watcher.py` | Gateway 会话实时同步 |
| `commands.py` | 命令执行和输出捕获 |
| `clarify.py` | 澄清问题处理 |
| `updates.py` | 软件版本更新检查 |
| `upload.py` | 文件上传 |
| `search.py` | 全文搜索 |
| `background.py` | 后台任务管理 |
| `compression_anchor.py` | 会话压缩锚点 |
| `extensions.py` | 插件/扩展管理 |
| `goals.py` | Agent 目标管理 |
| `helpers.py` | 共享工具函数（JSON 响应、body drain、cookie） |
| `metering.py` | 用量统计/计量 |
| `rollback.py` | 会话回滚 |
| `run_journal.py` | 运行日志记录 |
| `turn_journal.py` | 轮次日志记录 |
| `runtime_adapter.py` | 运行时适配 |
| `request_diagnostics.py` | 请求诊断 |
| `startup.py` | 启动自检和自愈 |
| `state_sync.py` | 状态同步 |
| `storage_api.py` | 存储 API |
| `storage_bridge.py` | 存储桥接 |
| `system_health.py` | 系统健康检查 |
| `agent_health.py` | Agent 健康监控 |
| `approval_events.py` | 审批事件处理 |
| `usage.py` | 用量查询 |
| `user_profile.py` | 用户 Profile |
| `wiki_contributions_handlers.py` | Wiki 贡献处理 |

## 前端架构

### 技术栈

- **原生 JavaScript** — 无框架依赖，模块化 JS 文件
- **PWA** — Service Worker (`sw.js`)、Manifest (`manifest.json`)、离线缓存
- **KaTeX** — 数学公式渲染
- **js-yaml** — YAML 解析
- **SMD** — Markdown 渲染

### 前端模块

| 文件 | 功能 |
|------|------|
| `index.html` | 主页面结构 |
| `boot.js` | 应用启动引导、路由 |
| `ui.js` | UI 框架（面板、布局、主题） |
| `sessions.js` | 会话列表和详情 |
| `messages.js` | 消息渲染 |
| `terminal.js` | 终端面板 |
| `members.js` | 成员管理 |
| `login.js` / `register.js` | 登录/注册页面 |
| `oauth.js` / `oauth-providers.js` | OAuth 流程 |
| `member-auth.js` / `member-oauth-providers.js` | 成员认证管理 |
| `onboarding.js` | 新用户引导 |
| `panels.js` | 面板系统 |
| `projects.js` | 项目管理 |
| `teams.js` | 团队管理 |
| `workspace.js` | 工作区文件浏览 |
| `commands.js` | 命令面板 |
| `canvas.js` | Canvas/SVG 渲染 |
| `code-cell.js` | 代码单元格 |
| `icons.js` | 图标库 |
| `i18n.js` | 国际化 |
| `wiki-panel.js` | Wiki 面板 |
| `user-profile.js` | 用户资料 |
| `sw.js` | Service Worker |
| `pwa-startup.js` | PWA 启动 |
| `style.css` | 全局样式 |

## 安全架构

### 认证链

```
请求到达
  │
  ├─ OPTIONS? → CORS preflight (Access-Control-Allow-Origin: *)
  │
  ├─ check_auth()
  │   ├─ 未设置密码 → 跳过认证（本地模式）
  │   ├─ Cookie token 有效 → 通过
  │   ├─ Authorization header → 验证
  │   └─ 失败 → 401 Unauthorized
  │
  ├─ bind_request_member_context()
  │   └─ 解析 member_id → 绑定到请求上下文
  │
  ├─ check_member_access()
  │   └─ 验证成员权限 → 通过/拒绝
  │
  └─ route_handler() → 执行业务逻辑
```

### 安全措施

- **CSP Report-Only** — 内容安全策略监控（`default-src 'self'`）
- **CORS** — 仅允许同源，OPTIONS 预检返回 `*`
- **CSRF 防护** — Cookie token + Origin/Referer 检查
- **TLS 可选** — 通过 `INTELLECT_WEBUI_TLS_CERT`/`INTELLECT_WEBUI_TLS_KEY` 环境变量启用
- **文件描述符限制** — 启动时自动提升 RLIMIT_NOFILE
- **权限修复** — 启动时自动修复敏感文件权限
- **网络隔离（测试）** — `INTELLECT_WEBUI_TEST_NETWORK_BLOCK` 阻止非本地出站连接

### 非本地绑定警告

绑定到非 loopback 地址且未设置密码时，服务启动会输出安全警告。

## 启动流程

```
main()
  ├─ print_startup_config()          # 输出配置信息
  ├─ _raise_fd_soft_limit()          # 提升文件描述符限制
  ├─ fix_credential_permissions()     # 修复敏感文件权限
  ├─ validate_webui_ha_startup()     # 多 Worker 模式检查
  ├─ recover_all_sessions_on_startup() # 会话恢复 (#1558)
  ├─ 安全警告检查
  ├─ verify_intellect_imports()      # 验证 agent 导入
  │   └─ auto_install_agent_deps()   # 自动安装缺失依赖
  ├─ 创建运行时目录 (STATE_DIR, SESSION_DIR, WORKSPACE)
  ├─ start_watcher()                 # Gateway 会话监听
  ├─ start_session_events_bridge()   # SSE 事件桥
  ├─ start_kanban_events_bridge()    # 看板事件桥
  ├─ TLS 设置 (可选)
  └─ httpd.serve_forever()           # 开始服务
```

## 关闭流程

```
serve_forever() 退出
  ├─ stop_watcher()                  # 停止 Gateway 监听
  ├─ stop_session_events_bridge()    # 停止 SSE 事件桥
  └─ drain_all_on_shutdown()         # 排空 memory-provider 生命周期提交
```
