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

### [TODO-003] Python 测试运行超时

**状态**: 待调查
**影响**: pytest 运行超过 5 分钟未出结果

**详情**: 之前用 `--tb=line` 启动后台 pytest，5+ 分钟无输出。可能原因：
- 某些测试在真实等待（网络/IO）
- 测试之间死锁
- 某个测试在 sleep 死循环

**行动**:
1. 用 `pytest tests/<dir> -x` 逐目录跑定位慢测试
2. 加 `--timeout=60` 全局超时避免无限等待
3. 找出根因后决定是否调整测试隔离性

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

## 📝 完成归档

- ✅ **v0.6.2 本地 bug 修复**: sandbox python -c 正则 + gateway 测试数据
- ✅ **Rust 扩展构建**: maturin develop --release 在 Python 3.12 venv 中成功
- ✅ **Rust 测试**: 84/84 pass（新增 2 个回归测试）
- ✅ **31 个孤儿测试文件**: 已删除，pytest 收集零错误
- ✅ **acp 包冲突**: 解决，294 个 acp 测试成功收集
- ✅ **Sandbox 边缘场景**: 5/5 已覆盖

---

---

## 🔵 Phase C — 架构改进（长期）

### [TODO-007] AST 解析方案跟踪

**状态**: ✅ 已实现 (2026-06-14) — _check_python_ast() 已集成到 check_execute_code_guard，支持 7 类检测 + auto-deny，12 单元测试

**技术方案**: 在 approval.py check_execute_code_guard() 中集成 ast.parse()
对 Python -c 载荷做 AST 级节点检查（Call/Import/ImportFrom），
与正则互补：正则初筛 + AST 深度确认。

**参考**: tools/skills_ast_audit.py 已有 ast.NodeVisitor 模式可复用。

**行动**:
- [ ] 实现 _check_python_ast() 函数
- [ ] 集成到 check_execute_code_guard
- [ ] 添加单元测试
- [ ] 评估正则 token 精简
**来源**: Code review finding G2 — 正则黑名单无法穷举 Python 危险 API（marshal.load、
os.posix_spawn、compile()、动态 getattr 混淆等均确认可绕过）

**详情**: 当前 Python `-c` 检测基于正则黑名单，每发现一个 bypass 需要手动追加 token。
长期应迁移到 AST 级分析（使用 Python `ast` 模块解析 `-c` 载荷并检查危险节点类型）。

**行动**:
1. 在 issue tracker 中创建正式任务
2. 设计 AST 方案技术规格（需检查的节点类型：`Call` to dangerous functions,
   `Import`/`ImportFrom` of dangerous modules, `Exec`/`Eval` builtins）
3. 与 `check_execute_code_guard`（approval.py:1476）集成
4. 评估性能影响（每次 `-c` 调用额外 AST 解析开销）

---

## 📝 Phase A/B 完成归档

- ✅ **A1: fancy-regex → regex 迁移**: 消除 ReDoS，87/87 pass
- ✅ **A2: Python DANGEROUS_PATTERNS 死代码删除**: ~110 行移除
- ✅ **A3: filelock 依赖修复**: 解决 gateway 测试并行 AST 扫描争抢
- ✅ **B2: 危险函数注册机制**: `SCRIPT_EXEC_DANGEROUS_TOKENS` 常量切片，新增 token 只需追加一行
- 📋 **B3: 跨命令误报**: 接受为安全策略（宁可误报不可漏报）

---

最后更新: 2026-06-14 (Phase A/B 完成)

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
