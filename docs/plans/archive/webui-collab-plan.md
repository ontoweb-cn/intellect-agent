# Intellect-Agent × WebUI 协作 — Agent 端开发计划

**日期：** 2026-06-01
**基于：** `~/.claude/plans/validate-id-fix-plan.md`
**状态：** ✅ Phase 1 已完成（113 tests passed）

---

## 一、执行摘要

WebUI 注册流程存在 bug：用户输入的 slug（如 "alice"）被当作 member_id 存入 cookie，但 agent 内部 member_id 是 12 位 hex（如 "a1b2c3d4e5f6"）。`resolve_member_id()` 用 cookie 值查 `WHERE id = ?` → 查不到。

**修复方向：** member_id 由 agent 统一生成（hex），WebUI 不再丢弃 `create_member()` 返回值。

---

## 二、Agent 端需要完成的工作

### Phase 1.1 — 新增 `validate_member_id()` + `validate_team_id()`（0.5h）

**文件：** `agent/membership.py`

在模块顶部（`_new_id()` 之前）添加两个校验函数：

```python
import re

_ID_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')
_ID_MAX_LEN = 64
_RESERVED_IDS = frozenset({
    "default", "admin", "root", "system", "webui", "api",
    "template", "none", "null", "true", "false",
})

def validate_member_id(raw: str) -> str:
    """Validate a member_id string. Returns normalized id or raises ValueError."""
    ...

def validate_team_id(raw: str) -> str:
    """Validate a team_id string. Returns normalized id or raises ValueError."""
    ...
```

**测试：** `tests/agent/test_validate_ids.py` — 8 个有效用例 + 11 个无效用例

---

### Phase 1.2 — 修改 `create_member()` 支持 `member_id=` 参数 + CLI `add` 合并（0.8h）

**文件：** `agent/membership.py`，`MembershipDB.create_member()`

新增 `member_id: str | None = None` 参数：
- `None`（默认）：自动生成 hex id（`_new_id()`），向后兼容——CLI 路径不受影响
- 非 None：必须通过 `validate_member_id()` 校验，已存在则抛 `ValueError`

```python
def create_member(
    self,
    display_name: str,
    login_name: str | None = None,
    email: str | None = None,
    platform: str = "cli",
    member_id: str | None = None,       # NEW
    actor_role: str | None = None,
) -> str | None:
    ...
    if member_id is not None:
        validate_member_id(member_id)
        if self.get_member(member_id):
            raise ValueError(f"member_id {member_id!r} already exists")
    else:
        member_id = self._new_id()
    ...
```

**文件：** `intellect_cli/main.py`，`_cmd_members_add`

新增 `--id` 参数，允许 owner 显式指定 member_id（slug 格式，经 `validate_member_id` 校验）：

```bash
# 默认：自动生成 hex id
intellect members add alice --name "Alice"

# 显式指定 id（如 WebUI 集成或迁移场景）
intellect members add alice --id alice-slug --name "Alice"
```

parser 变更：
```python
_mem_add.add_argument("--id", default=None, help="Custom member ID (slug format, owner only)")
```

handler 变更：
```python
mid = db.create_member(
    display_name=...,
    login_name=args.login,
    email=...,
    platform="cli",
    member_id=getattr(args, "id", None),  # NEW: 传递 CLI --id
)
```

**设计决策：** `--id` 与 `login` 是独立参数。`login` 始终映射到 `login_name`（用于密码登录），`--id` 映射到 `member_id`（用于外部系统引用）。不传 `--id` 时行为与当前完全一致。

**测试：** 
- `create_member(member_id="my-slug")` → 成功，id="my-slug"
- `create_member(member_id="admin")` → ValueError（保留字）
- `create_member(member_id="a/b")` → ValueError（非法字符）
- `create_member(member_id=None)` → 自动生成 hex（向后兼容）

---

### Phase 1.3 — 新建 `intellect_cli/members_http.py`（0.3h）

**文件：** `intellect_cli/members_http.py`（新文件）

```python
"""HTTP cookie names shared between WebUI and CLI dashboard."""

def member_cookie_name() -> str:
    return "intellect_member"

def team_cookie_name() -> str:
    return "intellect_team"
```

**测试：** 单元测试验证 cookie 名称一致性

---

### Phase 1.4 — 新建 `agent/member_session.py`（0.7h）

**文件：** `agent/member_session.py`（新文件）

Server-side session 管理（JSON 文件存储）：
- `create_member_session(member_id, ttl_hours=168)` → `token`
- `resolve_member_session(token)` → `dict | None`
- `delete_member_session(token)` → `None`

存储位置：`{INTELLECT_HOME}/.member-sessions`（JSON，原子写入）

**测试：** 4 个测试（创建/解析/过期/删除）

---

### Phase 1.5 — 确认缺失模块状态（0.2h）

| 模块 | 状态 | 处理 |
|------|------|------|
| `agent/member_credentials.py` | ❌ 不存在 | WebUI 端移除引用（agent 端不需要） |
| `agent/members_team.py` | ❌ 不存在 | WebUI 端移除引用（agent 端不需要） |
| `intellect_cli/members_http.py` | ❌ 不存在 | Phase 1.3 新建 |
| `agent/member_session.py` | ❌ 不存在 | Phase 1.4 新建 |

---

## 三、测试计划

| # | 测试文件 | 测试内容 | 数量 |
|---|----------|----------|------|
| 1 | `tests/agent/test_validate_ids.py`（新） | `validate_member_id` 有效/无效/保留字/边界 | 19 |
| 2 | `tests/agent/test_validate_ids.py`（新） | `validate_team_id` 有效/无效 | 8 |
| 3 | `tests/agent/test_e2e_members_teams_projects.py` | `create_member(member_id="my-slug")` / `member_id=None` / 保留字拒绝 / 非法字符拒绝 | 4 |
| 4 | `tests/agent/test_member_session.py`（新） | create/resolve/expired/delete | 4 |
| 5 | `tests/intellect_cli/test_members_http.py`（新） | cookie 名称一致性 | 2 |

**总计：37 个新测试**（含 validate 19 + team 8 + create_member 4 + session 4 + http 2）

---

## 四、实施步骤

| Step | 内容 | 时间 |
|------|------|------|
| **1.1** | 新增 `validate_member_id()` + `validate_team_id()` | 0.5h |
| **1.2** | 修改 `create_member()` 支持 `member_id=` | 0.5h |
| **1.3** | 新建 `intellect_cli/members_http.py` | 0.3h |
| **1.4** | 新建 `agent/member_session.py` | 0.7h |
| **1.5** | 确认缺失模块 | 0.2h |
| **Test** | 37 个新测试 | 1.5h |
| **Doc** | 更新 spec + review | 0.3h |
| **合计** | | **4h** |
