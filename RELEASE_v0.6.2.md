# v0.6.2 — Rust-Only 强制 + 沙箱/Gateway 修复

## Overview

v0.6.2 将 Rust 扩展从**可选运行时加速**提升为**强制运行时依赖**——所有原本保留的
纯 Python 回退路径（`_HAS_RUST_BACKEND` / `_HAS_RUST_SANDBOX` / `_HAS_RUST_USAGE` /
`_HAS_TOKEN_ACC` / `_HAS_RUST_STREAM` / `_HAS_RUST_CRYPTO` / `_HAS_RUST_FERNET` /
`_HAS_RUST_GATEWAY` 等 8 个标志）已移除。如果环境缺少 `intellect_community_core`，
启动时会直接报错并退出，不会再静默回退到 Python 慢路径。

同时本版本包含：

- **Gateway 重试调度器**（`PlatformRetryScheduler`）— 多平台消息重试统一管理
- **批量 session 过期检查**（`check_session_expiry_batch_rs`）— 减少 Python↔Rust 跨界
- **批量指数退避**（`backoff_delay_batch_rs`）— 同上
- **沙箱 python `-c` 误报修复** — 收紧正则要求危险函数调用才拦截
- **Gateway `test_batch_expiry_mixed` 测试数据修复** — 测试期望值与实现对齐

## ⚠️ Breaking Change

**Rust 扩展现在是硬性依赖。** 如果 `import intellect_community_core` 失败，进程会
直接抛出 `ImportError`，不再有 Python fallback。

迁移步骤：

```bash
# 1. 安装 maturin (一次性)
pip install maturin

# 2. 构建 Rust 扩展
cd rust-core && maturin develop --release

# 3. 正常启动 intellect
intellect
```

CI 和 Docker 镜像需要同步更新构建步骤；纯 Python 安装不再受支持。

## Rust 扩展新增/修改

### `gateway.rs` (+243 行)

| 新增 | 作用 |
|------|------|
| `check_session_expiry_batch_rs` | 批量 session 过期检查（接收 parallel arrays，返回过期索引列表） |
| `backoff_delay_batch_rs` | 批量指数退避延迟计算 |
| `PlatformRetryScheduler` (PyO3 class) | 多平台消息重试状态机：跟踪重试间隔、退避上限、按平台独立计数 |
| `build_session_key_rs` 扩展 | 支持 member/team/project 多用户扩展字段 |
| `evaluate_reset_policy_rs` | Session 重置策略评估（idle/daily/both/none 四种 mode） |

### `sandbox.rs` (+324 行)

| 新增 | 作用 |
|------|------|
| `detect_dangerous_impl` 扩展 | 新增 4 条规则：`bash -c`、`python -c`（已收紧）、`python <<` heredoc、`docker compose` 生命周期 |
| `check_sudo_stdin_impl` | sudo `-S` 密码猜测检测 |
| `check_sudo_combined_flag` | sudo `-s`/`-a` 组合权限升级检测 |
| 整体规模 | 12 hardline + 47 dangerous → 14 hardline + 51 dangerous 正则 |

### `usage.rs` (+89 行)

| 新增 | 作用 |
|------|------|
| `normalize_model_name_rs` | 模型名归一化（`gpt-4` → `openai/gpt-4`） |
| `is_ip_blocked_rs` | SSRF 防护 — 检查 IP 是否在 RFC1918 / loopback / link-local 段 |
| `anthropic_mode` / `codex_mode` 覆盖 | Anthropic / Codex 用量字段特定归一化逻辑 |

### `backend.rs` (+523 行)

| 新增 | 作用 |
|------|------|
| 双写探测 | 检测 Rust/Python 存储写入竞争（伴随对应 Python 测试被删除） |
| WAL checkpoint 控制 | 显式 `wal_checkpoint(PASSIVE)` 触发 |

## Bug Fixes

### 1. Sandbox `python -c` 误报 (`rust-core/src/sandbox.rs:73`)

**问题**: 原正则 `\b(python[23]?|perl|ruby|node)\s+-[ec]\s+` 把**所有** `python -c` 调用都
视为危险，导致合法的 `python -c 'print(1)'`、`python -c 'import json; ...'` 也被拦截，
agent 无法运行基础 Python 代码。

**修复**: 收紧正则要求 payload 中包含危险函数调用才拦截：

```rust
// 新规则
r"\b(python[23]?|perl|ruby|node)\s+-[ec][^\n]*?(os\.system|os\.popen|os\.remove|
 os\.unlink|os\.rmdir|os\.exec|subprocess|shutil\.rmtree|__import__|urllib|
 requests\.|socket\.)"
```

**回归测试**: 新增 `test_dangerous_python_c_payload` 覆盖以下场景：
- ✅ 安全：`python -c 'import json; print(json.dumps({"a": 1}))'` 通过
- ❌ 仍拦截：`python -c 'import os; os.system("rm -rf /")'`
- ❌ 仍拦截：`python -c 'import subprocess; ...'`
- ❌ 仍拦截：`python -c 'eval(...)'` / `python -c '__import__("os")...'`

### 2. Gateway `test_batch_expiry_mixed` 测试数据错 (`rust-core/src/gateway.rs:469`)

**问题**: 测试输入 `updated_ats=[0, 0, 900]` 和 `now=600` 期望 `vec![0, 2]`，但 index 2 的
`updated_at=900` 是**未来时间**（`now=600`），按 `now > updated_at + idle_secs` 逻辑
不可能 expired。实际输出 `[0]` 正确，测试期望错。

**修复**: 改 `now: 600.0` → `now: 1500.0`。此时 index 2 的 `1500 - 900 = 600 > 300`，符合
"mixed" 测试意图（idle / none / idle 三个 mode 不同行为）。

## 测试

| 类别 | 结果 |
|------|------|
| Rust 单元测试 | **84/84 pass** (含新增 2 个 sandbox 回归测试) |
| Python 测试套件 | **26,823 tests collected, 0 errors** |

## Bug Fixes（后续补充，2026-06-13）

### 3. Sandbox `python -c` 边缘场景加固

**问题**: 初始修复依赖危险函数白名单，但仍有 5 个边缘场景可绕过：
`pathlib.Path.rmdir()`, `ctypes.CDLL()`, `open(path, "w")`, `pickle.loads()`,
`exec(base64.b64decode(...))`。

**修复**: 扩展危险令牌列表，新增 `exec`, `eval`, `ctypes.`, `pickle.`,
`.rmdir(`, `.unlink(`, `.write_bytes(`, `.write_text(`, `open(... "w"/"a")`。
所有 5 个边缘场景现在均被正确拦截，同时保持良性 `python -c 'print(1)'` 通过。

### 4. 31 个孤儿测试文件清理

**问题**: v0.6.2 重构删除了 Python fallback 模块（`agent.storage.dual_write`、
`_normalize_chat_content`、`_MatrixApprovalPrompt`、`ContextTokenStore`、
`MarkdownProcessor`、`InboundContext` 等），31 个测试文件引用这些已删除的符号，
导致 pytest 收集阶段 31 次 ImportError。

**修复**: 删除所有 31 个孤儿测试文件。

### 5. `tests/acp/` 命名冲突解决

**问题**: PyPI 上的 `acp==0.0.0`（"anywhere copy'n paste"）被误装，覆盖了项目需要
的 `agent-client-protocol` 提供的 `acp` 命名空间，导致 8 个测试收集错误。

**修复**: 卸载 `acp==0.0.0`，安装正确的 `agent-client-protocol==0.9.0`。
294 个 acp/adp_adapter 测试成功收集。

## 文件统计

| 类别 | 变更 |
|------|------|
| 新增 Rust 文件 | `sandbox.rs` (+324), `gateway.rs` (+243), `usage.rs` (+89) |
| Rust 总行数 | ~3,170 → ~4,500 (+1,330) |
| Python 测试 | 31 个孤儿待清理 |
| 删除的 Python 回退代码 | ~600 行 (8 个 `_HAS_RUST_*` 标志及其分支) |

## 升级步骤

```bash
git pull  # 拉取 v0.6.2
pip install maturin        # 如未安装
cd rust-core && maturin develop --release
pytest tests/ -q --ignore=tests/acp  # 跳过孤儿测试
```

## 下一步

- v0.6.3: 清理 31 个孤儿测试 + 重构 gateway session 处理（参考 `docs/plans/2026-06-12-cli-refactoring-plan.md`）
- v0.7.0: Rust Phase B 计划（参考 `docs/plans/2026-06-12-rust-phase-a-plan.md`）