# Team RBAC 权限实现计划

**日期：** 2026-06-01
**状态：** ✅ 已实现（commit `e83599133`）
**测试：** 18/18 通过（含 14 个新增测试）

---

## 一、现状分析

### 1.1 已有 Team 操作（`agent/teams.py:TeamDB`）

| 方法 | 功能 | 当前权限门控 |
|------|------|-------------|
| `create_team(slug, name, created_by)` | 创建团队 | ❌ 仅 `is_teams_enabled` flag |
| `get_team(id)` / `get_team_by_slug(slug)` | 查询团队 | ❌ 无 |
| `list_teams(member_id)` | 列出团队 | ❌ 无 |
| `archive_team(id)` | 归档团队（软删除） | ❌ 仅 `is_teams_enabled` flag |
| `add_team_member(team_id, member_id, role)` | 添加成员到团队 | ❌ 无 |
| `remove_team_member(team_id, member_id)` | 移除团队成员 | ❌ 无 |
| `get_team_members(team_id)` | 列出团队成员 | ❌ 无 |
| `get_member_role(team_id, member_id)` | 查询成员在团队中的角色 | ❌ 无 |

### 1.2 当前 Action 枚举

```python
class Action(Enum):
    CHAT = "chat"
    READ = "read"
    PROJECT_CREATE = "project:create"
    PROJECT_MANAGE = "project:manage"
    PROJECT_ARCHIVE = "project:archive"
    PROJECT_DELETE = "project:delete"
    MEMBER_INVITE = "member:invite"
    MEMBER_KICK = "member:kick"
    API_TOKEN_MANAGE = "api_token:manage"
    ADMIN = "admin"
```

**问题：** Team 操作完全不在此枚举中，`authorize()` 对 team 操作无约束力。

### 1.3 当前 ROLE_PERMISSIONS

现有 4 角色权限矩阵中，没有 `team:*` 开头的任何条目。

---

## 二、设计方案

### 2.1 新增 Action 枚举值

```python
class Action(Enum):
    # ... 现有值 ...

    # Team management (新增)
    TEAM_CREATE = "team:create"        # 创建团队
    TEAM_MANAGE = "team:manage"        # 修改团队名称/属性
    TEAM_ARCHIVE = "team:archive"      # 归档团队
    TEAM_DELETE = "team:delete"        # 删除团队

    # Team membership (新增)
    TEAM_MEMBER_ADD = "team:member:add"     # 添加成员
    TEAM_MEMBER_REMOVE = "team:member:remove"  # 移除成员
    TEAM_MEMBER_LIST = "team:member:list"     # 列出成员
```

### 2.2 更新 ROLE_PERMISSIONS

```python
ROLE_PERMISSIONS: dict[str, set[Action]] = {
    "owner": {
        # ... 现有 ...
        Action.TEAM_CREATE,
        Action.TEAM_MANAGE,
        Action.TEAM_ARCHIVE,
        Action.TEAM_DELETE,
        Action.TEAM_MEMBER_ADD,
        Action.TEAM_MEMBER_REMOVE,
        Action.TEAM_MEMBER_LIST,
    },
    "admin": {
        # ... 现有 ...
        Action.TEAM_CREATE,
        Action.TEAM_MANAGE,
        Action.TEAM_MEMBER_ADD,
        Action.TEAM_MEMBER_REMOVE,
        Action.TEAM_MEMBER_LIST,
    },
    "member": {
        # ... 现有 ...
        Action.TEAM_MEMBER_LIST,   # 普通成员可查看同队成员
    },
    "guest": {
        # ... 现有（仅 READ）...
        Action.TEAM_MEMBER_LIST,   # guest 可查看团队公开信息
    },
}
```

### 2.3 权限矩阵（完整）

| 权限 | owner | admin | member | guest |
|------|:-----:|:-----:|:------:|:-----:|
| **Team 生命周期** | | | | |
| `team:create` | ✅ | ✅ | | |
| `team:manage` | ✅ | ✅ | | |
| `team:archive` | ✅ | | | |
| `team:delete` | ✅ | | | |
| **Team 成员管理** | | | | |
| `team:member:add` | ✅ | ✅ | | |
| `team:member:remove` | ✅ | ✅ | | |
| `team:member:list` | ✅ | ✅ | ✅ | ✅ |
| **现有权限（不变）** | | | | |
| `chat` / `read` | ✅ | ✅ | ✅ | ✅ |
| `project:*` (3 项) | ✅ | ✅ | | |
| `member:*` (2 项) | ✅ | ✅ | | |
| `api_token:manage` | ✅ | ✅ | | |
| `admin` | ✅ | | | |

---

## 三、实施步骤

### Step 1: 扩展 Action 枚举

**文件：** `agent/membership.py`，行 52–76

```python
class Action(Enum):
    # 现有（不变）
    CHAT = "chat"
    READ = "read"
    PROJECT_CREATE = "project:create"
    PROJECT_MANAGE = "project:manage"
    PROJECT_ARCHIVE = "project:archive"
    PROJECT_DELETE = "project:delete"
    MEMBER_INVITE = "member:invite"
    MEMBER_KICK = "member:kick"
    API_TOKEN_MANAGE = "api_token:manage"
    ADMIN = "admin"
    
    # 新增 Team 权限
    TEAM_CREATE = "team:create"
    TEAM_MANAGE = "team:manage"
    TEAM_ARCHIVE = "team:archive"
    TEAM_DELETE = "team:delete"
    TEAM_MEMBER_ADD = "team:member:add"
    TEAM_MEMBER_REMOVE = "team:member:remove"
    TEAM_MEMBER_LIST = "team:member:list"
```

### Step 2: 更新 ROLE_PERMISSIONS

**文件：** `agent/membership.py`，行 80–108

在 4 个角色的 `set[Action]` 中添加对应权限。

### Step 3: 在 TeamDB 中集成 authorize() 调用

**文件：** `agent/teams.py`

| 方法 | 添加的授权检查 |
|------|---------------|
| `create_team()` | `authorize(actor_role, Action.TEAM_CREATE)` |
| `archive_team()` | `authorize(actor_role, Action.TEAM_ARCHIVE)` |
| `add_team_member()` | `authorize(actor_role, Action.TEAM_MEMBER_ADD)` 或检查 team_memberships.role == "admin" |
| `remove_team_member()` | `authorize(actor_role, Action.TEAM_MEMBER_REMOVE)` 或检查自己是 admin |
| `get_team_members()` | `authorize(actor_role, Action.TEAM_MEMBER_LIST)` |

**设计选择：** `team_memberships` 表中已有 `role` 字段（`member` / `admin`），因此 team 内部角色可以与全局 RBAC 角色叠加：

```
全局 owner/admin → 全部 team 操作允许
全局 member      → TEAM_MEMBER_LIST 允许，增删需要 team 内 admin 角色
全局 guest       → 仅 TEAM_MEMBER_LIST
```

对于 `TEAM_MEMBER_ADD` 和 `TEAM_MEMBER_REMOVE`，采用**双重门控**：
1. 全局 `authorize(role, action)` — 检查全局角色
2. 如果全局角色不足，检查 `get_member_role(team_id, actor_id)` — 检查 team 内是否为 admin

### Step 4: 更新 spec 文档

**文件：** `docs/plans/2026-05-31-profile-teams-members-projects-spec-v2.md`

在 §7 权限矩阵中增加 Team 操作列。

### Step 5: 测试

**文件：** 扩展 `tests/agent/test_e2e_members_teams_projects.py` 或新建测试

| 测试场景 | 预期 |
|----------|------|
| owner 创建团队 | ✅ |
| admin 添加成员 | ✅ |
| member 创建团队 | ❌ 拒绝 |
| member 移除成员 | ❌ 拒绝 |
| member 列出成员 | ✅ 允许 |
| guest 列出成员 | ✅ 允许 |
| guest 添加成员 | ❌ 拒绝 |
| team admin (team内) 添加成员 | ✅ 允许（双重门控通过） |
| team member (team内) 移除成员 | ❌ 拒绝 |

---

## 四、影响范围

| 文件 | 变更类型 | 行数估计 |
|------|----------|----------|
| `agent/membership.py` | 扩展 Action + ROLE_PERMISSIONS | +15 行 |
| `agent/teams.py` | 在 5 个方法中添加 authorize() 门控 | +25 行 |
| `docs/plans/...spec-v2.md` | 更新权限矩阵 | +10 行 |
| 测试文件 | 9 个新测试场景 | +50 行 |

**总计：约 100 行，4 个文件，影响范围可控。**

---

## 五、向后兼容性

- `authorize(None, action)` 返回 `False`，因此未启用 members 的系统不受影响
- `ROLE_PERMISSIONS` 是纯增量——4 个角色的现有权限不变
- `TeamDB` 的 `is_teams_enabled` flag 依然是第一道门控，关闭时跳过所有授权

---

## 六、风险

| 风险 | 缓解 |
|------|------|
| 现有 CLI 命令（`intellect teams *`）未传 role 到 TeamDB | CLI 命令层传 `actor_role="admin"` 作为过渡默认值，待 CLI 接入 member session 后再改为真实角色 |
| `add_team_member` 的双重门控逻辑复杂 | 先在 `authorize()` 层判断全局角色；全局通过直接放行，不通过再查 team 内角色 |

---

*待确认后按 Step 1–5 顺序执行。*
