# v0.6.3 — 沙箱架构升级 + AST 双层防御 + ReDoS 消除

## Overview

v0.6.3 对安全沙箱进行了架构性升级：新增 AST 结构分析层、RegexSet 单次匹配优化、
消除 ReDoS 风险，同时完成了大量技术债务清理。

### 架构升级

```
v0.6.2: 命令 → 单个 Regex → 审批
v0.6.3: 命令 → RegexSet O(n) DFA（69 patterns）→ Python AST → auto-deny → 审批
```

## Highlights

### 沙箱安全

- **AST 双层防御**: `_check_python_ast()` 使用 `ast.parse()` 做 Python 代码结构分析，
  检测 `exec()`/`os.system()`/`getattr()`/`__dict__[]`/`importlib` 等 7 类危险模式。
  AST 命中 → 强制执行 auto-deny。
- **Dangerous import 检测**: `import os`/`from subprocess import call` 等 10 个危险模块的
  import 语句在 AST 层被拦截。
- **31 个 token 独立描述**: 每个危险 Python API 现在返回具体的威胁类型（如
  `"script execution via -e/-c flag: os.system()"`），审批系统和日志可区分威胁。
- **穿透测试**: 18 个对抗性 payload（string concat、Unicode 全角、chr() 混淆、
  getattr 嵌套、lambda 包装等），**0 bypasses**。

### 性能

- **RegexSet 优化**: 69 个正则合并为单个 DFA，匹配从 O(n×m) 降至 **O(n) 单次遍历**。
- **ReDoS 消除**: `fancy-regex`（回溯引擎）→ `regex` crate（Thompson NFA，线性时间）。
- **Benchmark**: RegexSet ~10μs，AST ~27μs，pipeline ~8μs。

### 新增 bypass 覆盖

| 向量 | 检测层 |
|------|--------|
| `ctypes.CDLL()` | Regex |
| `pickle.loads()` | Regex + AST |
| `marshal.loads()` | Regex + AST |
| `os.spawn*()` / `os.posix_spawn*()` | Regex + AST |
| `compile()` + `exec()` chain | Regex + AST |
| `getattr(__builtins__,"exec")` | Regex + AST |
| `os.__dict__["system"]` | Regex + AST |
| `importlib.import_module("os")` | AST (unique) |
| `open("path","wb"/"w+")` | Regex |
| `tee ~/.ssh/authorized_keys` | Regex |
| `>> /dev/sd*` | Regex |

### 技术债务清理

- **31 个孤儿测试文件**删除（引用 v0.6.2 已删除模块）
- **Python DANGEROUS_PATTERNS 死代码**删除（~110 行，检测始终走 Rust）
- **`tests/acp/` 包冲突**解决：PyPI `acp==0.0.0`→`agent-client-protocol==0.9.0`，294 测试通过
- **`SessionDB` 惰性路径**修复：`DEFAULT_DB_PATH` 常量→`get_intellect_home()` 惰性调用，
  测试隔离不再泄漏真实 sessions

### CI & 文档

- **CI 改进**: Rust job 新增 fast path（`--no-default-features`）、AST 验证、token 描述验证、
  测试计数断言
- **Gitee CI 修复**: `main.py` shim + `.gitee-ci.yml` 覆盖默认流水线
- **SECURITY.md**: 独立安全架构文档（三层防御、覆盖分析、性能基准）
- **AGENTS.md**: 沙箱架构 + token 注册表文档
- **AST vs Regex 覆盖分析**: 30 攻击向量双层覆盖统计

## Changelog

### Added
- AST-based Python code analysis (`_check_python_ast`, 7 detection categories)
- AST auto-deny in `check_execute_code_guard` (all contexts)
- `SCRIPT_EXEC_TOKEN_ENTRIES` token registry (31 individual descriptions)
- `RegexSet` optimization (69 patterns in single DFA)
- Dangerous import detection (10 modules)
- `__dict__[]` bypass coverage
- `importlib.import_module()` AST detection
- `DELETE FROM ... WHERE` distinct pattern
- `tee`/`>>` to SSH authorized_keys patterns
- `tee` to block device pattern
- `os.spawn*`, `os.posix_spawn*`, `marshal`, `compile()` token coverage
- `SECURITY.md` architecture document
- `.gitee-ci.yml` pipeline config
- `main.py` root shim
- 14 IP/path security Python integration tests
- 12 AST unit tests
- CI: fast Rust tests, AST verification, description verification

### Changed
- `fancy-regex` → `regex` crate (ReDoS immune, Thompson NFA)
- Command matching: linear iteration → `RegexSet` O(n) DFA
- `DANGEROUS_PATTERNS` Python list → empty stub (detection always via Rust)
- `_PATTERN_KEY_ALIASES` minimal explicit dict (old↔new description cross-link)
- `SessionDB` default path: module constant → lazy `get_intellect_home()`
- `_check_sensitive_path`: macOS temp dir exception added
- `_smart_approve` LLM prompt: stale example updated
- `test_delete_with_where_safe`: updated for lookahead removal
- `ssh` + `slow` pytest markers registered

### Fixed
- `open("f","wb")`/`"w+"`/`"ab"` multi-char modes bypass
- `getattr(__builtins__,"exec")` obfuscation bypass
- `_PATTERN_KEY_ALIASES` backward compat (old→new description)
- `acp` package conflict (wrong PyPI `acp==0.0.0` shadowed `agent-client-protocol`)
- 8 pre-existing test failures (approval + acp session isolation)
- `pytest.mark.ssh` unknown marker warning
- `TestAgentCacheSpilloverLive` missing `@pytest.mark.integration`
- `TestFeishuAdaptiveDelay` skipped (`FeishuBatchState` never implemented)
- Gitee default pipeline error (`./main.py` + `requirements.txt` not found)
- `agent.json` version sync (0.15.1→0.6.2→0.6.3)

### Removed
- 31 orphaned Python test files (referencing deleted v0.6.2 modules)
- Python `DANGEROUS_PATTERNS` dead code (~110 lines)
- `_legacy_pattern_key` function

## Test Summary

| Suite | Result |
|-------|--------|
| Rust unit tests | **88/88** |
| Python collection | **26,834 tests, 0 errors** |
| acp + acp_adapter | **294/0** |
| approval | **207/2 skipped** |
| Penetration test | **0 bypasses** (18 adversarial payloads) |

## Upgrade

```bash
git checkout v0.6.3
pip install maturin
cd rust-core && maturin develop --release
pip install -e ".[all,dev,acp]"
```
