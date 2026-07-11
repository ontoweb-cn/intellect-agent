# P1 细化稿 + 技术评审（双轨）

> **日期**：2026-07-11  
> **状态**：REVIEWED — 细化稿（非实施完成）  
> **范围**：  
> - **轨 A**：HP-401 Journey 跟进（P0 已合入后的 P1-1…P1-4）  
> - **轨 B**：WebUI Hermes parity 长对话体验（Turn Anchors / Transcript 虚拟窗 / 会话·provider 竞态）  
> **前置**：Journey P0（`8c7e9b0`）；parity 分析定稿 `docs/plans/2026-07-11-webui-hermes-parity-analysis.md`  
> **非目标**：本文件不实施代码；不替代 P0 Session SSE RFC；不展开 P2 gateway 重启

---

## 0. 评审摘要

| 轨 | 结论 | 合入策略 |
|----|------|----------|
| **A Journey** | 四项均可独立小 PR；**P1-2 应先于 P1-4**；P1-1 可与 P1-2 并行；P1-3 可延后到 profile/member 场景变热 | 1–2 周内可清完 MVP |
| **B Parity UX** | Intellect **已有** assistant-turn / Activity / 尾窗 50 / session generation / model hardening；缺的是 **命名契约与持久化**，不是从零建设 | **P1-C 可立即开**；P1-B MVP 可并行；**P1-A 全量依赖 P0 Session SSE**，仅 RFC + live→settled helper 可先做 |

**跨轨排期原则**：Journey 轨不阻塞 WebUI SSE P0；Parity 轨中 P1-A 实施排在 Session SSE 契约之后。

---

## 1. 轨 A — HP-401 Journey P1

### 1.1 现状锚点

| 模块 | 路径 |
|------|------|
| 图装配 | `agent/learning_graph.py` |
| 变更 | `agent/learning_mutations.py` |
| WebUI REST | `webui/api/learning.py`（P0 已 cookie-scope） |
| 前端 | `webui/static/journey.js` |
| Gateway | `gateway/command_handlers.py` `_handle_journey_command` |
| Archive / Hub | `tools/skill_usage.py`、`tools/skills_hub.py` |

### 1.2 P1-1 — Memory 索引 id 过期 → 404

**根因**：`memory:{source}:{i}` 中 `i` 是 **全量 card 列表的全局下标**（`learning_graph.py` ~291–304）。任一 MEMORY/USER 块增删都会使后续 id 漂移；`_memory_local_index` 校验失败后统一打成 404（`learning.py`）。

**严重度**：中（常见 UX：缓存列表点选 → 误以为丢数据）。

| | 内容 |
|---|------|
| **MVP（S）** | API：stale 返回 **409** 或 `404 + {code:"stale"}`；`journey.js`：遇 stale 自动 `loadJourney(true)` + 提示重选 |
| **理想（M）** | id 改为 **文件内局部下标** `memory:{source}:{local}`；去掉全局映射；可选内容指纹 v2 |
| **验收** | 删 `memory:memory:0` 后 profile 节点 id 行为符合方案文档；前端不静默 404 |
| **测试** | 扩展 `test_learning_mutations.py`；API 层断言 status/code；保留 graph/mutation parser parity |
| **依赖** | 无；宜在 P1-4 前或同批锁定 id 语义 |

**评审决议**：先做 **MVP（S）** 止血；局部下标迁移单独 PR，避免与 hub 语义搅在一起。

---

### 1.3 P1-2 — Hub skill 删除 vs curator

**根因**：

1. 图包含 hub（`use_count > 0`），但节点 JSON **无** `source`/`installKind`。  
2. `_find_skill` / archive 路径 **排除** `.hub`；`archive_skill` **拒绝** hub（`skill_usage.py` ~488–489）。  
3. 正确卸载是 `skills_hub.uninstall_skill()`。  
4. UI 一律提示 `curator restore` —— 对 hub **错误**。

**严重度**：中高（对 hub 用户：可点删除 → 失败/误导恢复路径）。

| | 内容 |
|---|------|
| **MVP（S）** | 图节点暴露 `source: profile\|hub`；hub 隐藏 Delete 或改文案指向 Skills uninstall；后端若仍收到 delete → 走 uninstall 并返回明确文案 |
| **理想（M）** | `learning_mutations` 内 provenance 路由器：`{kind, delete_fn, restore_hint}`，与 `skill_manage` / curator 不变量对齐 |
| **验收** | hub 节点可 detail；delete 不进 `.archive/`；agent-created 仍 archive + restore |
| **测试** | fixture：`.hub/<name>/` + lock + use_count；断言 uninstall 路径；pinned/agent 回归 |
| **依赖** | **阻塞 P1-4** |

**评审决议**：**必须先于 P1-4**；MVP 即可合，provenance 路由器可跟。

---

### 1.4 P1-3 — Gateway `/journey` profile / member scope

**根因**：`build_learning_graph()` 用进程级 `INTELLECT_HOME`，无 `resolve_member_id` / session profile 包装。WebUI 已用 `_active_profile_context()`。单 profile gateway（`intellect -p X`）碰巧正确。

**严重度**：今日低–中；profile 管理恢复或 `memory_scope=member` 时升至中高。

| | 内容 |
|---|------|
| **MVP（S）** | `/journey` 内 `resolve_member_id` → 设 `INTELLECT_MEMBER_ID`；文档写明 profile = gateway `-p` |
| **理想（M）** | 共享 `learning_context_for_event(event)`；若会话级 profile 回归则对齐 `profile_env_for_background_worker` |
| **验收** | 双 profile / 双 member fixture 下列表不串 |
| **测试** | gateway 单测（现 `tests/gateway/` 无 journey 覆盖） |
| **依赖** | 产品模型（per-gateway profile vs session profile）需与 `2026-06-profile-management-disabled-restore` 对齐 |

**评审决议**：**可延后**到 profile/member 热路径；不挡 Journey WebUI 体验。

---

### 1.5 P1-4 — Learning API E2E（删 skill → curator restore）

**根因**：仅有单元级 archive 断言与 profile mock；缺 handler→磁盘→restore→graph 闭环；hub 语义未定前写 E2E 会测错契约。

| | 内容 |
|---|------|
| **MVP（S）** | `tests/agent/test_learning_e2e.py`：agent-created skill → DELETE handler → `.archive/` → `restore_skill` → graph 再现；pinned 拒绝 |
| **理想（M）** | 含 hub uninstall 分支 + 可选 Playwright Journey 冒烟 |
| **依赖** | **P1-2 之后** |

**评审决议**：P1-2 合入后立刻补 MVP E2E，作为 Journey 写路径回归闸。

---

### 1.6 轨 A 实施顺序（评审定稿）

```text
P1-2 (hub) ──► P1-4 (E2E)
P1-1 (memory stale)  可并行 P1-2
P1-3 (gateway scope) 延后 / 与 profile 恢复同里程碑
```

| ID | 估时 | 优先级 |
|----|------|--------|
| P1-2 MVP | 1–2d | P1-A1 |
| P1-1 MVP | 0.5–1d | P1-A2（可并行） |
| P1-4 MVP | 1d | P1-A3 |
| P1-1 理想 / P1-2 理想 / P1-3 | 各 2–4d | backlog |

---

## 2. 轨 B — WebUI Hermes parity P1

对照：`docs/plans/2026-07-11-webui-hermes-parity-analysis.md` §3 P1；Hermes RFC 名仅作契约思想，不整仓移植。

### 2.1 已有能力（不宜重复建设）

| 能力 | 锚点 |
|------|------|
| `.assistant-turn` + live `#liveAssistantTurn` | `webui/static/ui.js` |
| Activity group（`data-live-tool-call-group`）+ disclosure localStorage | `ui.js` `ensureActivityGroup` |
| Inflight localStorage + 有界 | `ui.js` + `config.py` |
| Run journal `after_seq` replay | `run_journal.py` / `messages.js` |
| 消息尾窗默认 50 +「加载更早」 | `MESSAGE_RENDER_WINDOW_*` |
| 侧栏虚拟化（固定行高） | `sessions.js` `_sessionVirtualWindow` |
| Follow-intent / `overflow-anchor: none` | `ui.js` / `style.css` |
| `_loadingSessionId` / `_messagesGeneration` / `_isSessionCurrentPane` | `sessions.js` / `messages.js` |
| Model：`preferredProviderId` + deferred resolution | `ui.js` `syncTopbar` / `_applyModelToDropdown` |

### 2.2 P1-A — Stable Assistant Turn Anchors

**差距（相对 Hermes 意图）**：无持久化 `activity_scene_v1`；live/replay/settled/inflight 无统一契约；`done` 立即 `renderMessages` 全量 materialize；无 `transparent_stream`；注释提到 `_convertLiveActivityGroupToSettled` **函数不存在**（仅 `ui.js` ~5228 注释）。

| | 内容 |
|---|------|
| **RFC（必先）** | `docs/webui/rfcs/stable-assistant-turn-anchors.md`：scene schema、四态渲染、与 `run_journal` 关系（**不**平行第二套 journal） |
| **MVP（M）** | 实现 live→settled 转换；稳定 `activityKey`；inflight 内有界 scene 快照；`chat_activity_display_mode` 别名映射现有 `simplified_tool_calling` |
| **全量（L）** | 服务端 scene 持久化；replay 同构；idle 延迟 worklog；真 `transparent_stream` |
| **风险** | 不碰 agent prompt cache；须使 `_sessionHtmlCache` / scroll settle 不回归 |
| **依赖** | **全量实施依赖 P0 Session SSE 客户端**；RFC + helper 可与 P0 并行 |

**评审决议**：同意分析稿 R4——缺的是分层契约与持久化，不是 DOM 从零。**禁止**在 P0 SSE 未落地前做服务端 scene 双写。

---

### 2.3 P1-B — Transcript 虚拟窗口

**差距**：现有是 **尾切片**（隐藏行不在 DOM，但无 spacer 高度账本）；用户行 **无** 固有高度记忆；侧栏 virt 模式未迁到消息区。

| | 内容 |
|---|------|
| **MVP（M）** | 硬化 50 窗 + SSE `done`/重连 scroll；用户行 `ResizeObserver` 高度缓存；follow-intent 与 P0 重连对齐 |
| **全量（L）** | `#msgInner` spacer 虚拟窗（移植 `_sessionVirtualWindow`）；按 `rawIdx` 高度 Map |
| **风险** | HTML cache key 须含 window bounds；jump-to-question / compression anchor 在虚窗下仍可用 |
| **依赖** | 重连 scroll 对齐依赖 P0；MVP 硬化可先做 |

**评审决议**：先 MVP 硬化，全量 virt 单独立项，避免与 P1-A settle 动画互相踩。

---

### 2.4 P1-C — 会话加载 generation / provider 下拉

**已有**：`_loadingSessionId`、`_messagesGeneration`、`_isSessionCurrentPane`、`preferredProviderId` 路径大体到位。

**缺口**：

1. `sessions.js:731` 调用 `_applyPendingSessionModelForSession`，**全仓无定义**（死钩子）。  
2. `_modelStateForSelect` 在裸 model id 时仍可能读 `selectedOptions[0]`。  
3. 需审计所有异步 session 路径是否统一 generation 护栏。

| | 内容 |
|---|------|
| **MVP（S）** | 实现或删除死钩子；加载期间禁止 `_persistSessionModelCorrection`；解析只信 `S.session.model_provider` |
| **理想（S–M）** | 全路径审计清单 + 快速切会话回归用例 |
| **依赖** | **无**；可与 P0 / Journey 并行 |

**评审决议**：**立即开做**（性价比最高，防串台）。

---

### 2.5 轨 B 实施顺序（评审定稿）

```text
P0 Session SSE (契约+客户端) ──► P1-A 全量 / P1-B 全量 virt
         │
P1-C (立即) ─────────────────────► 贯穿全程防串台
P1-A RFC + live→settled helper ──► 可与 P0 并行
P1-B MVP 硬化 ───────────────────► 可与 P0 后半 / P1-C 并行
```

| ID | 估时 | 优先级 |
|----|------|--------|
| P1-C MVP | 2–4d | **B0（立即）** |
| P1-A RFC | 2–3d | B1（与 P0 并行） |
| P1-B MVP | 5–8d | B2（P0 后或尾声） |
| P1-A 全量 | 2–3w | B3（P0 后） |
| P1-B 全量 virt | 1–2w | B4（P1-A MVP 后） |

---

## 3. 合并排期（双轨）

### 3.1 建议日历（示意，可按人力压缩）

| 周次 | Journey 轨 A | Parity 轨 B | 备注 |
|------|--------------|-------------|------|
| **W0** | P1-2 + P1-1 MVP | **P1-C** + P1-A RFC 起草 | 互不阻塞 |
| **W1** | P1-4 E2E | P0 Session SSE 启动（若尚未） | Journey 轨可收口 |
| **W2–W3** | P1-3（若 profile 热）或理想项 | P0 客户端 + P1-B MVP | |
| **W4+** | backlog | P1-A 全量 → P1-B 全量 | |

### 3.2 并行矩阵

| | Journey P1-1/2/4 | Journey P1-3 | P1-C | P1-A RFC | P1-A 全量 | P1-B MVP | P0 SSE |
|--|------------------|--------------|------|----------|-----------|----------|--------|
| Journey P1-1/2/4 | — | 独立 | 独立 | 独立 | 独立 | 独立 | 独立 |
| P1-C | 独立 | 独立 | — | 独立 | 独立 | 独立 | 独立 |
| P1-A 全量 | 独立 | 独立 | 独立 | 先 RFC | — | 慎并行 | **依赖** |
| P1-B 全量 | 独立 | 独立 | 独立 | 独立 | 宜先后 | 先 MVP | 重连依赖 |

### 3.3 明确不做（本 P1 窗口）

- 整包移植 Hermes `static/` / 平行 SessionChannel  
- P1-A 在无 P0 客户端时上服务端 scene 双写  
- Vault 概念页以外的 Hermes 历史文档改写  
- Gateway 重启（属 parity P2 / 并行小项，见分析稿 §4）

---

## 4. 技术评审结论（签字栏）

| # | 议题 | 决议 |
|---|------|------|
| R-A1 | Journey memory id | MVP 先 stale UX；局部下标另 PR |
| R-A2 | Hub delete | P1-2 先于 P1-4；禁止对 hub 承诺 curator restore |
| R-A3 | Gateway `/journey` | 可延后；单 profile 不挡 |
| R-B1 | Turn anchors | 演进现有 turn/activity；RFC 先行；全量跟 P0 |
| R-B2 | Transcript virt | 先硬化尾窗；全量 virt 单独立项 |
| R-B3 | Session/provider | 立即修死钩子 + 审计；与 P0 并行 |
| R-X1 | 双轨关系 | **无代码耦合**；日历上 Journey 收口快，Parity 跟 SSE |

**总评**：两套 P1 均可开干；建议 **本周并行：Journey P1-2/P1-1 + Parity P1-C**，同时起草 Turn Anchors RFC 与（若未开始）Session SSE P0。

### W0 落地进度（2026-07-11，已确认开干）

| 项 | 状态 | 产出 |
|----|------|------|
| Journey P1-2 hub | ✅ MVP | `learning_mutations` hub detail/uninstall；图节点 `source`；Journey UI 文案 |
| Journey P1-1 stale | ✅ MVP | `code: stale` + HTTP 409；前端自动 refresh |
| Parity P1-C | ✅ MVP | `_applyPendingSessionModelForSession`；session provider 优先；resolve 护栏 |
| Turn Anchors RFC | ✅ DRAFT | `docs/webui/rfcs/stable-assistant-turn-anchors.md` |
| Journey P1-4 E2E | ⏳ 下一拍 | 依赖 P1-2（已就绪） |

---

## 5. 文档索引

| 文档 | 角色 |
|------|------|
| `docs/plans/2026-07-08-hermes-v0.16-v0.18-port-todo.md` §HP-401 P1 | Journey 条目源 |
| `docs/plans/2026-07-11-webui-hermes-parity-analysis.md` | Parity 差距权威摘要 |
| **本文件** | 双轨细化 + 评审 + 排期 |
| 待产：`docs/webui/rfcs/session-sse-contract-v1.md` | P0 |
| 待产：`docs/webui/rfcs/stable-assistant-turn-anchors.md` | P1-A |

---

## 6. 下一步（需你确认）

1. 是否按 **W0：P1-2 + P1-1 + P1-C** 开实施？  
2. Session SSE P0 是否已有人或需另开 `writing-plans`？  
3. Journey P1-3 是否挂到 profile-management 恢复里程碑？
