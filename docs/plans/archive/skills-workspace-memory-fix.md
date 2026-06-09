# Skills / Workspace / Memory 补全方案

**日期：** 2026-06-01
**状态：** ✅ 已实现（commit `1f10d49f0`）

---

## 一、Workspace 接入（2 步）

### 问题

`resolve_terminal_cwd()` 实现了正确逻辑但从未被调用。

### 修复

**Step W1** — Gateway agent 创建时填充 `terminal_cwd`

`gateway/run.py:17476` 附近，已有 `RuntimeContext(...)` 构造：

```python
# 新增
from agent.runtime_context import resolve_terminal_cwd
_terminal_cwd = resolve_terminal_cwd(ctx, config=_cfg)

ctx = RuntimeContext(
    member_id=_member_id,
    member_role=_member_role,
    team_id=_sticky_team,
    project_id=_sticky_project,
    terminal_cwd=_terminal_cwd,    # ← 新增
    ...
)
```

`resolve_terminal_cwd` 需要 `RuntimeContext` 中的 member_id/team_id/project_id 来决定工作目录。但当前是先构造 ctx 再调用函数——需要调整调用顺序。

**Step W2** — CLI agent 创建时填充 `terminal_cwd`

`cli.py` 中 agent 构造路径同样需要填充。

**涉及文件**: `gateway/run.py`, `cli.py`, `agent/runtime_context.py`

---

## 二、Memory 隔离（3 步）

### 问题

所有成员的 memory 存储在同一个 `~/.intellect/memories/` 目录。

### 配置

```yaml
members:
  enabled: true
  memory_scope: member   # 'profile' (default) | 'member'
```

- `profile`（默认）：所有成员共享 `~/.intellect/memories/`
- `member`：每个成员独享 `members/<id>/memories/`

### 修复

**Step M1** — `memory_tool.py` 支持 member scope

```python
def get_memory_dir(config=None) -> Path:
    """Return the memory directory, scoped per config.members.memory_scope."""
    home = get_intellect_home()
    scope = "profile"
    if config and isinstance(config, dict):
        scope = config.get("members", {}).get("memory_scope", "profile")
    
    if scope == "member":
        # Resolve current member
        member_id = _resolve_current_member_id_from_config(config)
        if member_id:
            return home / "members" / member_id / "memories"
    return home / "memories"
```

**Step M2** — `memory_manager.py` / `memory_provider.py` 适配

MemoryManager 在 load/save 时使用 `get_memory_dir(config)` 替代硬编码路径。

**Step M3** — 配置默认值 + doctor 检查

`DEFAULT_CONFIG` 中新增 `members.memory_scope: profile`。

**涉及文件**: `tools/memory_tool.py`, `agent/memory_manager.py`, `intellect_cli/config.py`

---

## 三、实施步骤

| Step | 内容 | 文件 | 预计 |
|------|------|------|------|
| **W1** | Gateway RuntimeContext 填充 terminal_cwd | `gateway/run.py` | 0.5h |
| **W2** | CLI RuntimeContext 填充 terminal_cwd | `cli.py` | 0.5h |
| **M1** | memory_tool.get_memory_dir() 支持 member scope | `tools/memory_tool.py` | 1h |
| **M2** | memory_manager 适配 per-member 路径 | `agent/memory_manager.py` | 0.5h |
| **M3** | 配置默认值 + doctor | `intellect_cli/config.py` | 0.5h |
| **M4** | 测试 | 测试文件 | 1h |

**总计：约 4 小时。**
