# Intellect Agent 核心层 Rust 迁移方案

## Overview

将性能关键的核心层（~30K 行）逐步迁移到 Rust，通过 PyO3 编译为 Python 原生扩展模块，保留 Python 工具层（~270 文件）不动。Rust 核心层已有 3,696 行，覆盖 Stage 1-5，Phase A 完成。

## 方案选型：PyO3 嵌入式

```
┌─────────────────────────────────────────┐
│  Python 层                               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │ CLI/TUI  │ │  Tools   │ │ Plugins  │ │
│  │ (保留)   │ │ (保留)   │ │ (保留)   │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ │
│       │             │             │       │
│  ┌────┴─────────────┴─────────────┴────┐ │
│  │         Python 适配层 (薄)          │ │
│  └────────────────┬───────────────────┘ │
├───────────────────┼─────────────────────┤
│  Rust 核心层       │ PyO3 FFI           │
│  ┌─────────────────┴──────────────────┐ │
│  │ SessionDB │ AIAgent  │ Gateway     │ │
│  │ Storage   │ Config   │ Session     │ │
│  │ FTS5      │ Sandbox  │ Crypto      │ │
│  └────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

## 六阶段迁移

### Stage 1: 存储引擎下沉（1-3 月）

Rust 实现：
- `SQLiteBackend` — 连接管理、WAL、写重试
- `SessionDB` — CRUD、schema 管理、FTS5 搜索
- `SessionStore` — session 索引（已从 JSON 迁到 SQLite）
- `Config Cache` — mtime 缓存、env var 展开

Python 保留：
- 所有 270+ 工具
- 平台适配器
- CLI/TUI

### Stage 2: 工具执行沙箱（与 Stage 1 并行）

Rust 实现：
- 文件路径验证（`is_forbidden_path`）
- URL 安全检查（`is_safe_url`、SSRF 防护）
- 命令注入检测（`detect_dangerous_command`）
- 子进程沙箱（`seccomp`/`pledge`）

### Stage 3: Agent 核心循环（3-6 月）

Rust 实现：
- 消息预处理、工具调用解析
- 流式响应处理（SSE 解析、token 累积）
- Fallback/重试逻辑
- Token 计数与 cost 跟踪

Python 保留：
- 工具实际执行（PyO3 回调）
- Memory/RAG provider

### Stage 4: Gateway 事件循环（6-12 月）

Rust 实现（tokio async）：
- Telegram/Discord/Slack/Matrix 长连接管理
- 消息队列、重连、限流
- Kanban 任务调度
- Session 过期管理

### Stage 5: OAuth + Crypto（与 Stage 4 并行）

Rust 实现：
- OAuth token 交换（HTTP + crypto）
- PKCE code verifier/challenge（SHA-256）
- JWT 验证（ring crate）
- Fernet 加密存储（AES-128-CBC + HMAC）

Python 保留：
- OAuth UI 流（浏览器打开、loopback server）

### Stage 6: 终极形态（可选，12 月+）

Rust 作为主进程（`intellectd`），通过 `cpython` crate 内嵌 Python 运行工具层。CLI 通过 Unix Socket 连接。

## 与大文件拆分协同

大文件拆分为 Rust 迁移划分模块边界。先拆后迁：

```
Phase 1 (已完成)       Phase 2 (拆分)         Phase 3 (Rust 迁移)

dead code 清除         intellect_state.py      Rust Stage 1
                       → state/               Storage + 搜索
                       run_agent.py
                       → agent/               Rust Stage 3
                       gateway/run.py          Agent 核心循环
                       → gateway/
                                              Rust Stage 4
                      拆出 10-15 个子模块      Gateway
                      接口稳定、测试覆盖
```

## 插件策略

Rust 宿主统一管理，插件只需选一种语言：

```
               Plugin trait (Rust)
                    │
          ┌─────────┴─────────┐
          │                   │
     Rust Plugin         Python Plugin
     (直接 impl)      (PyO3 桥接 impl)
          │                   │
     .so 动态加载       Python import
```

| 插件类型 | 语言 | 原因 |
|----------|------|------|
| 新存储后端 | Rust | 性能关键 |
| 安全沙箱扩展 | Rust | 内存安全 |
| Telegram/Discord | Python | SDK 现成 |
| RAG/Memory | Python | 生态依赖 |
| Skills | Python | LLM 驱动 |
| 自定义工具 | Python | 开发效率 |

## 分层判别标准

| 进 Rust | 留 Python |
|---------|-----------|
| 协议解析 | 调用外部 SDK |
| 密码学计算 | 操作 Python 对象 |
| 数据结构操作 | LLM prompt 上下文 |
| 高性能 I/O | 快速迭代功能 |
| 安全关键路径 | 生态依赖（PIL、BS4、Playwright） |

## Progress (2026-06-12)

### ✅ Stage 1-5: Rust Core Module (3,177 lines → 3,467 lines)

| Module | Lines | Stage | Status |
|--------|-------|-------|--------|
| `backend.rs` | 1,455 | 1 | ✅ SQLiteBackend + append_message + replace_messages + search_messages + list_sessions_basic + SessionDB CRUD |
| `connection.rs` | 258 | 1 | ✅ RustConnection + RustCursor + value conversion |
| `schema.rs` | 47 | 1 | ✅ Schema management |
| `fts.rs` | 189 | 1 | ✅ FTS5 triggers + rebuild + search |
| `compression.rs` | 144 | 1 | ✅ Compression tip chain walking |
| `sandbox.rs` | 298 | 2 | ✅ Command injection detection (hardline + dangerous) |
| `usage.rs` | 325 | 3 | ✅ TokenAccumulator + normalize_usage |
| `stream.rs` | 288 | 3 | ✅ StreamAccumulator (SSE delta state machine) |
| `gateway.rs` | 297 | 4 | ✅ session_key + reset_policy + backoff + TokenBucket |
| `crypto.rs` | 327 | 5 | ✅ PKCE + Fernet + JWT + secure_random |
| `lib.rs` | 68 | — | ✅ PyO3 module registration |

### ✅ Stage 1h: SessionDB CRUD (2026-06-12)

Added to `backend.rs`:
- `create_session(session_id, source, ...)` — 创建会话
- `end_session(session_id, reason)` — 结束会话
- `get_session_info(session_id)` — 会话元数据
- `list_sessions(member_id, limit, offset, active_only)` — 会话列表
- `get_messages(session_id, limit, offset, role)` — 消息查询
- `count_messages(session_id)` — 消息计数
- `update_session_tokens(session_id, ...)` — Token 计数更新
- `query(sql, params)` — 通用只读查询

### ✅ Python Integration (2026-06-12)

- `agent/storage/sqlite_backend.py` — RustSQLiteBackend 新增 CRUD 代理方法
- `intellect_rust.py` — 集中导入 + 可用性标志
- `Makefile` — 构建脚本 (rust-build, rust-dev, rust-check, rust-test)

### ✅ Completed: Large-File Split

### ✅ Completed: Large-File Split

| Source | Lines Before | Lines After | Modules Created |
|--------|-------------|-------------|-----------------|
| `intellect_state.py` | 4,238 | 3,712 | `state/schema.py`, `state/fts.py`, `state/compression.py`, `state/__init__.py` |
| `cli.py` | 15,664 | 15,521 | `intellect_cli/chat_console.py`, `intellect_cli/text_utils.py` |
| `run_agent.py` | 4,838 | 4,784 | `agent/timing.py`, `agent/errors.py` |
| `gateway/run.py` | 20,063 | 19,763 | `gateway/routing.py`, `gateway/helpers.py` |

New package structure ready for Rust Stage 1 migration.

### ✅ Completed: Performance & Security

- FTS5 unified trigram table (-50% write amplification)
- SessionStore JSON → SQLite migration
- O(n²) dedup → O(n) hash-set
- Skills scan mtime cache
- Config mtime cache for gateway hot path
- 121 bare `except:pass` → `logger.debug(exc_info=True)`（cli.py），main.py 已全部有合理处理
- WebSocket auth (`TUI_AUTH_TOKEN`)
- Health endpoint info leak fixed
- CORS wildcard warning
- ThreadPoolExecutor for agent eviction
- Session key entropy: 24 → 128 bits

## 后续计划

详见 [Rust Phase A 实施计划](2026-06-12-rust-phase-a-plan.md)。

四阶段迁移路线：
- **Phase A** ✅ 完成: search_messages / list_sessions_basic / append/replace 完全集成到 Rust
- **Phase B** (2-4 周): Agent 核心循环下沉 — 消息预处理、工具调用解析
- **Phase C** (1-2 月): Gateway 事件循环 — SessionStore、消息队列、限流
- **Phase D** (与 B/C 并行): 安全路径加固 — URL/路径检查 Rust 化

## 参考

- [PyO3](https://pyo3.rs/) — Rust bindings for Python
- [maturin](https://www.maturin.rs/) — Build and publish Rust-based Python packages
- [cpython](https://github.com/dgrunwald/rust-cpython) — Embed Python in Rust
