# v0.6.0 — Rust 核心层迁移

## Overview

Intellect Agent v0.6.0 引入 Rust (PyO3) 原生扩展 `intellect_community_core`，将存储、安全、
Agent 核心、加密和 Gateway 工具五个关键域的计算密集型代码从 Python 迁移到 Rust。
所有路径均保留纯 Python 回退（`_HAS_RUST` 模式），Rust 扩展为可选运行时加速。

## Rust 扩展

| 文件 | 行数 | 职责 |
|------|------|------|
| `backend.rs` | 930 | SQLiteBackend — WAL 管理、写重试、10 个高频写操作 |
| `crypto.rs` | 400 | PKCE (RFC 7636)、Fernet (AES-128-CBC+HMAC)、安全随机数 |
| `usage.rs` | 325 | `normalize_usage` 归一化 + `TokenAccumulator` 原子计数器 |
| `sandbox.rs` | 281 | 命令安全沙箱 — 59 个正则模式 (fancy-regex) |
| `gateway.rs` | 290 | Session key 构建器、过期策略、指数退避、令牌桶限流 |
| `connection.rs` | 258 | `RustConnection`/`RustCursor` DB-API 兼容代理 |
| `stream.rs` | 247 | `StreamAccumulator` — SSE delta 状态机 |
| `fts.rs` | 189 | FTS5 触发器/索引工具 (rusqlite) |
| `compression.rs` | 144 | 压缩链 CTE 遍历 |
| `schema.rs` | 47 | FTS 标识符白名单 |

**总计: 11 文件, ~3,170 行**

## Python 集成

20+ Python 模块已集成 Rust 加速路径：

| 模块 | 标志 | 功能 |
|------|------|------|
| `agent/storage/sqlite_backend.py` | `_HAS_RUST_BACKEND` | SQLite 存储后端 |
| `tools/approval.py` | `_HAS_RUST_SANDBOX` | 命令危险检测 |
| `agent/usage_pricing.py` | `_HAS_RUST_USAGE` | Token 归一化 |
| `run_agent.py` | `_HAS_TOKEN_ACC` | Token 计数器（主数据源） |
| `agent/chat_completion_helpers.py` | `_HAS_RUST_STREAM` | SSE delta 累积 |
| `agent/oauth/__init__.py` | `_HAS_RUST_CRYPTO` | PKCE + 安全随机 |
| `agent/oauth/storage.py` | `_HAS_RUST_FERNET` | OAuth token 加密 |
| `agent/secret_store.py` | `_HAS_RUST_FERNET` | Secret 存储加密 |
| `gateway/session.py` | `_HAS_RUST_GATEWAY` | Session key 构建 |

## Performance

| Operation | Python | Rust | Speedup |
|-----------|--------|------|---------|
| `normalize_usage` | 0.004ms | 0.001ms | **2.6x** |
| `build_session_key` | 0.002ms | 0.001ms | **1.2x** |

## Build

```bash
pip install maturin
cd rust-core
maturin develop         # dev install
maturin build --release # release wheel
```

## CLI 重构

v0.6.0 同时包含 cli.py 的大规模重构：

| 改动 | 说明 |
|------|------|
| Mixin lazy import | 修复 `cli_slash_handlers.py` 中 24 个方法的 `NameError`（`_cprint` 等符号） |
| 命令包迁移 | `commands.py` → `commands/registry.py`（包结构） |
| `_process_loop` 提取 | 149 行从 `run()` 提取为独立方法 |
| `_handle_enter` 提取 | 190 行从 `run()` 提取为独立方法 |
| `_COMMAND_DISPATCH` | 44 entries 的命令快速分发表 |
| 辅助函数模块化 | `_panel_box_width` 等 4 个函数去重为模块级 |
| `_display_chat_response` | 35 行从 `chat()` 提取 |
| `_maybe_auto_title` | 27 行从 `chat()` 提取 |
| 缺失方法恢复 | 12 个被删除的方法恢复 + 240 行死代码清除 |
| 测试覆盖 | 新增 `test_mixin_lazy_imports.py`（19 个 bytecode 验证测试） |

**效果**: cli.py 从 15,527 行降至 15,133 行（-394），run() 从 2,341 降至 2,022 行，chat() 从 627 降至 573 行。

## Breaking Changes

None. All Rust paths are optional with pure-Python fallbacks.

## Contributors

Co-Authored-By: Claude Opus 4.8
