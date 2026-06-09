# Member 密码认证实现方案

**日期：** 2026-06-01
**状态：** 待讨论
**替代：** `docs/plans/member-login-logout-design.md`

---

## 一、需求概述

| # | 功能 | 角色 | 说明 |
|---|------|------|------|
| 1 | Login | 所有成员 | 用户名 + 密码验证，成功写入 session |
| 2 | Logout | 已登录成员 | 清除 session，回到未登录状态 |
| 3 | 修改密码 | 自身 | 验证旧密码 + 新密码（两次确认）|
| 4 | Reset 密码 | owner | 生成验证码，成员下次 login 时强制修改 |

**交互协议：**

### 登录流程
```
$ intellect members login alice
Password: ********        ← 掩码输入
✓ Logged in as 'alice' (Alice).
```

### 密码错误
```
$ intellect members login alice
Password: ********
✗ Invalid password.
```

### 修改密码
```
$ intellect members passwd
Current password: ********
New password: ********
Confirm new password: ********
✓ Password changed.
```

### Owner Reset 密码
```
$ intellect members reset alice
✓ Reset code: IM-RC-ABCD1234
  Give this code to alice. It expires in 24 hours.
```

### 被 Reset 的成员登录
```
$ intellect members login alice
⚠ Your password has been reset by an administrator.
  Enter reset code: IM-RC-ABCD1234
  New password: ********
  Confirm new password: ********
✓ Password updated. Logged in as 'alice' (Alice).
```

---

## 二、数据模型

### 2.1 `members` 表新增字段

```sql
-- Schema v17: Add password authentication columns
ALTER TABLE members ADD COLUMN password_hash TEXT;          -- SHA-256( salt + password )
ALTER TABLE members ADD COLUMN password_salt TEXT;          -- random 32-char hex
ALTER TABLE members ADD COLUMN password_reset_code TEXT;    -- IM-RC-XXXXXXXX (NULL = none)
ALTER TABLE members ADD COLUMN password_reset_expiry REAL;  -- unix timestamp
ALTER TABLE members ADD COLUMN password_set_at REAL;        -- timestamp of last change
```

### 2.2 密码哈希方案

```
salt = secrets.token_hex(16)           # 128-bit random
hash = SHA-256(salt + ":" + password)  # salt:password → hex digest
```

存储：
```
password_hash = "sha256:<hex>"          # 带前缀，便于未来算法升级
password_salt = "<32-char-hex>"
```

**选择 SHA-256 的理由：**
- 与 `member_api_tokens.token_hash` 一致
- Python stdlib `hashlib` 直接可用，零依赖
- 对本地 CLI 工具足够（非 Web 服务，无暴力破解放大风险）
- 前缀标记支持未来升级到 bcrypt/argon2

---

## 三、CLI 命令设计

### 3.1 解析器扩展

```python
# ── members passwd ──
members_subparsers.add_parser("passwd", help="Change your password")

# ── members reset ──
_mem_reset = members_subparsers.add_parser("reset", help="Reset a member's password (owner only)")
_mem_reset.add_argument("login", help="Member login name")
```

### 3.2 Handler

#### `_cmd_members_login(args, config)` — 增强

```
现有: 直接写入 session（无密码验证）

增强:
  1. 查 member by login_name
  2. 如果 password_reset_code 存在且未过期:
     a. 提示 "password has been reset"
     b. 要求输入 reset_code
     c. 验证匹配
     d. 要求输入新密码 ×2
     e. 更新 password_hash, password_salt
     f. 清除 password_reset_code, password_reset_expiry
     g. 写入 session，完成
  
  3. 如果 password_hash 为 NULL (首次登录/未设密码):
     a. 提示 "No password set. Create one now."
     b. 输入新密码 ×2
     c. 保存 hash + salt
     d. 写入 session
  
  4. 正常密码验证:
     a. masked_secret_prompt("Password: ")
     b. SHA-256(salt + ":" + input) 与存储比对
     c. 匹配 → 写入 session
     d. 不匹配 → "Invalid password."
```

#### `_cmd_members_logout(args, config)` — 新增

```
  1. 读取 .cli-session.json
  2. 不存在 → "Not logged in."
  3. 删除文件
  4. 输出 "Logged out '<login>'."
```

#### `_cmd_members_passwd(args, config)` — 新增

```
  1. 读取当前 member (resolve_member_id)
  2. 未登录 → "Not logged in."
  3. 输入 current password
  4. 验证当前密码
  5. 输入 new password
  6. 输入 confirm new password
  7. new != confirm → "Passwords do not match."
  8. 生成新 salt, 计算新 hash
  9. UPDATE members SET password_hash=?, password_salt=?, password_set_at=?
  10. 输出 "Password changed."
```

#### `_cmd_members_reset(args, config)` — 新增

```
  1. 检查 actor 是否为 owner (authorize)
  2. 查 member by login
  3. 生成 reset code: "IM-RC-" + secrets.token_hex(4).upper()
  4. 设置 password_reset_code, password_reset_expiry = now + 24h
  5. UPDATE members
  6. 输出 "Reset code: IM-RC-ABCD1234 (expires in 24h)"
```

---

## 四、密码输入安全

### 4.1 输入方式

使用已有的 `intellect_cli/secret_prompt.py:masked_secret_prompt()`：
- POSIX: `termios.tty.setraw()` 逐字符 `*` 回显
- Fallback: `getpass.getpass()` 完全无回显
- 转义序列过滤

### 4.2 密码强度

不强制复杂度要求（本地工具，非 Web 服务）。可选：
- 最小长度 4 字符
- 警告弱密码（纯数字/纯字母/与 login 相同）

### 4.3 防暴力破解

```
连续失败 5 次 → 锁定 60 秒
连续失败 10 次 → 锁定 15 分钟
```

通过 `members` 表字段：
```sql
ALTER TABLE members ADD COLUMN failed_login_count INTEGER DEFAULT 0;
ALTER TABLE members ADD COLUMN locked_until REAL;  -- NULL = not locked
```

---

## 五、Session 管理

### 5.1 Session 文件格式

```json
{
  "member_id": "a1b2c3d4e5f6",
  "login_name": "alice",
  "display_name": "Alice",
  "device_id": "cli-a1b2c3d4",
  "login_at": 1717257600.0
}
```

### 5.2 Session 生命周期

```
login → 写入 .cli-session.json（永不过期）
logout → 删除 .cli-session.json
passwd → 不影响 session（已登录状态持续）
reset → 不影响 session（重置的是他人的密码）
```

### 5.3 Gateway Session

Gateway 侧不在本次范围——Gateway 使用 `state_meta` 存储 sticky member_id，与 CLI session 文件独立。密码验证在 Gateway 场景下通过 API token 完成，不涉及交互式密码输入。

---

## 六、Owner 角色实现

### 6.1 首个成员自动为 Owner

```python
# bootstrap 中
existing = db.conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
if existing == 0:
    role = "owner"
else:
    role = "admin"
```

### 6.2 role 字段

```sql
-- Schema v17 新增
ALTER TABLE members ADD COLUMN role TEXT DEFAULT 'member';
```

取值：`owner` | `admin` | `member`

### 6.3 authorize() 改造

```python
def resolve_member_id(...) -> tuple[str | None, str | None]:
    """Returns (member_id, role) or (None, None)."""
    # ... 现有逻辑 ...
    if member_id and db:
        row = db.conn.execute(
            "SELECT role FROM members WHERE id = ?", (member_id,)
        ).fetchone()
        role = row["role"] if row else None
    return member_id, role
```

CLI handler 中使用：
```python
member_id, role = resolve_member_id(...)
actor_role = role or "admin"  # fallback for backward compat
db.create_member(..., actor_role=actor_role)
```

---

## 七、实现步骤

### Phase 1: Schema + 密码基础设施（2h）

| Step | 内容 | 文件 |
|------|------|------|
| 1.1 | `members` 表加字段：password_hash, password_salt, password_reset_code, password_reset_expiry, password_set_at, failed_login_count, locked_until, role | `intellect_state.py` |
| 1.2 | SCHEMA_VERSION 16→17，v17 migration | `intellect_state.py` |
| 1.3 | 密码工具函数：`hash_password(pw, salt)`, `verify_password(pw, salt, hash)`, `generate_reset_code()` | `agent/membership.py` |

### Phase 2: Login/Logout（3h）

| Step | 内容 | 文件 |
|------|------|------|
| 2.1 | `_cmd_members_login` 增加密码验证逻辑 | `intellect_cli/main.py` |
| 2.2 | `_cmd_members_logout` 实现 | `intellect_cli/main.py` |
| 2.3 | 首次登录（无密码）自动提示设置密码 | `intellect_cli/main.py` |
| 2.4 | 暴力破解锁定（5→60s, 10→15min） | `agent/membership.py` |
| 2.5 | 锁定期间友好提示 | `intellect_cli/main.py` |

### Phase 3: Passwd/Reset（2h）

| Step | 内容 | 文件 |
|------|------|------|
| 3.1 | `_cmd_members_passwd` 实现 | `intellect_cli/main.py` |
| 3.2 | `_cmd_members_reset` 实现 | `intellect_cli/main.py` |
| 3.3 | Login 时检测 reset_code，强制改密 | `intellect_cli/main.py` |

### Phase 4: Owner 角色（1h）

| Step | 内容 | 文件 |
|------|------|------|
| 4.1 | bootstrap 首个成员 role='owner' | `intellect_cli/main.py` |
| 4.2 | `resolve_member_id` 返回 role | `agent/runtime_context.py` |
| 4.3 | handler 使用实际 role 替代硬编码 "admin" | `intellect_cli/main.py` |

### Phase 5: 测试（2h）

| Step | 内容 | 文件 |
|------|------|------|
| 5.1 | 密码哈希/验证单元测试 | `tests/agent/test_membership.py` |
| 5.2 | Login/logout/passwd/reset 集成测试 | `tests/intellect_cli/` |
| 5.3 | 暴力破解锁定测试 | `tests/agent/test_membership.py` |
| 5.4 | Owner reset 流程测试 | `tests/intellect_cli/` |

---

## 八、安全考量

| 项目 | 决策 |
|------|------|
| 密码哈希 | SHA-256(salt:pw)，带前缀 `sha256:` |
| Salt | 128-bit random hex |
| 传输 | 不涉及网络——CLI 本地操作 |
| 存储 | `members` 表明文（仅 hash），文件权限 0600 |
| 锁定 | 5 次→60s，10 次→15min |
| 重置码 | `IM-RC-` + 8-char hex，24h 过期，一次性使用 |
| 旧密码验证 | passwd 时必须验证旧密码（防 session 劫持后改密） |
| 首次设密 | 首次登录时强制提示设密码 |

---

## 九、总计

| Phase | 步骤 | 预计 |
|-------|------|------|
| Phase 1: Schema | 3 步骤 | 2h |
| Phase 2: Login/Logout | 5 步骤 | 3h |
| Phase 3: Passwd/Reset | 3 步骤 | 2h |
| Phase 4: Owner | 3 步骤 | 1h |
| Phase 5: 测试 | 4 步骤 | 2h |
| **合计** | **18 步骤** | **10h** |
