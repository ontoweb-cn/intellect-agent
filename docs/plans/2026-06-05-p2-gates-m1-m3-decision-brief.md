# P2 门禁决策一页纸：M1 · M2 · M3

**日期:** 2026-06-05  
**状态:** ✅ **已拍板（按推荐）** — 2026-06-05  
**阻塞:** [联合存储 PR](2026-06-02-multi-database-cache-mq-design.md) 的 **P2 PostgreSQL** 与多进程 WebUI  
**关联:** `intellect-webui/docs/plans/webui-agent-gap-analysis.md`、`session-member-isolation-plan.md`  
**其余待决:** [2026-06-05-joint-pr-remaining-decisions.md](2026-06-05-joint-pr-remaining-decisions.md)

---

## 拍板结果

| 议题 | 结论 |
|------|------|
| **M1** | ✅ **A** — 新成员仅 hex；WebUI 注册/邀请改造 |
| **M2** | ✅ **B** — `joined_at IS NULL` = pending；无 `status` 列 |
| **M3** | ✅ **默认** — `strict`；`owner_sees_all` / `admin_sees_all` = false；不按 team 过滤 |
| **M5** | ✅ P2 与 M1–M3 同一 release train |

---

## 历史：评审说明（已定稿）

<details>
<summary>原 M1–M3 方案对比（归档）</summary>

| 议题 | 原结论栏 |
|------|----------|
| M1 member_id | ☑ A ☐ B |
| M2 team pending | ☑ B ☐ A |
| M3 会话隔离 | ☑ 采纳默认配置 |

---

## M1 — `member_id` 格式（新成员如何定 id）

### 现状

| 来源 | 行为 |
|------|------|
| Agent CLI / `create_member()` | 自动生成 **12 位 hex** |
| WebUI 邀请注册 `register_from_invite` | 用户提交 **`member_id`**，经 `validate_member_id()` — **允许 hex 与 slug**（如 `alice`） |
| WebUI OAuth / `_resolve_or_create_member` | **Agent 生成 hex**，`display_name` 作 `login_name` |
| 同一 `state.db` | 可并存 hex 与 slug，不崩溃，但隔离/团队引用依赖 id 字符串一致 |

### 方案 A — 新成员仅 hex（推荐）

| 项 | 内容 |
|----|------|
| **规则** | 所有**新** WebUI 注册/邀请兑换：`member_id` 由 agent **自动生成**；用户可见名 → `display_name` + 可选 `login_name` |
| **邀请** | 邀请码可带 `reserved_member_id`（仍为 hex）；表单不再让用户填 id |
| **已有 slug** | 保留；`validate_member_id_existing()` 继续可寻址旧行 |
| **迁移** | 无强制迁移；文档说明「新部署请用 hex」 |

**优点:** 与 CLI/bootstrap 一致；PG 外键、日志、Redis 通道更干净。  
**缺点:** 邀请 UX 少一项「自定义 id」；需改 WebUI 注册 API/表单。

### 方案 B — 继续允许 slug 作 `member_id`

| 项 | 内容 |
|----|------|
| **规则** | 保持 `validate_member_id` 双格式；邀请/本地注册仍可用户指定 slug |
| **约束** | 文档规定 slug 字符集；禁止与 hex 混用同一「逻辑用户」 |

**优点:** 无注册 UI 变更。  
**缺点:** 联合 PR 后 PG 多 worker 场景下，运维与排错仍靠「猜 id 格式」；与 agent 默认生成路径长期分叉。

### 推荐：**方案 A**

**P2 门禁:** 拍板 A 后，在 P2 前完成 WebUI 注册/邀请路径改造 + 测试（现有 hex 成员不受影响）。

---

## M2 — 团队加入「待审批」如何存

### 现状

- Agent 表 `team_memberships`：**无 `status` 列**；有 `joined_at`、`role`、`invited_by`。
- `MembershipStore`（WebUI 用）已约定：**`joined_at IS NULL` → `status: "pending"`**；approve 时写入 `joined_at`；reject 删行。
- 见 `agent/membership.py` — `request_team_join` / `approve_team_join` / 列表 API 的 `d["status"] = "active" if d.get("joined_at") else "pending"`。

### 方案 A — 增加 `status` 列（`pending` / `active` / `rejected`）

| 项 | 内容 |
|----|------|
| **Schema** | `_reconcile_columns` + PG Alembic 增加 `team_memberships.status TEXT` |
| **语义** | reject 可保留行 `rejected`（审计）或删行（需统一） |
| **代码** | WebUI + agent 读写 `status`，逐步弃用「仅 joined_at」推断 |

**优点:** PG 查询直观（`WHERE status='pending'`）；与 WebUI 产品词汇一致。  
**缺点:** 联合 PR 多一张表迁移；须定义 reject 是否留痕。

### 方案 B — 以 `joined_at` 为唯一真相（推荐）

| 项 | 内容 |
|----|------|
| **规则** | **不新增列**；pending = `joined_at IS NULL`；active = `joined_at` 非空；reject = **DELETE**  membership 行（与现 `reject_team_join` 一致） |
| **文档** | 在 agent §schema 与 WebUI API 文档写明；禁止第二套 pending 标记 |
| **PG** | 索引 `(team_id) WHERE joined_at IS NULL` 可选 |

**优点:** 零 schema 变更即可进 P2；与当前运行时一致。  
**缺点:** 「rejected 历史」无 DB 行级记录（若产品以后要审计需再加表）。

### 推荐：**方案 B**（联合 PR 内）；若产品强需求「拒绝留痕」，再单开 schema PR 走向 A。

**P2 门禁:** 拍板 B 后，仅需 **文档 + 测试** 固化语义；选 A 则须排在 P2 **之前** 合入迁移。

---

## M3 — 多用户会话隔离（P1–P5）

### 现状（代码默认，`intellect_cli/config.py`）

```yaml
members:
  session_isolation:
    legacy_null_visibility: strict      # NULL member_id 会话：非 owner/admin 不可见
    owner_sees_all_sessions: false
    admin_sees_all_sessions: false
    require_member_id_on_save: true     # WebUI 侧 enforced
```

`session-member-isolation-plan.md` 曾建议 **owner 默认可见全员**；与当前 **默认 false** 不一致 — **需产品确认**。

### P1 — owner 是否可见全员会话？

| 方案 | 配置 | 适用 |
|------|------|------|
| **P1a** | `owner_sees_all_sessions: true` | 小团队运维：owner 可审计所有成员聊天元数据 |
| **P1b（现默认）** | `owner_sees_all_sessions: false` | owner 仅看自己的会话，除非改配置 |

**推荐:** **P1b 保持 false**；需要审计的部署在文档中显式打开 `true`。

### P2 — 全局 admin 是否可见全员？

| 方案 | 配置 |
|------|------|
| **P2a（现默认）** | `admin_sees_all_sessions: false` |
| **P2b** | `admin_sees_all_sessions: true` |

**推荐:** **P2a** — admin 能力与 member 接近，避免「多个超级旁听者」。

### P3 — 遗留 `member_id IS NULL` 会话

| 方案 | `legacy_null_visibility` | 行为 |
|------|--------------------------|------|
| **P3a（现默认）** | `strict` | 普通成员**看不到** NULL 会话；owner/admin 依 P1/P2 |
| **P3b** | `legacy_shared_null` | NULL 会话对**所有**成员可见（迁移兼容） |

**推荐:** 新部署 **P3a strict**；仅升级旧库时短期开 `legacy_shared_null` + 跑 `intellect members sessions migrate-ownership`。

### P4 — 侧栏是否按 `team_id` 过滤？

| 方案 | 说明 |
|------|------|
| **P4a（推荐）** | v1 **不做**；`SessionListScope.active_team_id` 暂不用于过滤 |
| **P4b** | 侧栏只显示当前 active team 的会话 |

**推荐:** **P4a** — 联合 PR 不扩 scope。

### P5 — WebUI 是否展示 CLI/Gateway 的 NULL 会话？

在 **P3a strict** 下，普通成员**本就不显示** NULL 行。  
若 **P1a owner 可见全员**，owner 在 WebUI 可见 CLI NULL 会话。

**推荐:** 与 **P1b + P3a** 一致 — 普通用户不展示；不要求额外 WebUI 特例。

### M3 总推荐（写入 `config.yaml` 默认）

| 键 | 值 |
|----|-----|
| `legacy_null_visibility` | `strict` |
| `owner_sees_all_sessions` | `false` |
| `admin_sees_all_sessions` | `false` |
| `require_member_id_on_save` | `true` |
| team 过滤 | 不做（P4a） |

**P2 前必须完成:** 会话隔离计划 **阶段 B 写路径**（`member_id` 写入 JSON + `state_sync`）已落地 — 见 webui `session-member-isolation-plan.md`；若未验收，**阻塞 P2**。

</details>

---

## 拍板后动作清单（执行中）

| 决策 | Agent | WebUI |
|------|-------|-------|
| **M1=A** ✅ | 文档：新成员仅 hex；invite `reserved_member_id` 仍为 hex | 注册/邀请 UI 去掉用户填 id；服务端生成 hex |
| **M2=B** ✅ | 文档化 `joined_at` 语义；可选 partial index | approve/reject 继续走 store API |
| **M3** ✅ | 保持 `DEFAULT_CONFIG`（strict，owner/admin false） | 回归 `test_session_member_scope.py` |

---

## 相关链接

- 总设计：[2026-06-02-multi-database-cache-mq-design.md](2026-06-02-multi-database-cache-mq-design.md) §12.2 P2 前置、§16
- WebUI 索引：`intellect-webui/docs/plans/README.md`
