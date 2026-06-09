# 代码审查整改与测试计划

**日期：** 2026-06-01
**基于：** `docs/plans/code-review-member-rbac-oauth.md`
**状态：** ✅ 已全部执行 + 第二轮修复（commits `162d2176f` + `9338eac55`，75 tests passed）

---

## 一、P1 修复 — 开发计划

### Fix 1: `register` 幽灵成员

**文件：** `intellect_cli/main.py:6480-6548`

**变更：** 将 invite 消费操作从 `redeem_invite`（立即创建 member）改为"先收集输入，再创建 member"：

```python
def _cmd_members_register(args, config):
    code = args.code.strip().upper()
    db = MembershipDB(config=config)

    # Phase 1: validate invite only (no member creation yet)
    row = db.conn.execute(
        "SELECT * FROM member_invites WHERE code = ?", (code,)
    ).fetchone()
    if not row:
        print(f"Error: Invalid invite code '{code}'.", file=sys.stderr)
        db.close()
        return 1
    invite = dict(row)
    if invite.get("accepted_by"):
        print("Error: This invite code has already been used.", file=sys.stderr)
        db.close()
        return 1
    if invite.get("expires_at") and time.time() > invite["expires_at"]:
        print("Error: This invite code has expired.", file=sys.stderr)
        db.close()
        return 1

    # Phase 2: collect all user input BEFORE creating member
    try:
        login = input("Login name: ").strip()
    except (EOFError, KeyboardInterrupt):
        print(); db.close(); return 1
    if not login or db.get_member_by_login(login):
        ...; db.close(); return 1

    pw1 = masked_secret_prompt("New password: ")
    pw2 = masked_secret_prompt("Confirm new password: ")
    if pw1 != pw2 or len(pw1) < 4:
        ...; db.close(); return 1

    # Phase 3: now create member atomically
    member_id = _register_member_atomic(db, code, login, pw1, invite.get("email"))
    _write_cli_session(...)
```

**测试：** 新增 `test_register_crash_before_login_input_does_not_consume_invite`

---

### Fix 2: 6 个 DB 方法添加 RBAC 门控

**文件：** `agent/membership.py`

为每个方法增加 `actor_role` 参数和 `authorize()` 调用：

| 方法 | Action |
|------|--------|
| `activate_member(member_id, actor_role=None)` | `Action.ADMIN` |
| `deactivate_member(member_id, actor_role=None)` | `Action.ADMIN` |
| `delete_member(member_id, actor_role=None)` | `Action.ADMIN` |
| `grant_owner(member_id, actor_role=None)` | `Action.ADMIN` |
| `create_invite(created_by, ..., actor_role=None)` | `Action.ADMIN` |
| `set_member_role(member_id, role, actor_role=None)` | `Action.ADMIN` |

**CLI 跟进：** 更新 `_cmd_members_activate/deactivate/delete/grant_owner/invite` 中的 DB 调用，传入 `actor_role=actor_role`（当前这些 handler 已有 CLI 层检查，但 DB 调用未传 actor_role）。

**测试：** 6 个新测试，验证 DB 方法直接调用被拒绝（`actor_role="member"` 时返回 False/None）。

---

## 二、P2 修复 — 开发计划

### Fix 3: `bind_identity` 防劫持

**文件：** `agent/membership.py:763-774`

**变更：** 在 `INSERT OR REPLACE` 前检查是否已绑定到不同成员：

```python
def bind_identity(self, member_id, provider, provider_id, ...):
    def _upsert(cursor):
        existing = cursor.execute(
            "SELECT member_id FROM identities WHERE provider=? AND provider_id=?",
            (provider, provider_id)
        ).fetchone()
        if existing and existing["member_id"] != member_id:
            # Log audit event
            logger.warning(
                "Identity %s:%s moved from member %s to %s",
                provider, provider_id, existing["member_id"], member_id
            )
        cursor.execute("INSERT OR REPLACE INTO identities (...)")
    self._execute_write(_upsert)
```

**变更理由：** 保留 INSERT OR REPLACE（允许 admin 重新绑定，如用户更换 GitHub 账号），但记录审计日志警告。

**测试：** `test_bind_identity_moves_with_warning` — 验证重新绑定会记录日志

---

### Fix 4: `delete_member` 磁盘清理

**文件：** `agent/membership.py:671-696`

**变更：** 在 `_delete` 函数末尾添加磁盘清理：

```python
def delete_member(self, member_id, actor_role=None):
    # ... existing cascade deletions ...
    def _delete(cursor):
        # ... existing DB cleanup ...
        pass

    self._execute_write(_delete)

    # Disk cleanup (best effort, after DB success)
    import shutil
    from intellect_constants import get_intellect_home
    member_dir = get_intellect_home() / "members" / member_id
    if member_dir.exists():
        shutil.rmtree(member_dir, ignore_errors=True)
    return True
```

**测试：** `test_delete_member_cleans_disk` — 创建 member → 确保目录存在 → delete → 断言目录不存在

---

### Fix 5: `logout` 不被步骤 4 覆盖

**文件：** `intellect_cli/main.py:6903-6920` + `agent/runtime_context.py:111-119`

**变更：** 登出时留下标记文件，步骤 4 检查此标记：

```python
# _cmd_members_logout
def _cmd_members_logout(args, config):
    session_file.unlink()
    # Create logout marker to prevent auto re-login
    logout_marker = get_intellect_home() / ".cli-logged-out"
    logout_marker.touch()
    print(f"Logged out '{login_name}'.")
```

```python
# resolve_member_id 步骤 4
if db:
    logout_marker = get_intellect_home() / ".cli-logged-out"
    if logout_marker.exists():
        return None, None  # user explicitly logged out
    rows = db._conn.execute(
        "SELECT id FROM members WHERE enabled = 1 LIMIT 2"
    ).fetchall()
    if len(rows) == 1:
        return rows[0]["id"], _resolve_role(rows[0]["id"])
```

```python
# 登录时清除登出标记
# _cmd_members_login 成功路径
logout_marker = get_intellect_home() / ".cli-logged-out"
if logout_marker.exists():
    logout_marker.unlink()
```

**测试：** `test_logout_prevents_auto_login` + `test_login_after_logout_works`

---

## 三、P3 改进 — 开发计划

### Fix 6: 弱密码警告

**文件：** `intellect_cli/main.py:_prompt_new_password`

**变更：** 在密码确认通过后添加强度检查（仅警告，不阻止）：

```python
if len(pw1) < 8:
    print("Consider a longer password (8+ characters recommended).")
if pw1.lower() == pw1 or pw1.upper() == pw1 or pw1.isdigit():
    print("Consider adding mixed case, numbers, or special characters.")
```

**测试：** `test_password_strength_warnings` — 验证弱密码会打印警告

---

### Fix 7: `authorize()` 审计日志

**文件：** `agent/membership.py:175-182`

**变更：** 拒绝时记录 DEBUG 日志：

```python
def authorize(role: str | None, action: Action) -> bool:
    if not role:
        return False
    allowed = ROLE_PERMISSIONS.get(role, set())
    result = action in allowed
    if not result:
        logger.debug("RBAC deny: role=%s action=%s", role, action.value)
    return result
```

**测试：** `test_authorize_logs_denial` — Mock logger 验证拒绝时调用

---

### Fix 8: Memory scoping 减少 IO

**文件：** `tools/memory_tool.py:55-77`

**变更：** `MemoryStore.__init__` 接收 `config` 参数，`load_from_disk` / `save_to_disk` 复用 `self._mem_dir`：

```python
class MemoryStore:
    def __init__(self, ..., config=None):
        self._config = config
        self._mem_dir = get_memory_dir()  # 提前计算

    def load_from_disk(self):
        mem_dir = self._mem_dir  # 复用，不重新调用 load_config()
```

**测试：** 无需新增（现有 memory 测试覆盖，性能改进通过 profiling 验证）

---

## 四、测试计划汇总

### 新增测试

| # | 测试 | 分类 | 优先级 |
|---|------|------|--------|
| 1 | `test_register_crash_does_not_consume_invite` → `TestRegisterNoGhostMember` (2 tests) | register 流程 | ✅ P1 |
| 2 | `test_activate_member_db_denied_for_non_owner` | DB 门控 | P1 |
| 3 | `test_deactivate_member_db_denied_for_non_owner` | DB 门控 | P1 |
| 4 | `test_delete_member_db_denied_for_non_owner` | DB 门控 | P1 |
| 5 | `test_grant_owner_db_denied_for_non_owner` | DB 门控 | P1 |
| 6 | `test_create_invite_db_denied_for_non_owner` | DB 门控 | P1 |
| 7 | `test_set_member_role_db_denied_for_non_owner` | DB 门控 | P1 |
| 8 | `test_bind_identity_move_logs_warning` | identity 劫持 | P2 |
| 9 | `test_delete_member_cleans_disk_directory` | 磁盘清理 | P2 |
| 10 | `test_logout_prevents_auto_login` | logout | P2 |
| 11 | `test_login_clears_logout_marker` | logout | P2 |
| 12 | `test_password_strength_warnings` | 密码强度 | P3 |
| 13 | `test_authorize_logs_denial` | 审计日志 | P3 |

**总计：13 个新测试**

### 回归测试

```
tests/agent/test_e2e_members_teams_projects.py (55 tests)
tests/intellect_cli/test_ontoweb_subscription.py (20 tests)
tests/intellect_cli/test_ontoweb_account.py (x tests)
tests/agent/test_ontoweb_rate_guard.py (x tests)
```

---

## 五、执行步骤

| Step | 内容 | 文件 | 时间 |
|------|------|------|------|
| **1** | ✅ Fix 1: register 幽灵成员 — 先收集输入再创建 member | `intellect_cli/main.py` | 1h |
| **2** | Fix 2: 6 个 DB 方法添加 actor_role 门控 | `agent/membership.py` | 1h |
| **3** | Fix 2: CLI handler 跟进传 actor_role | `intellect_cli/main.py` | 0.5h |
| **4** | Fix 3: bind_identity 审计日志 | `agent/membership.py` | 0.5h |
| **5** | Fix 4: delete_member 磁盘清理 | `agent/membership.py` | 0.5h |
| **6** | Fix 5: logout 标记防自动重新登录 | `main.py` + `runtime_context.py` | 0.5h |
| **7** | Fix 6: 弱密码警告 | `intellect_cli/main.py` | 0.3h |
| **8** | Fix 7: authorize() 审计日志 | `agent/membership.py` | 0.2h |
| **9** | Fix 8: Memory scoping 提前计算 mem_dir | `tools/memory_tool.py` | 0.2h |
| **10** | 13 个新测试 | 测试文件 | 2h |
| **11** | 全量回归 | — | 0.5h |
| **12** | 更新 spec 文档 | `docs/plans/` | 0.5h |

**总计：约 7.5 小时**

---

## 六、验证清单

- [ ] `register` 输入 login 前 Ctrl+C → 邀请码仍可用
- [ ] `activate/deactivate/delete/grant_owner` 在 API 路径调用 DB 方法时被拒绝（非 owner）
- [ ] `create_invite` DB 方法被非 owner 拒绝
- [ ] `bind_identity` 重新绑定到不同 member 时记录警告
- [ ] `delete_member` 后 `members/<id>/` 目录不存在
- [ ] `logout` 后单成员场景不自动重新登录
- [ ] `login` 清除 logout 标记，下次 login 正常工作
- [ ] 弱密码打印警告但不阻止
- [ ] `authorize()` 拒绝时记录 DEBUG 日志
- [ ] `MemoryStore` 不重复调用 `load_config()`
