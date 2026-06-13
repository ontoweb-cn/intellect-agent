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

最后更新: 2026-06-13 (本次会话)
