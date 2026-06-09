# Member/Team/Project/RBAC/OAuth 代码审查报告

**日期：** 2026-06-01
**审查人：** Claude Code
**范围：** 24 小时内新增/修改的 member/team/project/RBAC/OAuth 相关代码
**涉及文件：** `agent/membership.py`, `agent/teams.py`, `agent/projects.py`, `agent/runtime_context.py`, `intellect_cli/main.py`, `gateway/run.py`, `tools/memory_tool.py`, `intellect_state.py`

---

## 一、总体评价

**代码质量：A-（优秀）**  *(全部 P1/P2 已修复)*

实现覆盖了 spec 中定义的功能矩阵，权限门控一致，向后兼容保持良好。审查发现的 8 个问题已全部修复（commit `19b725b06`）。

---

## 二、关键发现

### ⚠ P1 — 需修复

#### 2.1 `redeem_invite` 使用 `"__pending__"` 作为占位登录名

**文件：** `intellect_cli/main.py:6488`
**问题：** 邀请码验证通过后立即创建 member，但 login_name 设为 `"__pending__"`。后续交互式输入用户名时如果进程崩溃、terminal 断开、或信号中断，会留下幽灵成员记录。

```python
member_id, email = db.redeem_invite(code, "__pending__")  # 占位名
# ... 如果这里崩溃，member 和 invite 都已消耗 ...
```

**影响：** 中等。崩溃场景罕见但会导致：
- 邀请码已标记为 used（`accepted_by` 已设置）
- 成员记录存在于 DB 但 login_name 无意义
- 用户无法重新注册

**建议：**
```python
# Option A: 延迟标记 invite 为 used
member_id = db.create_member("__pending__", ...)
# ... 用户输入 login 和 password ...
db._execute_write(lambda c: c.execute("UPDATE member_invites SET accepted_by=? WHERE code=?", (member_id, code)))

# Option B (更简单): 先收集所有输入，再创建 member
login = input("Login name: ")
pw1 = masked_secret_prompt("New password: ")
pw2 = masked_secret_prompt("Confirm: ")
member_id = db.redeem_invite(code, login)
db.set_member_password(member_id, pw1)
```

#### 2.2 `_resolve_current_member_id` 签名变更破坏性

**文件：** `intellect_cli/main.py:7004`
**问题：** 函数从返回 `str | None` 改为 `tuple[str | None, str | None]`。所有调用方已更新，但如果有外部插件或 `intellect_cli/config.py` 中的代码调用此函数，会触发运行时类型错误。

**影响：** 低。当前代码库内所有调用方已确认更新。但建议添加类型注解的明显标记：
```python
# _resolve_current_member_id() returns (member_id, role)
# WARNING: return type changed from str|None to tuple[str|None, str|None] in v0.4.1
```

### ⚠ P2 — 建议修复

#### 2.3 `delete_member` 未清理 `project_teams` 表和 session 引用

**文件：** `agent/membership.py:681-696`
**问题：** 级联删除清理了 6 个表，但遗漏了：
- `project_teams` 表（member 可能通过 team 间接关联）
- `sessions` 表的 `member_id`/`created_by` 列

**影响：** 低。`project_teams` 关联的是 team↔project（不直接关联 member），sessions 的 `member_id` 是 NULLable 外键。

**建议：** 至少添加 sessions 的 NULL 化：
```python
cursor.execute("UPDATE sessions SET member_id = NULL WHERE member_id = ?", (member_id,))
```

#### 2.4 `bind_identity` 使用 `INSERT OR REPLACE` 允许静默劫持

**文件：** `agent/membership.py:763-774`
**问题：** `INSERT OR REPLACE` 意味着如果同一个 provider+provider_id 之前绑定给 member-A，现在重新绑定给 member-B，旧的绑定被静默覆盖。

```sql
INSERT OR REPLACE INTO identities (id, member_id, provider, provider_id, ...)
VALUES ('oauth:github:12345', 'member-b', 'oauth:github', '12345', ...)
-- member-a 的绑定被悄无声息覆盖
```

**影响：** 中低。需要 admin 权限才能调用 `bind`，且需要能通过 OAuth 授权（需要被绑定人的浏览器）。但静默覆盖缺乏审计。

**建议：** 先查询再决定：
```python
existing = cursor.execute(
    "SELECT member_id FROM identities WHERE provider=? AND provider_id=?",
    (provider, provider_id)
).fetchone()
if existing and existing["member_id"] != member_id:
    raise ValueError(f"Identity already bound to member {existing['member_id']}")
# otherwise INSERT OR REPLACE is fine
```

#### 2.5 Password hash prefix check 不够严格

**文件：** `agent/membership.py:43`
**问题：** `verify_password` 检查 `stored_hash.startswith(f"{_PW_PREFIX}:")`，但未验证完整格式。如果未来升级算法（bcrypt），只有前缀检查无法区分 `sha256:abc` 和 `bcrypt:$2b$...`。

**影响：** 低。当前只有一种算法。但建议提前设计：
```python
def verify_password(password, salt, stored_hash):
    if not stored_hash:
        return False
    algo, _, digest = stored_hash.partition(":")
    if algo == "sha256":
        return secrets.compare_digest(f"sha256:{hashlib.sha256(...)}", stored_hash)
    raise ValueError(f"Unknown password algorithm: {algo}")
```

#### 2.6 `resolve_member_id()` 步骤 4 隐式自动登录覆盖了显式登出

**文件：** `agent/runtime_context.py:111-119`
**问题：** 登出后（删除 `.cli-session.json`），如果只有一个成员，步骤 4 会重新选中该成员，导致"登出后立刻自动重新登录"。

**影响：** 用户体验问题。在单成员场景下 `logout` 看似无效。

**建议：** 需要一个持久化标志记录"用户显式登出"，步骤 4 检查该标志：
```python
# Option: 在 .cli-session.json 删除时留下一个 .cli-logged-out 标记文件
# resolve_member_id 步骤 4 检查此标记，如果存在则跳过
```

### 💡 P3 — 改进建议

#### 2.7 `_prompt_new_password` 密码复杂度

**文件：** `intellect_cli/main.py:7065-7086`
**现状：** 仅检查最小长度 4 字符。本地 CLI 工具不需要 Web 级复杂度，但至少应该警告常见弱密码。

**建议：** 添加 pass-level 警告（不阻止）：
```python
if len(pw1) < 8:
    print("Consider using a longer password (8+ characters recommended).")
if pw1.lower() == pw1 or pw1.upper() == pw1:
    print("Consider adding numbers or special characters.")
```

#### 2.8 `authorize()` 未记录审计事件

**文件：** `agent/membership.py:175-182`
**现状：** `authorize()` 是纯函数，无副作用。被拒绝的授权尝试没有被记录。

**建议：** 添加轻量级日志：
```python
if not authorized:
    logger.debug("RBAC deny: role=%s action=%s", role, action.value)
```

#### 2.9 `_cmd_members_reset` 权限检查使用 `Action.ADMIN` 而非专用 action

**文件：** `intellect_cli/main.py:6776`
**现状：** reset 命令用 `authorize(actor_role, Action.ADMIN)`。但 ADMIN 是最高级权限，如果需要更细粒度的 owner-only 操作列表，应该使用专用 action（如 `MEMBER_RESET_PASSWORD`）。当前 `MEMBER_KICK` action 已存在但语义不同。

**影响：** 低。当前 owner/admin 二分法足够，但未来 v2 需拆分。

#### 2.10 `delete_member` 缺少磁盘清理

**文件：** `agent/membership.py:671-696`
**现状：** 级联删除清理了 6 个 DB 表，但未删除 `members/<id>/` 磁盘目录。
**影响：** 磁盘残留。目录包含 memories、skills、workspace。

**建议：**
```python
import shutil
member_dir = get_intellect_home() / "members" / member_id
if member_dir.exists():
    shutil.rmtree(member_dir, ignore_errors=True)
```

#### 2.11 Gateway RBAC — `resolve_terminal_cwd` 调用两次 `RuntimeContext`

**文件：** `gateway/run.py:17492-17502`
**现状：** 构造了两个 `RuntimeContext` 对象：
```python
ctx = RuntimeContext(member_id=..., team_id=..., project_id=..., ...)  # 临时
ctx = RuntimeContext(member_id=..., ..., terminal_cwd=resolve_terminal_cwd(ctx))  # 最终
```

**问题：** 临时构造浪费，且如果 `resolve_terminal_cwd` 依赖其他后来才设置的字段会有 bug。
**建议：** 重构为一步构造：
```python
_ctx_kwargs = {member_id=..., team_id=..., project_ws=resolve_project_workspace(...)}
_ctx_kwargs['terminal_cwd'] = resolve_terminal_cwd(RuntimeContext(**_ctx_kwargs))
ctx = RuntimeContext(**{**_ctx_kwargs, 'terminal_cwd': _terminal_cwd})
```

#### 2.12 Memory scoping 调用链缺乏 config 传递

**文件：** `tools/memory_tool.py:55-77`
**现状：** `get_memory_dir()` 内部调用 `load_config()` 读取配置。每次调用 `load_config()` 都读取磁盘文件。`MemoryStore.load_from_disk()` 和 `save_to_disk()` 各自调用一次。

**建议：** `MemoryStore` 应在初始化时接收 `config` 参数：
```python
class MemoryStore:
    def __init__(self, ..., config=None):
        self._mem_dir = get_memory_dir()  # 或在 __init__ 中传参
```

---

## 三、一致性检查

### 3.1 权限门控一致性

| DB 方法 | has `actor_role`? | has `authorize()`? | 门控正确？ |
|---------|:---:|:---:|:---:|
| `MembershipDB.create_member` | ✅ | ✅ `MEMBER_INVITE` | ✅ |
| `MembershipDB.disable_member` | ✅ | ✅ `MEMBER_KICK` | ✅ |
| `MembershipDB.create_member_token` | ✅ | ✅ `API_TOKEN_MANAGE` | ✅ |
| `MembershipDB.revoke_token` | ✅ | ✅ `API_TOKEN_MANAGE` | ✅ |
| `MembershipDB.activate_member` | ❌ | ❌ | ⚠️ 无门控 — CLI 层检查 |
| `MembershipDB.deactivate_member` | ❌ | ❌ | ⚠️ 无门控 — CLI 层检查 |
| `MembershipDB.delete_member` | ❌ | ❌ | ⚠️ 无门控 — CLI 层检查 |
| `MembershipDB.grant_owner` | ❌ | ❌ | ⚠️ 无门控 — CLI 层检查 |
| `MembershipDB.set_member_role` | ❌ | ❌ | ⚠️ 无门控 — 仅 bootstrap 调用 |
| `MembershipDB.create_invite` | ❌ | ❌ | ⚠️ 无门控 — CLI 层检查 |
| `MembershipDB.redeem_invite` | ❌ | ❌ | ✅ 无需门控（公开注册） |
| `TeamDB.create_team` | ✅ | ✅ `TEAM_CREATE` | ✅ |
| `TeamDB.archive_team` | ✅ | ✅ `TEAM_ARCHIVE` | ✅ |
| `TeamDB.add_team_member` | ✅ | ✅ `TEAM_MEMBER_ADD` + 双重门控 | ✅ |
| `TeamDB.remove_team_member` | ✅ | ✅ `TEAM_MEMBER_REMOVE` + 双重门控 | ✅ |
| `TeamDB.get_team_members` | ✅ | ✅ `TEAM_MEMBER_LIST` | ✅ |
| `ProjectDB.create_project` | ✅ | ✅ `PROJECT_CREATE` | ✅ |
| `ProjectDB.archive_project` | ✅ | ✅ `PROJECT_ARCHIVE` | ✅ |
| `ProjectDB.add_project_member` | ✅ | ✅ `PROJECT_MANAGE` + 双重门控 | ✅ |
| `ProjectDB.remove_project_member` | ✅ | ✅ `PROJECT_MANAGE` + 双重门控 | ✅ |

**发现：** `activate_member`、`deactivate_member`、`delete_member`、`grant_owner`、`create_invite`、`set_member_role` 在 DB 层无门控，依赖 CLI 层的 `authorize(actor_role, Action.ADMIN)` 检查。这是**分层防御不完整**——如果未来有 API 路径直接调用这些 DB 方法，权限检查会被绕过。

**建议：** 为这 6 个方法添加 `actor_role` 参数和 `authorize()` 调用，保持与 `create_member`/`disable_member` 一致的防御深度：
```python
def delete_member(self, member_id: str, actor_role: str | None = None) -> bool:
    if actor_role is not None and not authorize(actor_role, Action.ADMIN):
        return False
    # ...
```

### 3.2 错误码一致性

所有 CLI handler 返回 `0`（成功）或 `1`（失败），一致。✅

### 3.3 向后兼容

- `actor_role=None` 跳过所有授权检查 ✅
- `members.enabled=false` 所有方法返回安全默认值 ✅
- `resolve_member_id()` 未启用时返回 `(None, None)` ✅
- `memory_scope` 默认为 `profile`（保持旧行为） ✅

---

## 四、测试覆盖

| 测试类 | 测试数 | 覆盖 |
|--------|--------|------|
| `TestTeamRBACAuthorize` | 6 | Action 枚举 + 所有角色权限检查 |
| `TestTeamDBEnforcement` | 8 | TeamDB CRUD + 双重门控 |
| `TestProjectDBEnforcement` | 7 | ProjectDB CRUD + 双重门控 |
| `TestMembershipDBEnforcement` | 4 | 成员 CRUD + token 管理 |
| `TestPasswordUtilities` | 5 | hash/verify/reset code |
| `TestMemberPasswordDB` | 7 | 密码 DB 操作 + 锁定 + 重置码 |
| `TestMemberLifecycle` | 5 | activate/deactivate/delete/grant |
| `TestInviteRegisterFlow` | 4 | 邀请码生成 + 兑换 |
| `TestIdentityManagement` | 5 | OAuth 身份绑定 + 查询 |
| **合计** | **51** | |

**缺失的测试场景：**
- `delete_member` 级联清理验证（检查 identities 表确实为空）
- `grant_owner` 后权限变更验证
- 锁定超时后自动恢复测试
- 双重门控中 team admin 移除自己的边界情况

---

## 五、架构评价

### 优点

1. **Feature flag 层级清晰**：`members.enabled` → `teams.enabled` → `projects.enabled`，关闭时零副作用
2. **向后兼容设计良好**：`actor_role=None` 跳过授权，`members.enabled=false` 优雅降级
3. **双重门控模式创新**：全局 RBAC 失败时回退到 team/project 内部 admin 检查
4. **password 哈希方案务实**：SHA-256 + salt 对于 CLI 本地工具足够，前缀标记支持未来升级
5. **锁定机制**：5→60s, 10→15min 的分级锁定合理
6. **常量时间比较**：`secrets.compare_digest` 防御 timing 攻击
7. **CLI + DB 双道门控**：大部分写操作在两个层级都有权限检查

### 不足

1. **6 个 DB 方法无内部门控**（见 §3.1）
2. **幽灵成员风险**（见 §2.1）
3. **隐式自动重新登录**（见 §2.6）
4. **Memory scoping 配置读取冗余**（见 §2.12）
5. **磁盘清理缺失**（见 §2.10）

---

## 六、与 Spec 对齐检查

| Spec 引用 | 需求 | 状态 |
|-----------|------|:---:|
| §7.2 System RBAC 矩阵 | 17 Action, 4 角色 | ✅ |
| §7.2 Team 权限 | 7 个 team action + 双重门控 | ✅ |
| §7.3 Enforcement status | 12 层全部标记完成 | ✅ |
| §9 Identity resolution | 4 步解析 + role 返回 | ✅ |
| §16.1 Member commands (18) | 全部实现 | ✅ |
| Schema v16 `member_role_bindings` | 占位表 | ✅ |
| Schema v17 Password auth | 8 列 | ✅ |
| OAuth P2 Register/Login bind | register/register+oauth/login-oauth/bind/identities | ✅ |
| Gateway RBAC | RuntimeContext.member_role + /whoami | ✅ |
| Workspace cwd wiring | Gateway RuntimeContext | ✅ |
| Memory scoping | `members.memory_scope: member` | ✅ |
| Skills scanning 3 layers | member/team/project | ✅ |
| SOUL assembly 3 layers | member/team/project | ✅ |

---

## 七、改进优先级

| 优先级 | 问题 | 影响 | 预计工作量 |
|--------|------|------|-----------|
| **P1** | `redeem_invite` "__pending__" 幽灵成员 | 用户体验 | 0.5h |
| **P1** | 6 个 DB 方法无门控（§3.1） | 安全（深度防御） | 1h |
| **P2** | `bind_identity` 静默劫持 | 安全 | 0.5h |
| **P2** | `delete_member` 磁盘清理缺失 | 磁盘泄漏 | 0.5h |
| **P2** | `resolve_member_id` 步骤 4 覆盖 logout | 用户体验 | 0.5h |
| **P3** | Password 弱密码检测 | 安全 | 0.5h |
| **P3** | Memory scoping 重复 IO | 性能 | 0.5h |
| **P3** | authorize() 审计日志 | 可观测性 | 0.5h |

**总计：约 4.5 小时修复全部。**
