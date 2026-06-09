# 联合 PR 其余待决事项（T · M4–M5）

**日期:** 2026-06-05  
**状态:** ✅ **已全部按推荐拍板**（M1–M3 + T1–T12 + M4）  
**父文档:** [2026-06-02-multi-database-cache-mq-design.md](2026-06-02-multi-database-cache-mq-design.md)  
**M1–M3:** [2026-06-05-p2-gates-m1-m3-decision-brief.md](2026-06-05-p2-gates-m1-m3-decision-brief.md)

---

## 拍板总表（T · M4）

| 议题 | 结论 | 阶段 |
|------|------|------|
| **T1** | ✅ **A** — Kanban 表并入 `state.db` 单文件；提供 `migrate-kanban` | P3 |
| **T2** | ✅ **A** — 单 Redis；cache db0 / events db1 | P4 |
| **T3** | ✅ 冻结 §16.3 通道命名 | P4 |
| **T4** | ✅ **B** — P4 不做 chat Redis liveness | P4 |
| **T5** | ✅ **A** — `INTELLECT_WEBUI_WORKERS`；>1 强制 PG+Redis | P4 |
| **T6** | ✅ **B** — `intellect db backup` → tarball + manifest | P3 |
| **T7** | ✅ **A** — WebUI 读 agent `config.yaml` + env | P1 |
| **T8** | ✅ **B** — P1 可错峰；**P2+ 同 tag** + min 版本声明 | P1+ |
| **T9** | ✅ **A** — `sync_to_insights` 默认 off；HA 文档说明 | 文档 |
| **M4** | ✅ **A** — 多 worker 登录写 `member_sessions`；N>1 不依赖 `.member-sessions` 文件 | P4 |
| **T10** | ✅ SQLAlchemy **Core** only | P2 |
| **T11** | ✅ Agent 循环保持 **同步** | — |
| **T12** | ✅ ResponseStore → **CacheBackend** | P4a |

---

## 已拍板（范围 · 成员 · 侧栏）

| 组 | 决议 |
|----|------|
| **M1** | **A** — 新成员仅 agent 生成 hex；`display_name` / `login_name` 承载用户名 |
| **M2** | **B** — `joined_at IS NULL` = pending；reject 删行；不新增 `status` 列 |
| **M3** | **默认配置** — `strict` + owner/admin 均 `false` + 不按 team 过滤侧栏 |
| **M5** | P2 与 M1–M3 **同一 release train**；不「先 PG 后改成员」 |
| **侧栏搜索** | 联合 PR = Option A 子串；Option B FTS = 单独 PR (§17.1) |
| **范围** | Graphiti / RAG / Helm **不进**联合 PR (§17) |

### M1 拍板后的实现要点（供排期）

- WebUI `register_from_invite` / 本地注册：**不再接受用户自定义 slug 作 id**（邀请 `reserved_member_id` 仍为 hex）。
- 已有 slug 成员：**只读兼容**（`validate_member_id_existing`）。
- 测试：邀请注册、OAuth `_resolve_or_create_member`、CLI hex 并存。

---

## 新增功能需求（2026-06-05，已纳入主设计）

| ID | 需求 | 文档 |
|----|------|------|
| **R1** | 同一产品同时支持 SQLite 与 PG/Redis 能力；**单用户仅 SQLite** | §10.3 `single_user` profile |
| **R2** | 切换多用户时提供存储选项；**UI 默认推荐 PostgreSQL**，SQLite 为进阶/单进程 | §16.6 enablement flow |
| **R3** | 选 PG 时 **OAuth 与 DB 内运行态** 从 SQLite 迁入 PG 后再切 `storage.backend` | §16.6 迁移表 + `intellect db migrate-sqlite-to-pg` — **✅ 已落地并本机验证**（2026-06-05） |

**联合 PR 存储状态（2026-06-06）：** P1–P4 ✅ 已落地（`v0.4.2`）。含 W1–W4b/W6、T1–T6/T12、M1/M4、R2/R3、session-member-isolation。**下一项 P5：** W5 读副本路由；侧栏 FTS Option B、OAuth §9 联调、§17 Deferred 轨道为联合 PR 之后 backlog。

---

## 拍板后实现清单（按阶段）

| 阶段 | 交付项 |
|------|--------|
| **P1** | T7 WebUI `load_config()` 读 `storage`/`cache`/`events`；T8 release note 模板 |
| **P2** | ✅ T10 SQLAlchemy Core；M1 hex-only；**R2/R3** W7–W8 + `migrate-sqlite-to-pg` + `doctor --storage` |
| **P3** | **T1 ✅** — `migrate-kanban` + `kanban.storage=unified` + `board_id` + PG 方言；**KanbanRepository** + webui `kanban_bridge` 切换；**T6 ✅** backup + restore |
| **P4** | ✅ T2–T3 Redis 通道；T5 worker 门禁；T12 ResponseStore / idempotency / run status；**M4** `member_sessions`；W4b pub/sub（sessions / approval / clarify / kanban / `runs.{id}`） |
| **P5** | **W5** — PG 读副本路由（agent + webui 只读路径） |
| **Doc** | T9 HA 与 `sync_to_insights` 说明；§10.3 三档 profile 说明 |

---

## 历史：方案对比（归档）

<details>
<summary>T1–T12 原方案说明</summary>

---

## T1 — Kanban 在 SQLite 上如何落盘（P3 前）

### 现状

独立 `kanban.db`；`kanban_bridge` → `intellect_cli.kanban_db`。

### 方案 A — 表并入 `state.db` 单文件（推荐）

- 新装与迁移后仅一个 SQLite 文件；备份与 §12.3 manifest 一致。
- 一次性迁移：`intellect db migrate-kanban`（或 restore 路径）从旧 `kanban.db` 导入。

### 方案 B — 保留 `kanban.db` + `ATTACH DATABASE`

- 同一 `SQLiteBackend` 实例 ATTACH；路径仍两个文件。
- 备份 manifest 必须列两个文件。

**推荐 A。** PG 侧仍用 `kanban` schema（与 A 逻辑一致）。

---

## T2 — Redis 部署拓扑（P4 前）

### 方案 A — 单 Redis 实例，分 logical DB（推荐）

- 与现有 config 草案一致：`cache` → db `0`，`events` → db `1`（可用 `INTELLECT_REDIS_URL` 覆盖）。
- 联合 PR **不要求** Sentinel。

### 方案 B — 两个 Redis 实例（cache / events 分离）

- 运维复杂；仅超大部署考虑。

**推荐 A。** §7.3 Sentinel 标为 **post joint PR**。

---

## T3 — EventBus / Redis 通道命名（P4 前）

### 推荐 — 冻结 §16.3 + agent §8 对照表

| 前缀 | 用途 |
|------|------|
| `webui.sessions` | 侧栏 `sessions_changed` |
| `webui.approval.{session_id}` | 审批 SSE 扇出 |
| `webui.clarify.{session_id}` | 澄清 SSE 扇出 |
| `webui.kanban.{board_id}` | Kanban 失效 |
| `runs.{run_id}` / `runs.{run_id}.tools` | API Server / gateway（agent §8） |
| `gateway.sessions` | WebUI gateway 流（保持 agent 契约） |
| `sessions.{id}` | SessionDB 写后缓存失效（agent） |

**规则:** 新通道必须带 `webui.` / `gateway.` / `runs.` 命名空间；禁止裸 channel 名。

---

## T4 — `/api/chat/stream` 是否用 Redis liveness（P4b）

### 方案 A — P4b 上 Redis liveness

- 换 worker 后仍可发现「流在别的工作进程活着」。
- 需定义 payload、`stream_id` 租约 TTL、与 JSONL journal 关系。

### 方案 B — P4b 不做（推荐）

- Token 流仍 **仅本 worker SSE**；跨 worker 靠 `sessions_changed`、审批/澄清通道、客户端 `/api/chat/stream/status` + journal 重放。
- liveness 若有需求 → **P4b 后小 PR**（单独设计）。

**推荐 B**，降低 P4 面。

---

## T5 — 如何判定「多 worker WebUI」（P4b）

### 方案 A — 环境变量（推荐）

- `INTELLECT_WEBUI_WORKERS`（整数，默认 `1`）。
- `>1` 时启动检查：`storage.backend=postgresql`、`cache.backend=redis`、`events.backend=redis`，否则 **exit 1** 并打印修复提示。

### 方案 B — 运行时探测 worker PID 文件

- 由 process manager 写入；对 Docker/K8s 不统一。

**推荐 A**；在 `website/docs` 或部署 README 给 compose 示例。

---

## T6 — 备份对外形态（P3）

### 方案 A — 分项产物

- `pg_dump` 文件 + 目录拷贝 `sessions/` 等；用户自行打包。

### 方案 B — `intellect db backup` → 单 tarball（推荐）

- Manifest 列出版本、profile、`state.db` 或 dump、`sessions/`、journals、`.member-sessions`、checksum。
- `restore` 先 dry-run 再应用。

**推荐 B** 为用户唯一主路径；内部仍可调用 A 的工具。

---

## T7 — WebUI 如何读 storage/cache 配置（P1）

### 方案 A — 以 agent `config.yaml` 为准（推荐）

- WebUI 通过 `intellect_cli.config.load_config()`（或共享 helper）读 `storage` / `cache` / `events`。
- `INTELLECT_*` env 与 gateway 相同优先级。
- `settings.json` **仅** WebUI UI/本地偏好，不重复定义 PG/Redis。

### 方案 B — WebUI `settings.json` 镜像一份

- 双源易漂移；不推荐。

**推荐 A**；P1 在 `api/state_sync.py` 等处验证 profile 与 `INTELLECT_HOME` 一致。

---

## T8 — 两仓库发布节奏

### 方案 A — 严格同 tag

- `intellect-agent` 与 `intellect-webui` 同一版本号发布；CI 联合矩阵。

### 方案 B — 分阶段（推荐）

| 阶段 | Agent | WebUI |
|------|-------|-------|
| **P1** | 可先发布（ABCs + factory） | W1 跟进（兼容旧 agent 若仅 factory 未用 PG） |
| **P2+** | **必须** 同 tag / 同文档标注最低 WebUI 版本 | W2+ 硬依赖 PG 与 M1 |

**推荐 B**；在 release note 写 `min webui x.y` / `min agent x.y`。

---

## T9 — HA 部署与 `sync_to_insights`

| 方案 | 行为 |
|------|------|
| **A（推荐）** | 默认 **`sync_to_insights: off`**；HA 文档说明：开启可把用量/标题 mirror 到 PG，但 **JSON 仍为聊天真相**；侧栏子串搜 JSON |
| **B** | HA 推荐默认 **on**，换更强 PG 侧栏元数据 |

**推荐 A**，与 §16.4、侧栏 Option A 一致。

---

## M4 — 多 worker 时 WebUI 成员会话（P4 前）

### 方案 A — 登录/登出写 `member_sessions` 表（推荐）

- Cookie token 与 DB 行一致；多 worker 共享 PG。
- `.member-sessions` 文件：**单 worker 可保留**；`WORKERS>1` 时警告或只读 fallback。

### 方案 B — 继续仅文件 + flock

- 与 PG HA 目标冲突；不推荐。

**推荐 A**；可与 gap-analysis Phase 4.2 合并排期。**排期随 W4b 延后**（2026-06-05）：单 worker + PG 先行，多 worker 再开。

---

## T10–T12 — §13 技术项（建议直接 Resolved）

| # | 议题 | 决议 |
|---|------|------|
| T10 | SQLAlchemy Core vs 手写 SQL | **Core** |
| T11 | Agent 循环同步 vs 全 async | **保持同步** |
| T12 | ResponseStore → CacheBackend | **是**（P4a） |

无需单独会议，除非有人推翻。

</details>

---

## 明确延期（本轮不表决）

| ID | 内容 | 文档 |
|----|------|------|
| L1 | 侧栏 v2 FTS | §17.1 |
| L2 | JSON → `messages` 全量写穿 | §16.4 / P6+ |
| L3–L7 | Graphiti、RAG、Helm、在线状态、WebUI RBAC、Redis Sentinel | §17 |
| — | WebUI → `/v1/runs` | §16.2 |
| — | `sessions.storage_backend` per-section override | config future |

---

## 建议决策顺序

```text
P1 前:  T7, T8(P1段), T10–T12
P3 前:  T1, T6
P4 前:  T2, T3, T4, T5, M4
P2 前:  M1 实现（已拍板）+ M3 写路径验收
全程:   T9 文档
```

---

## 文档回写

✅ 已同步至主设计 §18、§13、§15.1（Kanban SQLite）、`intellect-webui/docs/plans/README.md`。
