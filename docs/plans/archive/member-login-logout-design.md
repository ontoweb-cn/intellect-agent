# Member Login/Logout 设计方案

**日期：** 2026-06-01  
**最后更新：** 2026-06-04  
**状态：** ✅ 已实现（含 CLI 24h `expires_at`）

---

## 一、现状

| 能力 | 状态 | 实现 |
|------|------|------|
| `intellect members login <login>` | ✅ | `_cmd_members_login` — 写入 `.cli-session.json` |
| `_write_cli_session()` | ✅ | 写入 0600 JSON，绑定 CLI identity |
| `resolve_member_id()` — 步骤 2 | ✅ | 读取 `.cli-session.json` 获取 member_id |
| `intellect members logout` | ✅ | 删除 `.cli-session.json` + `.cli-logged-out` 标记 |
| `_clear_cli_session()` | ✅ | 合并在 `logout` / `clear_cli_session()` |
| `/login` slash command | ✅ | Gateway sticky meta |
| `/logout` slash command | ✅ | 清除 sticky meta |
| `intellect members whoami` | ✅ | 显示 login、role、teams、projects |
| Session 过期 | ✅ | `.cli-session.json` 含 `expires_at`（24h）；过期由 `load_cli_session()` 清理 |

---

## 二、设计方案

### 2.1 CLI 端

#### `intellect members login <login>`

```
已实现 — 无需修改
  1. 查 members 表
  2. 写入 .cli-session.json (member_id, login_name, display_name, device_id)
  3. 绑定 CLI identity 到 identities 表
  4. 输出 "Logged in as '<login>' (Display Name)."
```

#### `intellect members logout`（新增）

```
流程：
  1. 检查 .cli-session.json 是否存在
     → 不存在: "Not logged in."
  2. 读取当前 session，获取 login_name
  3. 删除 .cli-session.json
  4. （可选）从 identities 表删除 CLI identity？→ 保留，不影响
  5. 输出 "Logged out '<login>'."
```

#### `intellect members whoami`（新增）

```
流程：
  1. 调用 resolve_member_id(config=config, session_file=...)
  2. 如果 None: "Not logged in. Use 'intellect members login <name>'."
  3. 查询 member 详情：display_name, login_name, email
  4. 查询 teams: list_active_teams(member_id)
  5. 查询 projects: list_projects(member_id)
  6. 格式化输出

输出示例：
  Logged in as: alice (Alice)
  Email: alice@example.com
  Teams: core, frontend
  Projects: web-app, api-server
```

### 2.2 Gateway 端

#### `/login <member_id_or_login>`（新增 slash command）

```
流程：
  1. 解析参数：支持 member_id 或 login_name
  2. 查 members 表
  3. 写入 state_meta: "session:{session_key}:member_id" = member_id
  4. 返回确认消息

注意：
  - Gateway 不使用 .cli-session.json（那是 CLI 专用）
  - 使用 state_meta 存储 sticky session state（与 /team、/project 一致）
  - 如果成员有 OAuth identity 绑定，提示验证
```

#### `/logout`（新增 slash command）

```
流程：
  1. 清除 state_meta: "session:{session_key}:member_id"
  2. 不清除 team_id / project_id（它们是独立设置的）
  3. 返回确认消息
```

### 2.3 `resolve_member_id()` 改造

当前步骤 2（CLI session file）在 Gateway 场景下不适用。改造方案：

```python
def resolve_member_id(
    *,
    platform: str = "cli",
    external_id: str | None = None,
    token: str | None = None,
    session_file: Path | None = None,
    config: dict | None = None,
    db: Any = None,
    session_key: str | None = None,  # 新增: Gateway session key
) -> str | None:
    # ... 现有步骤 1-3 ...

    # 2b. Gateway sticky session (新增)
    if session_key and db:
        row = db.get_meta(f"session:{session_key}:member_id")
        if row:
            return row

    # ... 现有步骤 4 (default member) ...
```

### 2.4 CLI 命令解析器扩展

```python
# intellect_cli/main.py

# ── members logout ──
members_subparsers.add_parser("logout", help="Log out current member")

# ── members whoami ──
members_subparsers.add_parser("whoami", help="Show current member identity")
```

### 2.5 Gateway Slash Commands 扩展

```python
# intellect_cli/commands.py COMMAND_REGISTRY

CommandDef("login", "Log in as a member", "Session",
           args_hint="<member_id_or_login>", gateway_only=True,
           gateway_config_gate="members.enabled"),

CommandDef("logout", "Log out current member", "Session",
           gateway_only=True,
           gateway_config_gate="members.enabled"),
```

---

## 三、数据流

### 登录流程

```
CLI                               Gateway
───                               ───────
intellect members login alice     /login alice
  │                                  │
  ├─ MembershipDB.get_member_by_login ├─ MembershipDB.get_member
  ├─ _write_cli_session()            ├─ state_meta["session:...:member_id"] = mid
  │   ├─ 写 .cli-session.json        └─ "Logged in as alice"
  │   └─ upsert identities(provider='cli')
  └─ "Logged in as 'alice'"

        ↓ 下次 agent 运行时

resolve_member_id()
  ├─ API token?                    → 继续
  ├─ CLI session file?             → 返回 member_id
  ├─ Gateway sticky session?       → 返回 member_id
  ├─ Platform identity?            → 继续
  └─ Single enabled member?        → 返回 member_id（隐式）
```

### 登出流程

```
CLI                               Gateway
───                               ───────
intellect members logout          /logout
  │                                  │
  ├─ 读 .cli-session.json            ├─ state_meta.delete("session:...:member_id")
  ├─ rm .cli-session.json            └─ "Logged out."
  └─ "Logged out 'alice'."

        ↓ 下次 agent 运行时

resolve_member_id()
  ├─ CLI session file → 不存在 → 继续
  ├─ Gateway sticky session → 不存在 → 继续
  ├─ Platform identity → 可能匹配 → 返回 member_id
  └─ Single member? → YES → 仍然返回（隐式自动登录）
```

### 关键设计决策：登出后是否回到单用户模式？

单成员时登出后 `resolve_member_id` 的步骤 4（唯一成员自动选中）**仍然有效**，导致"登出后立即自动重新登录"。有两种选择：

| 方案 | 行为 | 推荐 |
|------|------|------|
| **A** | 登出 = 真正登出，步骤 4 跳过（需额外状态标记） | 严格但增加复杂度 |
| **B** | 登出 = 清除显式 session，单成员场景自动回退 | 简单，符合"单用户兼容"设计 | ← |

建议采用 **方案 B**：`logout` 只清除显式登录状态，系统回退到默认行为。

---

## 四、实现步骤

| Step | 内容 | 文件 | 行数 |
|------|------|------|------|
| 1 | 新增 `_cmd_members_logout()` | `intellect_cli/main.py` | +20 |
| 2 | 新增 `_cmd_members_whoami()` | `intellect_cli/main.py` | +30 |
| 3 | `resolve_member_id()` 增加 `session_key` 参数 | `agent/runtime_context.py` | +8 |
| 4 | `/login` slash command handler | `gateway/run.py` | +25 |
| 5 | `/logout` slash command handler | `gateway/run.py` | +15 |
| 6 | COMMAND_REGISTRY 新增 login/logout | `intellect_cli/commands.py` | +10 |
| 7 | CLI parser 新增 logout/whoami | `intellect_cli/main.py` | +5 |
| 8 | 测试 | 测试文件 | +40 |

**总计：约 150 行，8 个步骤。**

---

## 五、后续：Owner 角色

login/logout 实现后，owner 角色的赋值方案：

1. **首个成员自动为 owner**：bootstrap 时检查 `SELECT COUNT(*) FROM members`，若为 0 则首个成员 `role='owner'`
2. **promote 命令**：`intellect members promote <login> --role owner`
3. **`members` 表加 `role` 列**：当前无此列，需 schema v17

*待 login/logout 确认后独立设计。*
