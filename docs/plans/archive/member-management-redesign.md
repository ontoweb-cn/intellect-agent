# Member 管理命令重构方案

**日期：** 2026-06-01（实现对照更新 2026-06-02）
**状态：** 已实现（CLI + agent + WebUI）；本文档为设计与验收对照

**关联：** 会话隔离 Phase 1–3 见 `2026-06-02-members-webui-hardening-design.md` §17；**待开发加固（Phase 4）** 见 `intellect-webui/docs/plans/session-member-isolation-plan.md` + §18；规范 `2026-05-31-profile-teams-members-projects-spec-v2.md` §12.7

---

## 一、现状

| 命令 | 权限 | 问题 |
|------|------|------|
| `intellect members create <login>` | admin+ | 名称应为 `add`（更直观） |
| `intellect members invite` | ~~任何已登录成员~~ | ✅ owner **或** admin（`Action.MEMBER_INVITE`） |
| `intellect members redeem <code>` | 任何人 | ✅ 已整合为 `members register <code>` |
| — | — | ✅ `activate` / `deactivate` / `delete` / `grant-owner` 已落地 |
| WebUI 本地注册 | — | ✅ `registration_pending` 与停用账号分离；profile admin 审批队列 |

---

## 二、新命令设计

### 命令总览

```
intellect members
  ├─ add <login> [--name] [--email]          owner: 直接添加成员（无需邀请码）
  ├─ invite [login] [--email] [--ttl] [--id] owner/admin: 生成邀请码（可选预留 member id）
  ├─ register <code>                         任何人: 用邀请码注册
  ├─ activate <login>                        owner: 启用已停用成员
  ├─ deactivate <login>                      owner: 停用成员
  ├─ delete <login>                          owner: 物理删除成员
  ├─ grant-owner <login>                     owner: 赋予另一个成员 owner 权限
  ├─ list                                    已登录: 列出成员
  ├─ show <login>                            已登录: 查看成员详情
  ├─ login <login>                           任何人: 登录
  ├─ logout                                  已登录: 登出
  ├─ passwd                                  已登录: 修改自己的密码
  ├─ reset <login>                           owner: 重置成员密码
  ├─ bootstrap [--admin-login] [--team] [--project]  首次设置
  ├─ bind <login> --provider <p>             admin+: 绑定 OAuth 身份
  ├─ identities <login>                      admin+: 查看 OAuth 绑定
  ├─ workspace <login>                       admin+: 查看工作区路径
  └─ whoami                                  已登录: 显示当前身份
```

### 权限矩阵

| 命令 | owner | admin | member | guest | 匿名 |
|------|:-----:|:-----:|:------:|:-----:|:----:|
| `add` | ✅ | | | | |
| `invite` | ✅ | ✅ | | | |
| `register` | | | | | ✅ |
| `activate` | ✅ | | | | |
| `deactivate` | ✅ | | | | |
| `delete` | ✅ | | | | |
| `grant-owner` | ✅ | | | | |
| `reset` | ✅ | | | | |
| `login` | | | | | ✅ |
| `logout` | ✅ | ✅ | ✅ | ✅ | |
| `passwd` | ✅ | ✅ | ✅ | ✅ | |
| `list` | ✅ | ✅ | ✅ | ✅ | |
| `show` | ✅ | ✅ | ✅ | ✅ | |
| `whoami` | ✅ | ✅ | ✅ | ✅ | |
| `bind` | ✅ | ✅ | | | |
| `identities` | ✅ | ✅ | | | |
| `workspace` | ✅ | ✅ | | | |
| `bootstrap` | ✅ | ✅ | | | |

---

## 三、详细流程

### 3.1 `add` — Owner 直接添加成员

```
$ intellect members add bob --name "Bob Smith" --email bob@example.com
✓ Member 'bob' created.

Bob 登录:
$ intellect members login bob
No password set. Create one now.
New password: ********
Confirm new password: ********
✓ Password set. Logged in as 'bob' (Bob Smith).
```

实现：调用 `create_member(display_name, login_name, email, actor_role=owner_role)`。成员创建后无密码，首次登录时强制设置。

### 3.2 `invite` + `register` — 邀请码注册

#### 生成邀请码（owner 或 admin）

```
$ intellect members invite charlie --email charlie@example.com
Invite code: IM-CH-ABCD1234
  Expires in: 168h
  Register with: intellect members register IM-CH-ABCD1234
```

可选 `--id <slug>`（WebUI/API 对应 `reserved_member_id`）：注册时必须使用该 member id。

存储（`member_invites`）：
```
  code, created_by, email, expires_at
  reserved_member_id   -- 可选；注册时强制使用该 id
  accepted_by, accepted_at
```

#### 注册（任何人持有邀请码）

```
$ intellect members register IM-CH-ABCD1234
Login name: charlie
New password: ********
Confirm new password: ********
✓ Registered as 'charlie'. Logged in.
```

流程（`MembershipStore.register_from_invite` / CLI `members register`）：
1. 校验邀请码（未过期、未使用）
2. 收集 login / member id / 密码（若邀请含 `reserved_member_id` 则 id 固定）
3. `validate_member_id` + 密码哈希后 **单次事务** 创建成员并标记邀请已用
4. 写入 CLI session（或 WebUI cookie）

### 3.3 `activate` / `deactivate` / `delete`

```
$ intellect members deactivate bob
✓ Member 'bob' deactivated. (enabled = 0, 软停用)

$ intellect members activate bob
✓ Member 'bob' activated. (enabled = 1)

$ intellect members delete bob
⚠ This permanently deletes member 'bob' and all associated data.
  Type 'bob' to confirm: bob
✓ Member 'bob' deleted.
```

实现：
- `activate`: `UPDATE members SET enabled = 1 WHERE login_name = ?`
- `deactivate`: `UPDATE members SET enabled = 0 WHERE login_name = ?`
- `delete`: 物理删除 member 行并级联：`member_identities`、`team_memberships`、`project_memberships`、`member_api_tokens`、该成员创建的 `member_invites`、`member_sessions`、拥有的 `projects`、OAuth token 文件、`sessions.member_id` 清空、`invited_by` 引用清空等。需要显式确认。WebUI `DELETE /api/members/{id}` 要求 `Action.ADMIN`（owner）。

### 3.5 WebUI 本地注册与审核（`registration_pending`）

当 `members.registration.local_requires_approval: true`（默认，见 `intellect_cli/config.py`）：

| 状态 | `enabled` | `registration_pending` | 含义 |
|------|-----------|------------------------|------|
| 待审（本地自助注册） | 0 | 1 | 出现在 `list_pending_registrations` |
| 已停用 | 0 | 0 | **不**出现在待审列表 |
| 正常 | 1 | 0 | 可登录 |

- **本地注册**：`register_local_pending` → 待审；OAuth `POST /register/pending` **不**设 `registration_pending`（由 `auto_provision` / 邀请 token 单独处理）。
- **批准**：`approve_registration` → `enabled=1`, `registration_pending=0`（仅当匹配待审行时返回 True）。
- **拒绝**：`reject_registration` → **物理删除** 该行；API 响应 `status: "deleted"`（非 `members.status` 列）。
- **停用**：`deactivate_member` 清除 `registration_pending`，避免与待审混淆。

**Owner/admin** 通过 WebUI **Members** 面板或 `GET/POST /api/members/registrations/*` 操作待审队列（不再使用 `members.profile_admins` 配置）。用户文档：`website/docs/user-guide/features/teams-and-members.md`；WebUI：`intellect-webui/docs/members-oauth-webui.md`。

### 3.4 `grant-owner` — Owner 赋权

```
$ intellect members grant-owner bob
⚠ This gives 'bob' full system owner privileges (all 17 permissions including ADMIN).
  Continue? [y/N]: y
✓ 'bob' is now an owner.
```

实现：
- `UPDATE members SET role = 'owner' WHERE login_name = ?`
- 仅当前 owner 可调用（`authorize(actor_role, Action.ADMIN)`）
- 需要显式确认

---

## 四、命令映射（旧→新）

| 旧命令 | 新命令 | 说明 |
|--------|--------|------|
| `members create <login>` | `members add <login>` | 重命名 |
| `members invite --login --email` | `members invite <login> [--email]` | login 提升为位置参数 |
| `members redeem <code>` | `members register <code>` | 语义更清晰 |
| — | `members activate <login>` | **新增** |
| — | `members deactivate <login>` | **新增** |
| — | `members delete <login>` | **新增** |
| — | `members grant-owner <login>` | **新增** |

保持不变的命令：`list`、`show`、`login`、`logout`、`passwd`、`reset`、`bootstrap`、`bind`、`identities`、`workspace`。

---

## 五、Schema 变更（已合并进 `intellect_state.py`）

**`members`**

```sql
registration_pending INTEGER NOT NULL DEFAULT 0  -- 1 = 本地注册待 profile admin 审批
```

**`member_invites`**

```sql
accepted_by TEXT;
accepted_at REAL;
reserved_member_id TEXT;   -- 可选；邀请注册时强制 member id
```

---

## 六、测试方案

### 6.1 单元测试（`test_membership.py`）

| # | 测试 | 验证 |
|---|------|------|
| 1 | `add` by owner → 成功 | create_member 返回 member_id |
| 2 | `add` by member → 拒绝 | authorize 返回 False |
| 3 | `activate` → enabled=1 | DB 状态切换 |
| 4 | `deactivate` → enabled=0 | DB 状态切换 |
| 5 | `delete` → 物理删除 + 级联 | member + 关联数据全部清除 |
| 6 | `grant-owner` → role='owner' | DB 更新 |
| 7 | `grant-owner` by admin → 拒绝 | authorize(admin, ADMIN) = False |
| 8 | `invite` by owner → 生成 code | code 格式 IM-CH-XXXXXXXX |
| 9 | `register` with valid code → 成功 | 成员创建 + 密码设置 + invite 标记 |
| 10 | `register` with expired code → 拒绝 | 验证过期检查 |
| 11 | `register` with already-used code → 拒绝 | 验证 accepted_by 检查 |

### 6.2 集成测试（`test_e2e_members_teams_projects.py`）

| # | 场景 | 步骤 |
|---|------|------|
| 1 | Owner add → 成员 login → 设密码 | 完整 add + 首次登录流程 |
| 2 | Owner invite → register | 邀请码 → 注册 → 自动登录 |
| 3 | Owner deactivate → 成员 login 被拒 | 停用后无法登录 |
| 4 | Owner delete → 数据清理 | 删除后 teams/projects 中无残留 |
| 5 | Owner → grant-owner → 新 owner 可 reset 他人 | 权限传递验证 |
| 6 | Admin 可 invite；无法 add/deactivate/delete/grant-owner | `MEMBER_INVITE` vs `ADMIN` |
| 7 | WebUI 本地注册 → 待审 → approve/reject | `registration_pending` 与 `enabled=0` 停用分离 |
| 8 | `delete_member` 级联 sessions/projects/OAuth | E2E `test_delete_member_*` |

---

## 七、实施步骤（已完成）

| Step | 内容 | 状态 |
|------|------|------|
| 1 | Schema：`member_invites` + `members.registration_pending` | ✅ |
| 2 | `MembershipDB` / `MembershipStore`：生命周期、邀请、注册、待审队列 | ✅ |
| 3 | CLI：`add`/`invite`/`register`/activate/deactivate/delete/grant-owner | ✅ |
| 4 | WebUI：`api/members.py` 注册/邀请/OAuth token/待审 API | ✅ |
| 5 | 测试：`test_e2e_members_teams_projects`、`intellect-webui/tests/test_members_webui.py` | ✅ |
| 6 | 文档：本文档、`spec-v2` §7.3/§16、`members-oauth-webui.md` | ✅ 2026-06-02 |

**已知限制：** 若历史数据存在 member **id** 为保留字 `admin`，`validate_member_id` 会阻止按 id 路径删除；应使用 login 名或迁移 id。新注册/邀请路径均已校验 id。
