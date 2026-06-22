# TODO

按优先级排序的待办事项追踪。本文件由开发会话持续更新。

---

## 🔴 P0 — 阻塞性问题

### [TODO-001] ~~清理 31 个孤儿 Python 测试文件~~ ✅ 已完成

**状态**: 已修复 (2026-06-13)
**操作**: 删除 31 个引用已删除模块的测试文件，pytest 收集从 26,616 提升到 26,823（零错误）。
**提交**: 见 `M tests/*.py` (31 删除)

---

### [TODO-002] ~~`tests/acp/` 和 `tests/acp_adapter/` 命名冲突~~ ✅ 已完成

**状态**: 已修复 (2026-06-13)
**根因**: PyPI `acp` 包 (v0.0.0, "anywhere copy'n paste") 被误装，覆盖了项目需要的 `agent-client-protocol` 提供的 `acp` 命名空间。
**操作**:
1. 卸载 `acp==0.0.0`
2. 安装 `agent-client-protocol==0.9.0`（从 Aliyun TUNA 镜像）
3. 结果: 294 个 acp 测试成功收集，零错误

---

## 🟡 P1 — 改进项

### [TODO-003] ~~Python 测试运行超时~~ ✅ 已修复

**状态**: 已修复 (2026-06-22)
**提交**: `ed41748 fix(TODO-003): resolve pytest collection errors`

**根因分析**:
1. `tools/transcription_tools.py:42` 语法错误 — `from agent.safe_print import safe_print` 错插入另一 import 块中间，阻塞 5 个测试文件收集
2. TB3 shim 清理过度 — 6 个 `gateway/run.py` re-export shim 被删但测试仍依赖，阻塞 2 个测试文件

**修复后**: 26,820 测试在 6.59s 内完成收集（之前 26,657 / 7 错误），仅剩 1 个预存 `_PROVIDER_PREFIXES` 导入错误

**全局超时**: `--timeout=30` 已在 `pyproject.toml` addopts 中配置

---

### [TODO-004] ~~Sandbox `python -c` 规则后续加固~~ ✅ 已加固

**状态**: 已加固 (2026-06-13)，AST 方案留待后续
**提交**: `rust-core/src/sandbox.rs` (扩展正则 + 新增 1 个测试)

**5 个边缘场景已全部覆盖**:

| 边缘场景 | 状态 |
|---------|------|
| `python -c 'from pathlib import Path; Path("/etc").rmdir()'` | ✅ 已拦截 (`.rmdir\(`) |
| `python -c 'import ctypes; ctypes.CDLL("libc.so.6")'` | ✅ 已拦截 (`ctypes\.`) |
| `python -c 'open("/etc/passwd","w").write("...")'` | ✅ 已拦截 (`open\(...["'][wa]["']`) |
| `python -c 'import pickle; pickle.loads(b"...")'` | ✅ 已拦截 (`pickle\.`) |
| `python -c 'import base64, sys; exec(base64.b64decode("..."))'` | ✅ 已拦截 (`\bexec\b`) |

**新增危险令牌**: `exec`, `eval`, `ctypes.`, `pickle.`, `.rmdir(`, `.unlink(`, `.write_bytes(`, `.write_text(`, `open(... [wa])`
**Rust 测试**: 84/84 ✅ (新增 `test_dangerous_python_c_edge_cases`)

---

## 🟢 P2 — 跟进

### [TODO-005] ~~把 v0.6.2 bug fix 提交到云端~~ ✅ 已提交

**状态**: 已提交 (2026-06-13)
**修改文件**:
- `rust-core/src/sandbox.rs` (扩展 python -c 正则 + 新增 2 个测试)
- `rust-core/src/gateway.rs` (修正 test_batch_expiry_mixed 测试数据)
- `intellect_community_core/__init__.py` (新建 — maturin 构建所需)
- `CHANGELOG.md` (新增 v0.6.2 行)
- `RELEASE_v0.6.2.md` (新增)
- `TODO.md` (本文件 — 更新状态)
- 31 个测试文件删除 (孤儿测试)
- `AGENTS.md` (uv 镜像配置说明)

---

### [TODO-006] uv 镜像配置文档化

**状态**: ✅ 已完成

**详情**: 已在 `AGENTS.md` 的 "Network / PyPI mirror" 节中记录 uv 镜像配置。

---

### [TODO-008] ~~Rust 迁移技术债务 (T1-T4)~~ ✅ 已完成

**状态**: 已修复 (2026-06-18)
**提交**: `6f02024 chore: resolve Rust migration technical debt (T1-T4)`

| 项目 | 内容 |
|------|------|
| T1 | `rust-core/README.md`: 修正依赖引用 (fancy-regex→regex), 补充 hex/pbkdf2, 行数更新 ~3,170→~4,500 |
| T2 | CI parity 测试: 新增 32 个覆盖 Crypto/Sandbox/Usage/Gateway/Stream 的 parity 测试 (17→49), CI 最低断言 >=45 |
| T3 | `pyproject.toml`: Rust 扩展注释从 "optional" 修正为 "required since v0.6.2" |
| T4 | 死代码移除: `intellect_rust.py` ~65 行 fallback wrappers, `agent/agent_init.py` try/except 吞异常修复, 3 处过期注释修正 |

---

## 📝 完成归档

- ✅ **A1 gateway/run.py 单体拆分 (TODO-009)**: 19,808→10,098行 (-49.0%), 5 mixin + 4 helper, MRO 派发链
- ✅ **A1 技术债务 (TB1-TB7)**: getter 去重, shim 清理, Rust warning, TYPE_CHECKING, accessor 统一, 剩余函数迁出
- ✅ **TODO-003 pytest 超时**: 语法错误 + shim 恢复, 26,820 测试 6.59s 收集
- ✅ **P5**: get_session() 默认跳过 system_prompt (100K+), 仅 1 个调用方显式加载
- ✅ **WebUI 进程组终止**: POSIX os.killpg, 防止孤儿进程
- ✅ **T1-T4 Rust 迁移技术债务**: 死代码移除 ~112 行, CI parity 测试 17→49, 文档修正
- ✅ **AST 双层防御 (TODO-007)**: 7 类检测 + auto-deny, 18 payloads 渗透 0 bypasses
- ✅ **v0.6.2 本地 bug 修复**: sandbox python -c 正则 + gateway 测试数据
- ✅ **Rust 扩展构建**: maturin develop --release 在 Python 3.12 venv 中成功
- ✅ **Rust 测试**: 84/84 pass（新增 2 个回归测试）
- ✅ **31 个孤儿测试文件**: 已删除，pytest 收集零错误
- ✅ **acp 包冲突**: 解决，294 个 acp 测试成功收集
- ✅ **Sandbox 边缘场景**: 5/5 已覆盖

---

---

## 🔵 Phase C — 架构改进（长期）

### [TODO-009] ~~A1: `gateway/run.py` 单体拆分~~ ✅ 已完成

**状态**: ✅ 全部阶段完成 (2026-06-22)
**影响**: `gateway/run.py` 19,808 → 10,510 行 (-46.9%)
**提交**: `5858667` (Phase 1-6), `f72ca26` (TB1-TB4 技术债务)

**最终结构**:

| 模块 | 行数 | 内容 |
|------|:---:|------|
| `run.py` | 10,510 | GatewayRunner 骨架 + 13 个核心方法 (start/stop/dispatch) |
| `command_handlers.py` | 3,705 | 57 命令处理器 + `_COMMAND_DISPATCH` 注册表派发 |
| `agent_runner.py` | 3,865 | 29 agent 生命周期方法 (_run_agent, cache, cleanup) |
| `platform_handlers.py` | 1,399 | 41 Telegram/平台适配方法 |
| `infrastructure_handlers.py` | 4,363 | 90 session/通知/认证/voice/goal/watcher/queue 方法 |
| `helpers.py` | 442 | 网络/SSL/错误/时间/媒体/lazy accessor |
| `message_helpers.py` | 324 | 消息构建/转录回放/Telegram/媒体占位 |
| `config_helpers.py` | 504 | 二进制解析/home-target env/配置加载/认证回退/运行时缓存 |
| `skill_session_helpers.py` | 305 | 中断常量/Skill/Session/Agent响应 |

**MRO 链**: `GatewayRunner → CommandHandlers → AgentRunner → PlatformHandlers → InfrastructureHandlers`

**A1 技术债务** (提交 `f72ca26`, `1a5e7e8`):
- TB2: `_get_pending_sentinel()` 去重 → helpers.py
- TB3: 29 无用 re-export shim 移除 (后恢复 6 个测试需要的)
- TB4: Rust unused import 修复
- TB6: `_get_intellect_home()` 统一到 helpers.py
- TB7: 9 个剩余函数迁至 config_helpers.py (run.py -418行)
- `_intellect_home` / `_AGENT_PENDING_SENTINEL` 裸名引用 → lazy accessor
- `/start` logging 丢失, `_log_non_critical()` 丢失
- `_DESTRUCTIVE_CONFIRM_COMMANDS` 死代码, F401 死导入

---

### [TODO-007] ~~AST 解析方案跟踪~~ ✅ 已完成

**状态**: ✅ 已实现 (2026-06-14) — `_check_python_ast()` 已集成到 `check_execute_code_guard`，支持 7 类检测 + auto-deny，12 单元测试。

**技术方案**: 在 approval.py 中集成 `ast.parse()` 对 Python `-c` 载荷做 AST 级节点检查（Call/Import/ImportFrom），与正则互补：正则初筛 + AST 深度确认。

**完成项**:
- [x] 实现 `_check_python_ast()` 函数
- [x] 集成到 `check_execute_code_guard`
- [x] 添加单元测试 (12 个)
- [x] 渗透测试: 18 adversarial payloads, 0 bypasses (NFKC 归一化 + AST + regex 三层覆盖)
- [x] 评估正则 token 精简 (保留 30 攻击向量双层覆盖)

**来源**: Code review finding G2 — 正则黑名单无法穷举 Python 危险 API（marshal.load、os.posix_spawn、compile()、动态 getattr 混淆等均确认可绕过）

---

## 📝 Phase A/B 完成归档

- ✅ **A1: fancy-regex → regex 迁移**: 消除 ReDoS，87/87 pass
- ✅ **A2: Python DANGEROUS_PATTERNS 死代码删除**: ~110 行移除
- ✅ **A3: filelock 依赖修复**: 解决 gateway 测试并行 AST 扫描争抢
- ✅ **B2: 危险函数注册机制**: `SCRIPT_EXEC_DANGEROUS_TOKENS` 常量切片，新增 token 只需追加一行
- 📋 **B3: 跨命令误报**: 接受为安全策略（宁可误报不可漏报）

---

最后更新: 2026-06-22 (A1 完成, TODO-003, TB1-TB7, P5, webui fix)

---

## AST vs Regex 覆盖分析 (2026-06-14)

30 个攻击向量的双层覆盖:

| 层级 | 数量 | 代表 |
|------|------|------|
| **Both** | 23 | exec, os.system, subprocess, pickle, ctypes, ... |
| **Regex only** | 4 | Path.rmdir(), Path.unlink(), Path.write_bytes(), Path.write_text() |
| **AST only** | 1 | importlib.import_module().system() |
| **Neither** (benign) | 3 | print, json, list comprehension |

**结论**: 两层互补已验证 — Regex 擅长通用模式匹配（`.rmdir(`），AST 擅长结构化分析（importlib 链 + import 语句检测）。两者缺一不可。

**Penetration test (2026-06-14)**: 18 adversarial payloads, 0 bypasses. NFKC normalization handles Unicode tricks, AST catches importlib chains, regex catches generic method calls. Fullwidth exec correctly blocked after normalization.
