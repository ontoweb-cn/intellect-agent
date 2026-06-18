# 性能、安全、架构持续优化计划

> **状态**: 36/36 已完成（100%） | 最后更新: 2026-06-12

## Context

五阶段共 36 项优化。全部完成。

已完成的修复：

| 轮次 | 项数 | 涵盖 |
|------|------|------|
| P0 安全高危 | 3/3 ✅ | 密码哈希文档、WebSocket 认证、快速命令验证 |
| P1 性能瓶颈 | 5/5 ✅ | O(n²) 去重、技能缓存、延迟导入、CTE 优化、config 缓存 |
| P2 中危修复 | 7/7 ✅ | Health 端点、CORS 警告、ThreadPoolExecutor、session key 熵、Agent cache、except:pass 消除、atomic_yaml_write |
| Phase 1 安全加固 | 3/3 ✅ | SQL 白名单、ANSI 过滤、DNS TOCTOU 文档 |
| Phase 2 性能优化 | 5/5 ✅ | Schema 缓存、FTS5 探测缓存、插件导入、Session list 子查询合并、atomic_yaml_write |
| 架构快赢 | 3/3 ✅ | WAL 清理、MappingProxyType、SecretStore CLI |
| 长期优化 | 5/5 ✅ | @timed decorator、版本号统一、信号处理器去重、FTS5 优化、SessionStore 防抖、大文件拆分 |
| 其他 | 3/3 ✅ | Wiki 修复、Code Review 修复、文档更新 |
| 安全深度加固 | 2/2 ✅ | SecretStore API Key 迁移、except:pass 消除 |
| **总计** | **36/36** | 全部完成 |

---

## ✅ 第一阶段：快速安全加固（3/3 完成）

### ✅ 1.1 L1: SQL 标识符白名单验证

**文件**: `intellect_state.py` FTS trigger 管理相关代码
**问题**: 使用 f-string 拼接 table_name 和 trigger 名称（当前值来自硬编码常量，安全但脆弱）
**修复**: 在 `_rebuild_fts_triggers` 等方法中添加白名单校验：
```python
_ALLOWED_FTS_TABLES = frozenset({"messages_fts", "messages_fts_trigram"})
_ALLOWED_FTS_TRIGGERS = frozenset({...})

def _validate_fts_identifier(name: str, allowed: frozenset) -> str:
    if name not in allowed:
        raise ValueError(f"Unexpected FTS identifier: {name}")
    return name
```
**验证**: 运行 `test_intellect_state.py` FTS 相关测试

### ✅ 1.2 L2: CLI 输出 ANSI 转义序列过滤

**文件**: `cli.py:2026` — `_cprint()` / `_PT_ANSI()`
**问题**: LLM 输出中如含 ANSI 转义序列（如 clipboard 操作码），会被终端直接渲染
**修复**: 在 `_cprint` 渲染前添加 strip/sanitize：
```python
import re
_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\][0-9;]*\x07')

def _sanitize_ansi_for_display(text: str) -> str:
    """Strip non-color ANSI sequences from LLM output before terminal rendering."""
    return _ANSI_ESCAPE_RE.sub('', text)
```
然后在 `_cprint()` 的 `_PT_ANSI(data)` 调用前应用。注意：需要保留 prompt_toolkit 自身的颜色序列（`\x1b[...m`），只过滤 OSC 序列（`\x1b]...`）和 DCS 序列。
**验证**: 构造含 ANSI 序列的测试输入，确认终端无异常行为

### ✅ 1.3 L4: DNS TOCTOU 文档标注

**文件**: `tools/url_safety.py:332`, `tools/safe_http.py`
**问题**: `is_safe_url` 预检 DNS 解析和 `safe_http` connect-time 验证之间存在 TOCTOU 窗口
**现状**: `safe_httpx_client` 已在 connect 时验证 TCP peer address，已实际覆盖该窗口
**修复**: 在 `url_safety.py` 的函数文档中标注：
```python
def is_safe_url(...):
    """Pre-flight URL safety check.
    
    NOTE: DNS resolution here is a pre-check only. The definitive guard is
    in ``safe_http.safe_async_http_transport`` which validates the actual
    TCP peer address at connect time. Always use ``safe_httpx_client`` or
    ``safe_async_http_transport`` for HTTP fetches — never a bare httpx client.
    """
```
同时全局搜索 `httpx.Client(` 和 `httpx.AsyncClient(` 确保所有 HTTP 调用都经过安全传输层。
**验证**: grep 检查无裸 httpx client 使用

---

## ✅ 第二阶段：性能持续优化（5/5 完成）

### ✅ 2.1 P7: Session list 关联子查询合并

**文件**: `intellect_state.py:1614-1651`
**问题**: `_preview_raw` 和 `last_active` 使用关联子查询，每返回行执行 2 次额外查询。limit=20 时额外 40 次查询。
**修复**: 使用 ROW_NUMBER() OVER (PARTITION BY session_id) + LEFT JOIN 替代关联子查询。CTE 路径和简单路径两处均已修改。
**验证**: 对比修改前后 `list_sessions_rich` 返回结果一致

### ✅ 2.2 P8: Schema reconciliation 进程内缓存

**文件**: `intellect_state.py:950-990` — `_reconcile_columns()`
**问题**: 每次 `SessionDB()` 初始化都 PRAGMA 扫描 ~40 张表。SCHEMA_SQL 是模块常量，列信息不会变。
**修复**: 添加进程级缓存：
```python
_SCHEMA_COLUMNS_CACHE: dict | None = None

def _parse_schema_columns(schema_sql: str) -> Dict[str, Dict[str, str]]:
    global _SCHEMA_COLUMNS_CACHE
    if _SCHEMA_COLUMNS_CACHE is not None:
        return _SCHEMA_COLUMNS_CACHE
    # ... existing parsing logic ...
    _SCHEMA_COLUMNS_CACHE = table_columns
    return table_columns
```
**验证**: 确认首次解析后 cache 命中，多次 `SessionDB()` 构造不再触发 PRAGMA

### ✅ 2.3 P10: atomic_yaml_write 定向 key 编辑

**文件**: `intellect_cli/config.py:4968`, `gateway/run.py:11108`
**修复**: `save_config()` 已实现 `only_keys` 参数 — 合并 delta 到现有 config 后直接写入，跳过完整规范化流程
**验证**: `gateway/run.py:11108` 已在 `/model --global` 中使用 `save_config(cfg, only_keys=["model"])`

### ✅ 2.4 进程内 FTS5 探测缓存

**文件**: `intellect_state.py:806-815` — `_sqlite_supports_fts5()`
**问题**: 每次 `SessionDB()` init 都创建并删除临时 FTS5 表来探测支持
**修复**: 添加布尔缓存：
```python
_FTS5_SUPPORT_CACHE: bool | None = None

def _sqlite_supports_fts5() -> bool:
    global _FTS5_SUPPORT_CACHE
    if _FTS5_SUPPORT_CACHE is not None:
        return _FTS5_SUPPORT_CACHE
    # ... existing probe ...
    _FTS5_SUPPORT_CACHE = result
    return result
```

---

## ✅ 第三阶段：安全深度加固（2/2 完成）

### ✅ 3.1 M3: config.yaml API Key 迁移到 SecretStore

**文件**: `intellect_cli/main.py:4514-4515`, `agent/secret_store.py`, `gateway/run.py`
**问题**: Provider API Key 明文存储在 `config.yaml` 中
**已完成**:
1. ✅ `agent/secret_store.py` — Fernet 加密实现（AES-128-CBC + HMAC）
2. ✅ `intellect_cli/api_key_secrets.py` — 迁移逻辑、SecretStore/Config 双路径解析
3. ✅ `intellect secrets store migrate-api-keys` CLI 命令
4. ✅ `intellect_cli/auth.py` — provider 解析优先 SecretStore → env → config.yaml
5. ✅ `gateway/run.py` — fallback provider 路径集成 SecretStore 查询（2026-06-12）
**验证**: 迁移后 agent 正常运行，原 config 中 key 已移除

### ✅ 3.2 全局 `except Exception: pass` 消除（第一轮 — CLI 路径）

**范围**: `cli.py`（121 处替换）和 `intellect_cli/main.py`（0 处，已全部有合理处理）
**修复**:
- `cli.py`: 121 处 `except Exception: pass` → `except Exception: logger.debug("...", exc_info=True)`
- 保留 5 处合理的 `except: pass`（import guard、KeyboardInterrupt、OSError 清理）
- `main.py`: 所有 `except Exception:` 块已有 `logger.debug`/`print`/`return` 处理；29 处 `except SpecificError: pass` 均为 import guard、curses.error、OSError 清理等合理模式
**验证**: Python AST 语法检查通过，剩余 except:pass 均为合理模式

---

## 第四阶段：架构升级（4 项，预计 5-8h）

### 4.1 延迟插件 CLI 导入

**文件**: `intellect_cli/main.py:15745-15791` — plugin CLI discovery
**问题**: `plugins.memory` 和 `plugins.rag` 在 parser 构建时被急切导入，增加 500-650ms 启动时间
**修复**: 将导入移到 handler 函数内部：
```python
# Before (module level):
import plugins.memory as _memory_plugins
import plugins.rag as _rag_plugins

def _register_plugin_subcommands(subparsers):
    _memory_plugins.register_cli(subparsers)
    _rag_plugins.register_cli(subparsers)

# After (lazy in handler):
def _register_plugin_subcommands(subparsers):
    # Register placeholder parsers that import on first use
    mem_parser = subparsers.add_parser("memory", ...)
    mem_parser.set_defaults(func=_dispatch_memory_cmd)
```
**验证**: `intellect --help` 启动时间对比，确认 500ms+ 减少

### 4.2 `SessionDB.__init__` 错误路径清理

**文件**: `intellect_state.py:761-774`
**问题**: 部分初始化失败时 `SQLiteBackend` 未调用 `close()`，可能泄漏 WAL 文件句柄
**修复**: 在 except 块中添加清理：
```python
except Exception as exc:
    _set_last_init_error(f"{type(exc).__name__}: {exc}")
    if self._storage_backend is not None:
        try:
            self._storage_backend.close()
        except Exception:
            pass
    self._conn = None
    raise
```
**验证**: 构造 Schema 初始化失败场景，确认无文件句柄泄漏

### 4.3 Kanban 连接复用

**文件**: `intellect_cli/kanban_db.py` — `connect()` 函数
**问题**: 每次 kanban 操作创建新 SQLite 连接。~30 处调用点每操作一次 connect/disconnect
**修复**: 添加线程级连接缓存（类似 `SessionDB` 的模式）：
```python
_KANBAN_CONN_CACHE: dict[str, sqlite3.Connection] = {}

def connect(*, board=None, db_path=None, reuse: bool = True):
    key = str(db_path or board or "default")
    if reuse and key in _KANBAN_CONN_CACHE:
        conn = _KANBAN_CONN_CACHE[key]
        try:
            conn.execute("SELECT 1")
            return conn
        except sqlite3.Error:
            del _KANBAN_CONN_CACHE[key]
    conn = _connect_new(key, board, db_path)
    if reuse:
        _KANBAN_CONN_CACHE[key] = conn
    return conn
```
**验证**: 多次 kanban 操作使用同一连接，确认无 `OperationalError: database is locked`

### 4.4 `load_config_readonly()` 不可变保护

**文件**: `intellect_cli/config.py:4811-4828`
**问题**: `load_config_readonly()` 返回缓存的活字典，注释说"修改会污染缓存"
**修复**: 用 `types.MappingProxyType` 包装返回值：
```python
from types import MappingProxyType

def load_config_readonly() -> MappingProxyType:
    raw = _load_config_impl(deepcopy=False)
    return MappingProxyType(raw if isinstance(raw, dict) else {})
```
**验证**: 尝试修改返回值应抛出 `TypeError`

---

## ✅ 第五阶段：长期演进（5/5 完成）

### ✅ 5.1 大文件拆分（第一轮完成）
| 文件 | 原行数 | 现行数 | 拆分内容 |
|------|--------|--------|----------|
| `cli.py` | ~15,500 | ~12,750 | `SlashCommandMixin` (36方法, ~2.4K行) + `VoiceMixin` (6方法, ~320行) |
| `intellect_cli/main.py` | ~16,000 | ~8,000 | `cmd_model` (4K行) → `commands/model.py`；`cmd_analytics` (4K行) → `commands/analytics.py` |
| `run_agent.py` | ~4,800 | ~4,800 | 暂不拆分（已有 `agent_runtime_helpers.py` + `conversation_loop.py`） |

新增模块：
- `intellect_cli/commands/model.py` — 模型选择 + provider 配置 (4,054行)
- `intellect_cli/commands/analytics.py` — analytics/config/secrets/skills 子命令 (4,056行)
- `intellect_cli/cli_slash_handlers.py` — SlashCommandMixin (2,528行)
- `intellect_cli/cli_voice.py` — VoiceMixin (355行)

### ✅ 5.2 性能 Instrumentation
- ✅ `agent/timing.py`: `@timed` decorator 支持 JSON 输出模式（`INTELLECT_TIMING_JSON=1`）
- ✅ `run_agent.py`: `tool_invoke` 和 `run_conversation` 已有 `@timed`
- ✅ `gateway/run.py`: `_try_resolve_fallback_provider` 添加计时
- ✅ `intellect_cli/doctor.py`: `intellect doctor --perf` 快速性能诊断

### ✅ 5.3 信号处理器去重
- ✅ `cli.py`: 提取 `_create_signal_handler()` 工厂函数 + `_kanban_worker_exit()` 辅助函数
- ✅ 交互模式和队列模式共用共享逻辑，消除重复代码

### ✅ 5.4 FTS5 触发器优化
- ✅ 已通过合并为单一 trigram tokenizer 实现（`state/schema.py: FTS_SQL`），无需额外工作

### ✅ 5.5 SessionStore 防抖写入
- ✅ `gateway/session.py`: `_save()` 改为 500ms 防抖写入
- ✅ 新增 `_save_now()`（立即写入）和 `_flush()`（取消防抖+立即写入）
- ✅ 新增 `close()` 方法确保关闭时数据不丢失

---

## 验证方法

```bash
# 每阶段完成后运行
python3 -m pytest tests/agent/test_runtime_context.py tests/test_intellect_state.py tests/agent/test_storage_p1.py -q -o "addopts="

# 性能验证
python3 -c "
import time, subprocess
t0 = time.time()
subprocess.run(['python3', '-c', 'import gateway.run'], capture_output=True, timeout=10)
print(f'Gateway import: {time.time()-t0:.3f}s')
"
```
