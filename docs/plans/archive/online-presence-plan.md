# 用户在线状态功能设计方案

**日期：** 2026-06-01
**状态：** ✅ 已实现（commit `8ebb86ebd`，75 tests passed）

---

## 一、需求分析

### 1.1 核心需求

在多用户模式下，系统需要记录和展示成员的在线状态：

- **谁在线**：当前有哪些成员处于活跃状态
- **从哪里登录**：CLI / Telegram / Discord / Slack / Signal 等
- **何时登录**：最后活跃时间
- **在线多久**：会话时长

### 1.2 使用场景

```
$ intellect members list
LOGIN    NAME      ROLE    STATUS   LAST SEEN
alice    Alice     owner   online   now (CLI)
bob      Bob       member  online   5m ago (Telegram)
charlie  Charlie   member  offline  2h ago (CLI)

$ intellect members show alice
...
Status: online
Sessions:
  CLI      — active since 2026-06-01 14:30 (current)
  Telegram — last seen 2026-06-01 10:15

Gateway /whoami:
  You: alice (owner) — online
  Active sessions: 2 (CLI, Telegram)
```

### 1.3 非目标

- 实时心跳/WebSocket（不需要秒级精度）
- 成员间即时通讯
- 跨 profile 在线状态

---

## 二、数据模型

### 2.1 `member_sessions` 表

```sql
-- Schema v18
CREATE TABLE IF NOT EXISTS member_sessions (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id),
    platform TEXT NOT NULL,           -- 'cli', 'telegram', 'discord', 'slack', ...
    session_type TEXT NOT NULL,       -- 'login' | 'activity'
    external_id TEXT,                 -- platform user ID (for gateway sessions)
    ip_address TEXT,                  -- optional, for audit
    user_agent TEXT,                  -- optional
    login_at REAL NOT NULL,           -- session start / login time
    last_active_at REAL NOT NULL,     -- last heartbeat / activity
    expires_at REAL,                  -- session TTL
    status TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'expired' | 'logged_out'
    metadata TEXT                     -- JSON: device_id, chat_name, etc.
);

CREATE INDEX IF NOT EXISTS idx_member_sessions_member
    ON member_sessions(member_id, status);
CREATE INDEX IF NOT EXISTS idx_member_sessions_platform
    ON member_sessions(platform, external_id);
```

### 2.2 `members` 表新增字段

```sql
-- 聚合字段，由 member_sessions 触发器或定期刷新维护
ALTER TABLE members ADD COLUMN last_active_at REAL;
ALTER TABLE members ADD COLUMN last_active_platform TEXT;
ALTER TABLE members ADD COLUMN online_status TEXT DEFAULT 'offline';  -- 'online' | 'offline'
```

---

## 三、状态生命周期

```
成员执行操作 (login / API call / gateway message)
  │
  ├─ 查找或创建 member_sessions 行
  │   (platform + external_id 或 CLI session key 作为唯一标识)
  │
  ├─ 更新 last_active_at = now()
  ├─ 更新 status = 'active'
  │
  ├─ 如果之前 status != 'active':
  │   ├─ 设置 login_at = now()（新会话开始）
  │   └─ 触发 "online" 事件
  │
  └─ 更新 members 聚合字段:
      ├─ last_active_at = now()
      ├─ last_active_platform = platform
      └─ online_status = 'online'

成员 logout 或 session 过期:
  │
  ├─ 更新 member_sessions.status = 'logged_out' 或 'expired'
  ├─ 检查该成员是否还有其他 active session
  │   ├─ 有 → 保持 online_status = 'online'
  │   └─ 无 → members.online_status = 'offline'
```

### Session TTL

| 场景 | TTL | 说明 |
|------|-----|------|
| CLI 登录 | 24h | `.cli-session.json` 存在期间 |
| CLI logout | 立即 | `.cli-session.json` 删除 |
| Gateway 消息 | 1h | 最后一次消息后 1 小时过期，新消息自动续期 |
| Gateway /logout | 立即 | 显式登出 |

过期 session 由定期清理任务处理（`list` 命令时被动清理 + 可选 cron）。

---

## 四、实现步骤

### Phase 1: Schema + 数据层（1h）

| Step | 文件 | 内容 |
|------|------|------|
| **1.1** | `intellect_state.py` | Schema v18：`member_sessions` 表 + `members` 表新增 `last_active_at`、`last_active_platform`、`online_status` |
| **1.2** | `agent/membership.py` | `MembershipDB` 新增方法：`record_session(member_id, platform, session_type, external_id)`、`update_activity(member_id, platform, external_id)`、`end_session(member_id, platform, external_id)`、`list_active_sessions(member_id)`、`get_online_status(member_id)` |
| **1.3** | `agent/membership.py` | `list_members()` 增加返回 `online_status`、`last_active_at`、`last_active_platform` 字段 |

### Phase 2: CLI 接入（1h）

| Step | 文件 | 内容 |
|------|------|------|
| **2.1** | `intellect_cli/main.py` | `_cmd_members_login` 成功后调用 `db.record_session(member_id, 'cli', 'login', device_id)` |
| **2.2** | `intellect_cli/main.py` | `_cmd_members_logout` 调用 `db.end_session(member_id, 'cli')` |
| **2.3** | `intellect_cli/main.py` | `_cmd_members_register` 成功后调用 `db.record_session` |
| **2.4** | `intellect_cli/main.py` | `_cmd_members_list` 输出增加 `STATUS` 和 `LAST SEEN` 列 |
| **2.5** | `intellect_cli/main.py` | `_cmd_members_show` 输出增加 online status + active sessions 列表 |

### Phase 3: Gateway 接入（1h）

| Step | 文件 | 内容 |
|------|------|------|
| **3.1** | `gateway/run.py` | 每次消息处理时调用 `db.update_activity(member_id, platform, session_type)` — 续期 session |
| **3.2** | `gateway/run.py` | `/login` slash command 显式创建 session |
| **3.3** | `gateway/run.py` | `/logout` slash command 结束 session |
| **3.4** | `gateway/run.py` | `/whoami` 显示 online status |

### Phase 4: 清理 + 聚合刷新（0.5h）

| Step | 文件 | 内容 |
|------|------|------|
| **4.1** | `agent/membership.py` | `_refresh_online_status(member_id)` — 检查 active sessions 数量，刷新 `members.online_status` |
| **4.2** | `agent/membership.py` | `cleanup_expired_sessions()` — 将过期 session 标记为 `expired`，刷新对应成员的 online_status |
| **4.3** | `intellect_cli/main.py` | `_cmd_members_list` 调用 `cleanup_expired_sessions()` 前做被动清理 |

### Phase 5: 测试（2h）

| Step | 文件 | 内容 |
|------|------|------|
| **5.1** | tests | 数据库方法测试：record/update/end/list_active/cleanup |
| **5.2** | tests | CLI 测试：login 记录 session → list 显示 online → logout 清除 |
| **5.3** | tests | 多 session 测试：同一成员 CLI+Gateway 双 session，logout 一个后仍 online |
| **5.4** | tests | 过期清理测试：session TTL 过后 status 变 expired |

---

## 五、输出示例

### `intellect members list`

```
LOGIN      NAME        ROLE    STATUS    LAST SEEN
alice      Alice       owner   online    now (CLI)
bob        Bob Smith   member  online    3m ago (telegram)
charlie    Charlie     member  offline   1h ago (CLI)
dave       Dave        member  offline   2d ago (CLI)
```

### `intellect members show alice`

```
Login:       alice
Name:        Alice
Role:        owner
Status:      online
Last seen:   2026-06-01 14:30:15 (CLI)

Active sessions:
  CLI        — since 2026-06-01 14:30 (current)
  Telegram   — since 2026-06-01 10:15
```

### Gateway `/whoami`（多用户启用时）

```
You: alice (owner)
Status: online
Active sessions: 2 (CLI, Telegram)
Slash commands you can run: all available
```

---

## 六、总计

| Phase | 内容 | 时间 |
|-------|------|------|
| 1: Schema + DB | 3 步骤 | 1h |
| 2: CLI | 5 步骤 | 1h |
| 3: Gateway | 4 步骤 | 1h |
| 4: 清理 | 3 步骤 | 0.5h |
| 5: 测试 | 4 步骤 | 2h |
| **合计** | **19 步骤** | **5.5h** |

---

## 七、设计中需确认的选择

| 决策 | 选项 A | 选项 B | 建议 |
|------|--------|--------|------|
| Session TTL | CLI 24h / Gateway 1h | 统一 TTL（如 2h） | A — 场景不同 |
| 离线判定 | 定时器主动标记 | 查询时被动检查 | B — 简单，不依赖后台线程 |
| 在线状态字段 | 聚合列自动刷新 | 每次查询实时计算 | A — `COUNT(*)` 查询开销低，聚合列避免重复 |
| 审计日志 | member_sessions 即审计 | 独立 audit 表 | 当前方案用 member_sessions 存储所有登录事件即可 |
| 跨 Profile | 不支持 | 支持 | 不支持 — profiles 隔离是设计原则 |
