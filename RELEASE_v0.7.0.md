# v0.7.0 — profile → agent 重命名 + PyO3 0.29 迁移 + Rust 流水线修复

## Overview

v0.7.0 是一条**身份重命名 + 构建链现代化**的版本。产品语义没变，但两处"地基"被换掉
了：隔离单元从 `profile` 正名为 `agent`，Rust 绑定从 pyo3 0.21 跨到 0.29.2。

**重命名是带兼容层的正名，不是硬改**。canonical 名一律写成 `agent`，但 `profile` 的旧拼写
在所有入口继续可读（CLI flag、HTTP 前缀、配置键、看板列、WS 鉴权变量），因此已有部署与脚本
不会被这次升级打断。落在磁盘上的 `~/.intellect/profiles/<name>/` 在首次访问时迁移到
`~/.intellect/agents/<name>/`。

**pyo3 跨 8 个小版本**是本次唯一有破坏性的依赖变更：0.21 → 0.29 期间 pyo3 的
`Bound<'py, T>` API、GIL 处理与若干 trait 签名都变了，`rust-core/src/` 下多个模块需要跟着改写。
副产物是一个长期存在的问题消失了——此前 `cargo test --no-default-features`（CI 的调用方式）
会在 backend.rs 的 GIL 测试处 aborts，迁移后同一命令干净跑通。

同时修好了一条**从未真正生效的 Rust CI 步骤**（sandbox-patterns 因引用了已删除的
`_HAS_RUST_SANDBOX` 而始终 ImportError 失败），以及一个**真实的安全回归**：
`is_ip_blocked_rs` 在切换到 Rust 快速路径时丢掉了 IANA 特殊用途地址段。

从 v0.6.9 到 v0.7.0 共 **24 个提交**，作者：simongu（24）。

## Highlights

### profile → agent 重命名（带旧名双读）

| 层面 | canonical | 旧拼写（仍可读） |
|------|-----------|------------------|
| 磁盘布局 | `~/.intellect/agents/<name>/` | `~/.intellect/profiles/<name>/`（首次访问迁移） |
| CLI | `intellect agent`、`-a` / `--agent` | `intellect profile`、`-p` / `--profile` |
| 配置键 | `agents.management_enabled` | `profiles.management_enabled`（OR 关系） |
| HTTP 前缀 | `/a/<agent>/…` | `/p/<agent>/…` |
| WebUI REST | `/api/agents`、`/api/agent/{active,switch,create,delete}` | `/api/profiles`、`/api/profile/…` |
| 看板 | `task_runs.agent` 列、run JSON/CLI 的 `agent` | `profile`（列保留、双写回填，不 DROP） |
| 网关状态 | `served_agents` | `served_profiles` |
| WS 鉴权 | `TUI_AUTH_TOKEN_<AGENT>` | 全局 `TUI_AUTH_TOKEN` |

**为什么是这样**：重命名最容易踩的坑是"改一半"——只改写入路径不改读取路径，或在同一个
版本里既改 canonical 又删旧名，导致升级即断。本次采用 **canonical-first、旧名长期可读**
的策略：新代码一律写 canonical 名，旧拼写只保留读取能力，不设删除期限。看板的
`task_runs.profile` 列**刻意不删**，新行两个列都写，读者任选其一都正确。

`intellect_cli/profiles.py` 从 1680 行瘦身为兼容 shim，实体逻辑迁入新的
`intellect_cli/agents_home.py`（1880 行）。

### PyO3 0.21 → 0.29.2

| 破坏点 | 处理 |
|--------|------|
| `Bound<'py, T>` API 取代裸指针风格 | `connection.rs` / `backend.rs` / `error_classifier.rs` / `prompt_caching.rs` 等按 0.29 签名改写 |
| GIL / `with_gil` 语义变化 | 调用点跟随新 API；此前会 abort 的测试面恢复稳定 |
| `lib.rs` 导出与 `tool_utils` 契约 | 跟随新版本调整 |

**一处附带收益**：pyo3 0.21 下 `cargo test --no-default-features` 会在
`pyo3-0.21.2/src/gil.rs:201` 报 "thread panicked while processing panic" 并 abort 整个进程，
把真实的逐测试结果掩盖掉。迁移后该命令跑出 **198 passed / 0 failed**。

### Rust CI 与安全修复

- **sandbox-patterns 步骤从未通过**：该步骤写在 `_HAS_RUST_SANDBOX` 被删除（4d3c9f9）之后，
  引用旧名导致 ImportError，绿灯从未点亮过。已改为使用 `intellect_rust.HAS_SANDBOX`。
- **`is_ip_blocked_rs` 安全回归**：`ad0397f` 把 `_is_blocked_ip` 切到 Rust 快速路径时，手写的
  检查漏掉了 Python `ipaddress` 表原本覆盖的 IANA 特殊用途段；`4d3c9f9` 又删掉了仍正确的
  Python 兜底，把缺口固化。已恢复 0.0.0.0/8、192.0.0.0/24（放行可路由 anycast .9/.10）、
  三段 TEST-NET 文档段、240.0.0.0/4，以及全球单播 2000::/3 之外的全部 IPv6 段，并与 Rust
  之前的基线逐项核对（零漏拦、零误拦）。
- deny.toml 迁移到 cargo-deny 0.16+ schema；Rust CI 现在会在自身 workflow 文件变更时也运行。
- 移除 rust-full job 中无法执行的 extension-module 测试步骤（该 feature 会抑制 libpython，
  测试二进制无法链接；纯 Rust 测试由 rust-unit job 以 `--no-default-features` 覆盖）。

### 两处静默缺陷修复

- **网关缺失 `import sqlite3`**：`gateway/infrastructure_handlers.py` 两处 `sqlite3.DatabaseError`
  引用却从未导入该模块。文件头部的 `# ruff: noqa: F821`（多数名字经 GatewayRunner MRO 解析）
  连同未定义名检查一并关掉，因此没有任何 lint 规则能拦住它。后果是看板板面损坏时，
  `except` 子句**自己**抛 NameError，把"数据库损坏"这条可操作信息降级成通用的
  "unexpected watcher error"。
- **`init_db()` 关闭连接池句柄**：`init_db()` 经 `connect()` 重新初始化后关掉了拿到的连接，
  而连接池 `_KANBAN_CONN_POOL` 虽注释写着 thread-local、实现却按**路径**共享，进程内所有
  调用者拿到的是同一个 connection。由于 `intellect kanban <verb>` 会在每条子命令前调用
  `init_db()`，而网关是长驻进程同时服务这些命令——一条 kanban 命令就会把 dispatcher 正在
  用的连接关掉。且 `executescript()` 执行前会先 commit，还可能提交别的线程的在途写入。
  已改为在**私有连接**上跑 schema/迁移，完全不碰连接池。

## Full Changelog

### ✨ Features

- **agents**: 隔离单元 profile → agent 正名，含旧名双读与磁盘布局迁移
- **agents**: 新增 `intellect_cli/agents_home.py`；`profiles.py` 瘦身为兼容 shim
- **agents**: 看板列、WebUI REST、网关分发改接到 canonical `agent`
- **agents**: 确认配置键提升（`agents.management_enabled`）与文档/术语清扫

### 🐛 Bug Fixes

- **gateway**: 补齐 corrupt-board guard 缺失的 `import sqlite3`
- **kanban**: `init_db()` 不再关闭连接池共享连接（私有连接执行 schema/迁移）
- **rust**: 恢复 `is_ip_blocked_rs` 中的 IANA 特殊用途 IP 段（真实 SSRF 回归）
- **rust**: 修复从未通过的 verification 测试与 CI 计数 grep
- **rust**: deny.toml 迁移到 cargo-deny 0.16+ schema
- **rust**: 每个 backend 测试使用独立临时 DB，修正 RW flag 注释
- **ci**: 修复从未通过的 sandbox-patterns 步骤
- **agents**: 补上重命名评审遗留的 WS / slash / 补全缺口

### ✅ Tests

- 修复指向陈旧目标的 Rust 桥接测试与网关 approval 测试
- `verification.rs` 临时 DB 助手与 `backend.rs` 对齐
- 网关测试在 macOS 上 stub 用户级 systemd D-Bus 预检

### 👷 CI/CD

- rust-full job 移除无法测试的 extension-module 步骤
- Rust CI 在自身 workflow 文件变更时触发

### 🔧 Chores

- pyo3 0.21 → 0.29.2；`rust-core/Cargo.lock` 自版本号同步

## Verification

```bash
# Verify version (auto-reads pyproject.toml)
python3 -c "from intellect_cli import __version__; print(__version__)"   # 0.7.0

# Rust handshake constant must match Cargo.toml
python3 -c "import intellect_community_core as c; print(c.RUST_CORE_VERSION)"   # 0.7.0

# Lockstep tests (pyproject ↔ Cargo ↔ rust handshake ↔ ACP registry)
.venv/bin/python -m pytest tests/acp/test_registry_manifest.py -q

# Pure Rust unit tests (CI's invocation)
cd rust-core && cargo test --no-default-features -- --test-threads=1   # 198 passed

# Rebuild the extension, then run the Python↔Rust bridge suite
maturin develop --release -m rust-core/Cargo.toml    # from repo root
.venv/bin/python -m pytest tests/intellect_state/test_rust_parity.py -q   # 58 passed
```

See [CHANGELOG.md](CHANGELOG.md) for the milestone-level index.
