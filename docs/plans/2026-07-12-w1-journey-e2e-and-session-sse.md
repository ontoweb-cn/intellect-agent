# W1 细化稿 + 技术评审（双轨并行）

> **日期**：2026-07-12  
> **状态**：REVIEWED — **Approve**（用户签字 2026-07-12）  
> **策略（已确认）**：**方案 3** — Journey P1-4 E2E ∥ Session SSE P0 RFC/启动  
> **前置**：W0 已合入 `d2cefe4`（P1-1/P1-2/P1-C + Turn Anchors RFC draft）  
> **父文档**：[`2026-07-11-p1-journey-and-webui-parity-refinement.md`](./2026-07-11-p1-journey-and-webui-parity-refinement.md)、[`2026-07-11-webui-hermes-parity-analysis.md`](./2026-07-11-webui-hermes-parity-analysis.md)  
> **计划评审**：2026-07-12 Request changes → 修订 → **Approve**

---

## 0. 评审摘要（待签）

| 轨 | 本拍目标 | 合入形态 |
|----|----------|----------|
| **A Journey** | **P1-4** Learning API E2E 回归闸 | 1 个小 PR（**~1d**） |
| **B Session SSE** | **RFC v1 定稿**（含 S1–S9）；B1 有界 buffer 可选同拍 | RFC PR + 可选 buffer PR；**禁止**只做服务端无客户端 |
| **旁路（可选）** | P1-A live→settled helper；同机 Gateway Restart | **不进 W1 DoD** |

**跨轨原则**：A 与 B **无代码耦合**；B2+B3 客户端闭环后，才开 P1-A **服务端** scene / P1-B **全量** virt。  
**P1-B MVP 硬化**可与 P0 后半并行，**不**依赖 B2+B3（与父文档 §2.5 一致）。

### 0.1 W1 Definition of Done（合入门槛）

| 必须 | 可选 | 明确不在 W1 DoD |
|------|------|-----------------|
| 轨 A：E1–E4 绿并合入 | B1 有界 buffer | B2+B3 端点+客户端 |
| 轨 B0：RFC 状态 → **REVIEWED**（签字） | Opt-C / Opt-D | B4 wakeup；P1-3；P1-A/B 全量 |

**实施进度（2026-07-12）**：

| 项 | 状态 |
|----|------|
| 轨 A E1–E4 | ✅ `tests/agent/test_learning_e2e.py`（code review Approve；Important 缺口不挡合入） |
| 轨 B0 RFC | ✅ **REVIEWED / Approve** — `docs/webui/rfcs/session-sse-contract-v1.md` |
| B1 有界 buffer | ✅ `StreamChannel` S9 caps + `dropped_offline_events`；`tests/webui/test_stream_channel_bounds.py` |
| B2+B3 | ⏳ 待另开 `writing-plans` |

**W1 ≠ P0 完成**：wakeup 门禁 + 可恢复客户端仍属 P0 backlog；本拍只把 SSE 推到「可评审契约」（+ 可选 buffer）。

---

## 1. W0 → W1 交接

| 项 | W0 状态 | W1 动作 |
|----|---------|---------|
| Journey P1-2 hub provenance | ✅ | 契约已定；E2E 必须覆盖 archive **与** uninstall + `ambiguous` |
| Journey P1-1 stale 409 | ✅ | E5 **非门槛**（已有 handler mock；真漂移另测） |
| Parity P1-C model guards | ✅ | 本拍不扩；发现回归再开 hotfix |
| Turn Anchors RFC | ✅ DRAFT | 本拍不实施全量；旁路可做 live→settled helper |
| Session SSE RFC | ❌ 未产 | **本拍主产出之一** |
| Journey P1-3 gateway scope | 延后 | **仍延后**（挂 profile-management 恢复） |
| P1-B transcript virt | 未开 | MVP 硬化可 W2 与 P0 后半并行；全量 virt 跟 SSE 客户端 |

---

## 2. 轨 A — Journey P1-4 E2E

### 2.1 目标

把 Learning **写路径**从「单元断言」升到 **handler → 真实磁盘 → restore/graph** 闭环，防止 hub/profile 语义回退。

> 现有 `test_learning_mutations.py` / `test_learning_api_profile.py` 已覆盖大量 mutation/API mock；本拍价值在 **不 mock `delete_node`/`node_detail`**，经 handler 打穿。

### 2.2 锚点

| 层 | 路径 |
|----|------|
| Handlers | `webui/api/learning.py`（`handle_learning_node_*`） |
| Mutations | `agent/learning_mutations.py` |
| Graph | `agent/learning_graph.py` |
| Restore | `tools/skill_usage.restore_skill` / `archive_skill` |
| Hub | `tools/skills_hub` lock + uninstall |
| 现有单测 | `tests/agent/test_learning_mutations.py`、`test_learning_api_profile.py` |
| **新建** | `tests/agent/test_learning_e2e.py` |

### 2.3 MVP 用例矩阵

| # | 门槛？ | 场景 | 步骤 | 断言 |
|---|--------|------|------|------|
| E1 | **必须** | Agent-created archive | fixture → `handle_learning_node_delete` → graph | `.archive/<name>/SKILL.md`；`restore_skill` 后 graph 再现；`source=profile` |
| E2 | **必须** | Pinned refuse | pin → DELETE | 失败；磁盘未动 |
| E3 | **必须** | Hub uninstall | `.hub` + lock + use_count → DELETE | 不进 `.archive/`；lock 空；audit 在 **活动** home；graph 无节点 |
| E4 | **必须** | Ambiguous | profile + hub 同名 → GET/DELETE/PUT | HTTP **409** + `code=ambiguous`；两侧磁盘未变 |
| E5 | 非门槛 | Stale memory | 真索引漂移或越界 GET | 409 + `code=stale`（可选；勿仅重复 mock） |

**非目标**：Playwright Journey UI；gateway `/journey`；memory 局部下标迁移。

### 2.4 实现约束

1. 经 **handler** 调用（录 `j(..., status=)`）；**禁止** mock `learning_mutations.delete_node` / `node_detail`。  
2. Profile 隔离：对齐 `test_learning_api_profile.py` — `get_active_intellect_home` + `cron_profile_context_for_home`（learning 模块内 `_active_profile_context` 封装二者）。  
3. E3：故意把模块级 `AUDIT_LOG` 指到错误 home，断言 audit 仍写活动 home（锁 W0 Important #3）。  
4. 不用 change-detector（不硬编码技能数量）。

### 2.5 估时 / PR

| | |
|--|--|
| 估时 | **~1d**（E1–E4；E5 不进关键 path） |
| PR 标题建议 | `test(webui): Journey learning API E2E for archive, hub uninstall, ambiguous` |
| 依赖 | P1-2 ✅ |
| 阻塞 | 无（不挡 SSE） |

---

## 3. 轨 B — Session SSE P0（RFC 启动）

### 3.1 问题（对照 parity 分析 §3 P0）

1. 有 stream `run_journal` + `after_seq`，**无**统一可恢复契约（`Last-Event-ID` / 有界回放 / 诚实 gap）。  
2. `StreamChannel._offline_buffer` 仍是 **无界 list**（`webui/api/config.py` ~4394）。  
3. Wakeup / 凭证耗尽暂停门禁仍缺（B4；**不**宣称本拍完成 P0）。  
4. Hermes 教训：**禁止**「只做服务端、客户端下一里程碑」。

**代码现实（RFC 必读）**：

- 持久日志：`_run_journal/{session_id}/{run_id}.jsonl`，回放在 **`api/chat/stream?stream_id=`** + `after_seq`  
- 活缓冲：per-stream `StreamChannel`（内存）  
- **`GET /api/sessions/events` 已存在**（会话**列表** SSE，非 per-session 事件流）— 新路径命名必须消歧

### 3.2 本拍范围切分

| 切片 | 内容 | W1？ |
|------|------|------|
| **B0** | 写 `docs/webui/rfcs/session-sse-contract-v1.md` 并评审定稿（S1–S9） | **DoD 必须** |
| **B1** | `StreamChannel` 有界 deque + drop-oldest + gap 可观测 | **强烈建议**；进可选 DoD |
| **B2** | 端点（演进 chat/stream 或新 path）+ TOCTOU cursor | 出 W1 DoD；RFC 后 W1 尾/W2 |
| **B3** | 前端 EventSource + Last-Event-ID + `session_snapshot` | **与 B2 同里程碑** |
| **B4** | Wakeup paused 门禁 | 独立；P0 backlog |

### 3.3 RFC 必须钉死的决议（写入 RFC 正文）

| # | 议题 | 决议 / 必须回答 |
|---|------|-----------------|
| S1 | 数据面 | **扩展现有 `run_journal`**，不新建平行 per-session event log |
| S2 | 断线策略 | 有界回放；缺口返回诚实 `session_snapshot` |
| S3 | Cursor 锁定 | Headers 提交前锁定 resume cursor + journal baseline（防 TOCTOU） |
| S4 | 客户端 | 与服务端 **同里程碑**；参考 `kanban_bridge` Last-Event-ID |
| S5 | Turn Anchors | 本 RFC **不要求** scene 双写 |
| **S6** | **端点命名** | 在「演进 `api/chat/stream` / 新 `GET /api/sessions/{id}/events` / facade」中择一；**必须消歧**已有列表 SSE `/api/sessions/events` |
| **S7** | **Cursor 身份** | 今日多为 `{run_id}:{seq}` / `after_seq`；钉死 session resume：无 `active_stream_id`、跨 run、idle 时算法 |
| **S8** | **`session_snapshot`** | 触发条件（gap / unknown cursor / 无 active run）与最小字段 |
| **S9** | **B1 临时常量** | 若 RFC 前合 B1：写明 provisional `max_events` / `max_bytes` / drop-oldest / 诊断字段（如 `dropped_offline_events`） |

### 3.4 RFC 目录骨架

```text
1. Problem / Non-goals（含：W1 不宣称 P0 完成）
2. Existing surfaces
   - run_journal per (session, run)
   - StreamChannel offline buffer
   - api/chat/stream + after_seq / STREAM_LAST_EVENT_ID
   - GET /api/sessions/events（列表 SSE — 消歧）
   - messages.js _lastRunJournalSeq
3. Event model (id, seq, types; map today's payloads)
4. Resume protocol (S6–S8)
5. Bounds (S9 + 正式常量)
6. Auth / profile scope (cookie active home)
7. Client algorithm (connect → replay → live; generation guards)
8. Acceptance + test plan
9. Migration / rollout (feature flag?)
10. Open questions (wakeup B4 coupling only)
```

### 3.5 锚点

| 模块 | 路径 |
|------|------|
| Journal | `webui/api/run_journal.py` |
| Buffer | `webui/api/config.py` `StreamChannel` |
| Replay | `webui/api/routes.py` `_replay_run_journal` |
| Runtime cursor | `webui/api/runtime_adapter.py` `_cursor_to_after_seq` |
| 列表 SSE（消歧） | `GET /api/sessions/events` + `sessions.js` EventSource |
| 前端 seq | `messages.js` `_lastRunJournalSeq`；`STREAM_LAST_EVENT_ID` |
| Kanban 参考 | `webui/api/kanban_bridge.py` |
| 对照 | Hermes `session-sse-contract-v1`（思想，非照搬路径） |

### 3.6 估时 / PR

| 切片 | 估时 | PR |
|------|------|-----|
| B0 RFC | **1–2d** | `docs: Session SSE contract v1` |
| B1 有界 buffer | **0.5–1d** | `fix(webui): bound StreamChannel offline buffer`（须带 S9 常量） |
| B2+B3 | **5–10d**（跨 W1–W2） | `writing-plans` **仅 RFC 批准后** |
| B4 wakeup | **2–3d** | 独立 |

---

## 4. 旁路（可选，不挡主轴）

| ID | 内容 | 何时做 | 依赖 |
|----|------|--------|------|
| **Opt-C** | P1-A `_convertLiveActivityGroupToSettled` + 稳定 activityKey | 人力空闲；**不**写服务端 scene | Turn Anchors RFC draft |
| **Opt-D** | 同机 Gateway Restart | 运维痛时；独立小 PR | 见下 |
| **P1-3** | Gateway `/journey` member/profile | **仍延后** | profile-management 恢复 |

**Opt-D 延期声明**：parity §8 DECIDED #3 原为「与 P0 **并行**独立小 PR」。本 W1 将其降为 **opt-in**（不进 DoD）。一旦排期，必须遵守 DECIDED **#1**（跟随活动 profile）与 **#4**（更新 API × gateway 状态硬失败），不得重新发明作用域。

默认：**旁路不进 W1 合入门槛**。

---

## 5. 并行矩阵与日历

```text
W1:
  [A] P1-4 E2E (E1–E4) ───────────────────► merge
  [B] Session SSE RFC (S1–S9) ──► REVIEWED ──► (B1 可选)
  [opt] live→settled / gateway restart ───► 旁路

W1 尾 / W2:
  B2+B3 同里程碑客户端闭环
  P1-B MVP 硬化（可与 P0 后半并行，不依赖 B2+B3）
  P1-A 全量 / P1-B 全量 virt（跟 SSE 客户端）
  P1-3 若 profile 热
```

| | P1-4 | SSE RFC | Buffer B1 | SSE 客户端 | Opt-C | Opt-D |
|--|------|---------|-----------|------------|-------|-------|
| P1-4 | — | 独立 | 独立 | 独立 | 独立 | 独立 |
| SSE RFC | 独立 | — | S9 常量可先 | **先 RFC** | 独立 | 独立 |
| SSE 客户端 | 独立 | 依赖 | 宜先有界 | — | 慎并行 settle | 独立 |

---

## 6. 明确不做（本拍）

- P1-A 服务端 `activity_scene_v1` 双写  
- P1-B 消息区 spacer **全量**虚拟窗（MVP 硬化不在本拍 DoD，可另排）  
- Journey P1-3 / memory 局部下标迁移  
- 整包移植 Hermes `static/` 或平行 SessionChannel  
- 「只合 SSE 服务端、客户端下拍再说」  
- 宣称 **P0 完成**（缺 wakeup + 可恢复客户端）

---

## 7. 技术评审议程

### 7.1 会前阅读（~20min）

1. 本文件 §0–§6（含 §0.1 DoD）  
2. W0：父文档 W0 进度；commit `d2cefe4`  
3. Parity 分析 §3 P0 + §8 DECIDED  
4. Turn Anchors RFC（边界即可）

### 7.2 议题与建议决议

| # | 议题 | 建议决议 | 勾选 |
|---|------|----------|------|
| R1 | W1 主轴维持 P1-4 ∥ SSE RFC | **是** | ☑ |
| R2 | P1-4 经 handler + 含 hub/ambiguous；E1–E4 门槛 | **是** | ☑ |
| R3 | 数据面扩展 `run_journal`（S1）；并过 S6–S8 | **是** | ☑ |
| R4 | B1 可在 RFC 定稿前合入 | **可**（必须带 S9 provisional 常量 + gap 指标） | ☑ |
| R5 | B2+B3 禁止先服务端后客户端 | **否（禁止拆）** | ☑ |
| R6 | Opt-C/D 进 W1 门槛 | **否** | ☑ |
| R7 | P1-3 本拍 | **否** | ☑ |
| R8 | Wakeup B4 绑死 SSE RFC | **否**；P0 仍不完整（I6） | ☑ |
| **R9** | RFC 定稿前必须钉死 S6–S9 | **是** | ☑ |

### 7.3 签字栏

| 角色 | 姓名 | 日期 | 结论（Approve / Changes） |
|------|------|------|---------------------------|
| Author | agent | 2026-07-12 | Approve（修订稿） |
| Reviewer | user | 2026-07-12 | **Approve** |

**总评**：W1 收口 Journey 写路径回归，并把 Session SSE 推到可评审契约；有界 buffer 为低风险早赢。实施 B2+B3 前 RFC 必须过 **R3–R5 与 R9（S6–S9）**。

---

## 8. 文档索引

| 文档 | 角色 |
|------|------|
| **本文件** | W1 细化 + 评审 |
| `2026-07-11-p1-journey-and-webui-parity-refinement.md` | 双轨 P1 父排期 |
| `2026-07-11-webui-hermes-parity-analysis.md` | 差距权威摘要 |
| 待产 `docs/webui/rfcs/session-sse-contract-v1.md` | P0 契约 |
| `docs/webui/rfcs/stable-assistant-turn-anchors.md` | P1-A DRAFT |
| `2026-07-08-hermes-v0.16-v0.18-port-todo.md` §HP-401 | Journey 条目源 |

---

## 9. 下一步（签字 Approve 后）

1. 实施轨 A：`tests/agent/test_learning_e2e.py`（E1–E4）。  
2. 起草轨 B0：`docs/webui/rfcs/session-sse-contract-v1.md`（含 S6–S9）→ 单独 RFC 评审。  
3. （可选）B1 有界 buffer（S9 常量入 PR 描述）。  
4. RFC 批准后另开 `writing-plans` 产出 B2+B3（本文件不替代）。  
5. 旁路仅勾选后排期；Opt-D 遵守 DECIDED #1/#4。

---

## 10. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07-12 | 初稿 READY FOR REVIEW（方案 3） |
| 2026-07-12 | 计划评审 Request changes：补 DoD、S6–S9、P1-B 序列修正、Opt-D 延期声明、E5 非门槛、估时 ~1d |
| 2026-07-12 | **Approve** — R1–R9 全部通过；可开实施 |
