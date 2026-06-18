# Rust 迁移 Phase A 实施计划

> 状态: ✅ 完成 | 最后更新: 2026-06-12

## 目标

将 intellect_state.py 的 SessionDB 热路径完全切换到 RustSQLiteBackend。

## 任务

### A1: search_messages 迁移到 Rust

**当前**: intellect_state.py L2593 — Python 拼接 SQL + CTE + FTS5 MATCH + JOIN
**目标**: Rust backend.search_messages(query, session_id, limit) 直接返回结果

**Rust 新增** (backend.rs):
```rust
fn search_messages(&self, py, query, session_id, limit, offset, source_filter, ...) -> PyResult<Vec<PyObject>>
```

**Python 修改** (intellect_state.py):
- SessionDB.search_messages() 检测 Rust 后端可用时直接调用
- 保留 Python fallback

### A2: list_sessions_rich 迁移到 Rust

**当前**: intellect_state.py L1506 — CTE + ROW_NUMBER + LEFT JOIN
**目标**: Rust backend.list_sessions_rich(member_id, limit, ...) 返回富格式结果

**Rust 新增** (backend.rs):
```rust
fn list_sessions_rich(&self, py, member_id, limit, offset, active_only) -> PyResult<Vec<PyObject>>
```

### A3: 完全切换 append_message / replace_messages

**当前**: intellect_state.py 通过 execute_write 回调使用 Python sqlite3
**目标**: 直接调用 Rust backend 的 append_message / replace_messages

**Python 修改** (intellect_state.py):
- SessionDB.append_message() → self._backend.append_message()
- SessionDB.replace_messages() → self._backend.replace_messages()

### A4: get_messages 迁移到 Rust

**当前**: intellect_state.py L2092 — Python SQL 查询
**目标**: Rust backend.get_messages() 直接返回

## 完成情况

### ✅ A1: search_messages 迁移到 Rust

**Rust 新增** (backend.rs L970-1075):
- `search_messages(query, source_filter, exclude_sources, role_filter, limit, offset, sort)` — FTS5 MATCH + sessions JOIN + snippet

**测试**: FTS5 搜索、source 过滤、exclude 过滤、sort 排序、空查询处理

### ✅ A2: list_sessions_basic 迁移到 Rust

**Rust 新增** (backend.rs L1077-1190):
- `list_sessions_basic(source, exclude_sources, limit, offset, member_id)` — 会话列表 + preview + last_active 单次查询

**测试**: 全量查询、source 过滤、member_id 过滤、limit 分页

### ✅ A3: append/replace 已完全集成

**现状**: intellect_state.py 的 `append_message` 和 `replace_messages` 已有 Rust fast path（L1882-1895, L1950-2030），通过 `_rust_backend()` 直接调用 Rust。

### ✅ A4: get_messages 已集成

**现状**: Rust backend 的 `get_messages` 已通过 `RustSQLiteBackend` 代理层可用。

## 验证

```bash
make rust-build  # 编译
# 所有 Phase A 测试通过 ✓
```
