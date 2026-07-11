# W2 细化稿 — Session SSE B2+B3 → Parity 旁路 → B4

> **日期**：2026-07-12  
> **状态**：REVIEWED — 技术评审 Request changes → 修订；**#1 B2+B3 实施中/已落地核心**  
> **策略（用户确认）**：先技术评审，再按 **1→5** 严格串行执行  
> **前置**：W1 已合入 `9183e41`（P1-4 E2E + Session SSE RFC REVIEWED + B1 StreamChannel bounds）  
> **父文档**：[`2026-07-12-w1-journey-e2e-and-session-sse.md`](./2026-07-12-w1-journey-e2e-and-session-sse.md)、[`2026-07-11-webui-hermes-parity-analysis.md`](./2026-07-11-webui-hermes-parity-analysis.md)、[`2026-07-11-p1-journey-and-webui-parity-refinement.md`](./2026-07-11-p1-journey-and-webui-parity-refinement.md)  
> **契约 SoT**：[`docs/webui/rfcs/session-sse-contract-v1.md`](../webui/rfcs/session-sse-contract-v1.md)（S1–S9 **REVIEWED**）

### 0.3 实施进度

| # | 项 | 状态 |
|---|----|------|
| 1 | B2+B3 | ✅ `api/session_sse.py` + `_handle_sse_stream` + `messages.js` snapshot/close latch；测试绿 |
| 2 | P1-B MVP | ✅ follow-intent helper、用户行高度 Map、cache key 含 window bounds |
| 3 | Opt-C | ✅ `_convertLiveActivityGroupToSettled` + done 路径 |
| 4 | Opt-D | ✅ `gateway_lifecycle.py` + `POST /api/health/restart` + banner + updates DECIDED #4 |
| 5 | B4 | ✅ `wakeup_pause.py` + chat/start 门禁 + quota/rate-limit set + done clear |
---

## 0. 评审摘要

| # | 项 | 估时 | DoD 合入形态 |
|---|----|------|--------------|
| **1** | **B2+B3** Session SSE 端点硬化 + 同里程碑客户端 | 3–5d | 1 个里程碑（可拆 2 PR，但同 release train；**禁止**只合服务端） |
| **2** | **P1-B MVP** transcript 窗硬化 | 2–4d | 独立 PR；**不**做 spacer 全量 virt |
| **3** | **Opt-C** live→settled helper | 1–2d | 独立小 PR；**不**写服务端 scene |
| **4** | **Opt-D** 同机 Gateway Restart | 1–2d | 独立小 PR；**含** DECIDED #4 `updates.py` 硬失败 |
| **5** | **B4** wakeup / credential-exhaustion pause | 2–3d | 独立 PR；完成后方可宣称 **P0 完成** |

**串行原则**：后一项不得抢先合入阻塞前一项的契约/API 面；文档可并行起草。  
**跨轨原则**：Journey P1-3 / P1-A 全量 / P1-B 全量 virt **不进** W2 DoD。  
**排期覆盖**：parity DECIDED #3「Opt-D 可与 P0 并行」被本拍用户指令 **1→5 串行** 覆盖；紧急运维可插队并书面记录。

**W2 完成 ≠ P0 完成**：P0 宣称仅当 **#1 + #5**（B1 已在 W1）。#2–#4 是 UX/运维旁路。

### 0.1 W2 Definition of Done

| 必须 | 明确不在 W2 DoD |
|------|-----------------|
| #1 B2+B3 绿并合入（RFC S6–S8 + §2.4 终态 close） | Journey P1-3 gateway `/journey` |
| #2 P1-B MVP 硬化合入 | P1-A 服务端 scene 双写 |
| #3 Opt-C helper 合入 | P1-B spacer 全量 virt / `#msgInner` `_sessionVirtualWindow` |
| #4 Opt-D Restart API + banner + **updates.py 硬失败挂钩** | SessionChannel / Hermes 路径改名 |
| #5 B4 pause 门禁合入 → **P0 可宣称完成** | Turn Anchors 全量 |

### 0.2 技术评审结论（2026-07-12）

| Verdict | Request changes → 本修订后可执行 |
|---------|----------------------------------|
| Critical 已修 | C1 终态 snapshot → `es.close()` + 禁止重连；C2 Opt-D DoD 含 DECIDED #4 |
| Important 已修 | T9–T12；无 cursor 双分支；双 parser 破环；回放 provisional caps；feature-flag 拍板；B4 SoT 预锁；#2–4 验收行 |

---

## 1. W1 → W2 交接

| 项 | W1 状态 | W2 动作 |
|----|---------|---------|
| Session SSE RFC | ✅ REVIEWED | 实施 S6–S8（B2+B3）；遵守 S1/S4/S7 |
| B1 StreamChannel | ✅ 500 / 2MiB + drop-oldest | B2 **不**仅因 offline buffer 驱逐就发 `gap`；journal-first |
| P1-4 E2E | ✅ | 本拍不扩 Learning |
| P1-C model guards | ✅ W0 | 回归护栏；发现再 hotfix |
| Turn Anchors RFC | DRAFT | Opt-C 只做 DOM helper；全量延后 |
| B4 wakeup | 未开 | #5 |
| Opt-D Gateway Restart | 延期声明 | #4 |

---

## 2. 项 #1 — B2+B3 Session SSE（主路径）

### 2.1 目标

把 REVIEWED 契约落到 **`GET /api/chat/stream`** + `messages.js`，使断线/重连/畸形 cursor 诚实、可测，且 **同里程碑**客户端消费 `session_snapshot`。

### 2.2 锚点

| 层 | 路径 |
|----|------|
| Handler | `webui/api/routes.py` — `_handle_sse_stream`, `_replay_run_journal`, `_parse_run_journal_after_seq`（malformed→0 **必须改**） |
| Cursor | `webui/api/runtime_adapter.py` — `_cursor_to_after_seq`（malformed→0 **必须改**） |
| Journal | `webui/api/run_journal.py` — `read_run_events` |
| Live id | `webui/api/streaming.py` + `STREAM_LAST_EVENT_ID` |
| 参考 | `webui/api/kanban_bridge.py` Last-Event-ID 链 |
| Client | `webui/static/messages.js` — `_lastRunJournalSeq`, reconnect, `_restoreSettledSession` |
| Tests | 新建 `tests/webui/test_session_sse_resume.py`（+ 必要前端契约单测若有 harness） |

**Breaking call sites（T2 钉死）**：`_parse_run_journal_after_seq` **与** `_cursor_to_after_seq` 今日均可能把垃圾 coerce 成 `0`；B2 二者都必须改为 `unknown_cursor` 路径。

### 2.3 B2 服务端行为（必须）

1. **Resume cursor 解析顺序**（S6）：query `after_seq` / `cursor` → `Last-Event-ID` →（见下「无 cursor」）。**Query 优先于** `Last-Event-ID`。
2. **无 cursor 时的服务端双分支**（澄清 RFC live-tail）：
   - **Live worker 在**：无 cursor = **live-tail-only**（今日多 tab 行为；**不是**可恢复契约）。
   - **Dead worker + 已知 `stream_id` + journal 存在**：允许从 run 起点 journal replay（今日 `_replay_run_journal`）；可恢复客户端仍应**始终**带 cursor（B3 规则），但服务端不得把「死 worker 无 cursor」误判为 live-tail 而拒绝回放。
3. **TOCTOU**（S3）：在 `end_headers()` 前 freeze cursor + journal baseline + 决策（replay / live / snapshot）。
4. **Journal-first**（S2/S9）：先 journal 回填；仅当无法证明 cursor 后连续 `seq` 时发 `session_snapshot(reason=gap)`。Offline buffer 驱逐** alone 不足** 触发 gap。
5. **Malformed / stale**（S7）：不可解析 cursor → `unknown_cursor`；run_id 冲突 / 不可附加 → `stale_run`；**禁止** coerce 到 `after_seq=0`。
6. **可选** `?session_id=`：解析 `active_stream_id` 后 attach 或 `no_active_run` / `journal_missing` snapshot。
7. **有界 journal 回放**（parity P0）：`_replay_run_journal` provisional caps（与 S9 同精神，可调）：
   - `max_replay_events = 2000`
   - `max_replay_bytes ≈ 8 MiB`
   - 超限且无法证明连续性 → `gap`（禁止静默截断当成功）。
8. **Idle / 终态策略**：`no_active_run` / `journal_missing` / 需 `messages_reload` 的 snapshot（含通常的 `gap` / `unknown_cursor` / `stale_run`）后 **关闭** SSE 连接；不 hold 等下一 run。客户端必须配合 §2.4。
9. **Feature flag**：**不**引入长期双路径。直接演进 `GET /api/chat/stream`；不设 `webui.session_sse_v1` 常驻开关（若需紧急回滚，用 git revert，不留永久 fork）。

### 2.4 B3 客户端行为（必须，同里程碑）

1. 保留 JS `_lastRunJournalSeq` 镜像；依赖浏览器自动 `Last-Event-ID`（仅用于**非终态**断线）+ 手动重连时显式 `cursor`/`after_seq`。
2. 处理 `event: session_snapshot`：
   - `messages_reload` → refetch `/api/session`（或等价）
   - 若 `active_stream_id` 且 reason 允许继续：opt-in 再 attach（**新** EventSource；空/新 cursor 策略按 reason）
   - 否则 idle
3. **终态 / 诚实缺口 — 强制**（C1）：收到 `session_snapshot` 且 reason ∈ `{no_active_run, journal_missing}`，或 reason ∈ `{gap, unknown_cursor, stale_run}` 且已决定走 REST reload：
   - 调用 `es.close()`
   - 置位 **reconnect-suppress latch**（禁止 `onerror` 自动/手动再连同一终态 cursor）
   - **非目标**：依赖浏览器对已关闭连接的自动重连
4. Generation / session-switch 护栏不变（#1366）；禁止跨 run 静默拼接。
5. 重连后 follow-intent 与 P1-B MVP（#2）对齐——#1 至少不破坏现有 `_shouldFollowMessagesOnDomReplace`；scroll 硬化细节归 #2。

### 2.5 测试矩阵（项 #1 门槛）

| # | 场景 | 断言 |
|---|------|------|
| T1 | `Last-Event-ID` ≡ `after_seq`/`cursor` | 回放起点一致 |
| T2 | malformed cursor（两 parser） | `session_snapshot` `unknown_cursor`；**不**全量 replay |
| T3 | stale run_id in cursor | `stale_run`；不应用到当前 run |
| T4 | journal 连续 + live | journal fill 后 live attach；无假 gap |
| T5 | journal 缺口 | `gap` + `messages_reload` |
| T6 | 无 active run + session_id | `no_active_run` |
| T7 | 回放超 cap | `gap`（非静默截断） |
| T8 | 客户端 snapshot → reload | reload 路径触发；不双渲染 token |
| T9 | offline-buffer drop，journal 仍连续 | **不**发 `gap` |
| T10 | query `after_seq`/`cursor` vs `Last-Event-ID` 同时存在 | **query 胜出** |
| T11 | TOCTOU | freeze 在 `end_headers()` 之前（单测或契约探针） |
| T12 | 终态 / idle snapshot | `es.close()` + reconnect latch；**无**重连环 |

### 2.6 非目标（#1）

- 不改名/占用 `GET /api/sessions/events`（list SSE）
- 不引入平行 SessionChannel
- 不双写 activity scene（S5）
- 不宣称 P0 完成（缺 B4）
- 不依赖浏览器在终态 snapshot 后自动重连

### 2.7 风险与缓解

| 风险 | 缓解 |
|------|------|
| malformed→0 breaking（两 call site） | T2 钉死两端；RFC 已声明 |
| EventSource 自动重连环 | §2.4.3 + T12 |
| 超大 journal OOM | T7 + provisional caps |
| 只合服务端 | PR checklist：无 B3 不 merge |
| 无 cursor 误判 | §2.3.2 双分支 + 死 worker 回放保留 |

---

## 3. 项 #2 — P1-B MVP transcript 硬化

### 3.1 目标

硬化现有 **尾窗 50** + SSE `done`/重连 scroll + 用户行高度记忆；**不做** `#msgInner` spacer 全量 virt。

### 3.2 锚点

| | 路径 |
|---|------|
| Window | `webui/static/ui.js` — `MESSAGE_RENDER_WINDOW_*`, `_messageHiddenBeforeCount` |
| Scroll | `ui.js` — `_shouldFollowMessagesOnDomReplace`, `_scrollAfterMessageRender` |
| SSE | `messages.js` — `done` / reconnect / `session_snapshot` reload |
| Load older | `sessions.js` — expand window + `preserveScroll` |

### 3.3 MVP 范围

1. 统一 SSE 终态 / 重连 / snapshot-reload 后的 follow-intent（与 #1 snapshot 路径对接）。
2. 用户消息行 `ResizeObserver`（或等价）高度缓存 Map；load-older / render 时用于减少跳动。**仅**用户行 Map — **无** spacer 高度账本。
3. HTML cache key 含 window bounds（防错缓存）。
4. jump-to-question / compression_anchor 在尾窗下仍可用。

### 3.4 验收行

| # | 验收 |
|---|------|
| B-A1 | `done` + 近底部 → 仍 follow；钉住阅读 → 不抢滚动 |
| B-A2 | snapshot reload 后 follow-intent 与 `done` 一致 |
| B-A3 | load-older 后 scroll 位置稳定（preserveScroll） |
| B-A4 | cache key 含 window bounds（错窗不命中旧 HTML） |
| B-A5 | jump-to-question / compression_anchor 手工清单通过 |

### 3.5 非目标

- `_sessionVirtualWindow` 迁到 `#msgInner`
- spacer 高度账本 / 全量 virt
- 改变默认窗大小（保持 50，除非有测量依据）

---

## 4. 项 #3 — Opt-C live→settled helper

### 4.1 目标

实现注释中缺失的 `_convertLiveActivityGroupToSettled`（或等价命名），用稳定 `activityKey` 把 live DOM 转为 settled，减少 `clearLiveToolCards` + 全量 `renderMessages` 闪烁。

### 4.2 约束

- **禁止**服务端 scene / journal 双写；**禁止**发明 journal 字段
- DOM key：`live:{streamId}` → `assistant:{idx}`（现有 disclosure 路径）
- 失败则 fallback 现有 `clearLiveToolCards` + render（行为不劣于今日）

### 4.3 验收行

| # | 验收 |
|---|------|
| C-A1 | `done` 优先走 convert；成功则减少对 live 节点的整树清除闪烁（目视或 DOM 探针） |
| C-A2 | 用户手动展开的 live group 状态保留（`#1298`） |
| C-A3 | convert 抛错 → fallback 与今日一致 |

---

## 5. 项 #4 — Opt-D 同机 Gateway Restart

### 5.1 目标

同机部署下：健康 banner **Restart** + `POST /api/health/restart`，调用与 CLI 一致的 profile/root 作用域；**且** agent 更新路径证明 gateway 已重启（DECIDED #4）。

### 5.2 必须遵守（parity DECIDED）

1. **#1** 跟随活动 profile / `INTELLECT_HOME`；文案不得暗示「只重启当前会话」。
2. **#4** agent 更新成功路径若宣称 gateway 已重启，必须硬失败于未证明重启（`updates.py` 挂钩）— **进本项 DoD，非可选**。
3. 与 `gateway_watcher` **分离**（watcher ≠ messaging gateway）。
4. 认证 + CSRF/同源；不回传原始 stderr 到浏览器。

### 5.3 形态

```
webui/api/gateway_lifecycle.py
  resolve intellect CLI → INTELLECT_HOME / --profile
  非阻塞锁；快返回 in_progress；后台 drain
  状态：completed | in_progress | busy | failed

POST /api/health/restart  + #agentHealthBanner Restart 按钮
updates.py target=agent 成功路径：未证明 restart → 硬失败（busy/failed）
```

### 5.4 验收行

| # | 验收 |
|---|------|
| D-A1 | 未认证 / CSRF 失败 → 4xx；无副作用 |
| D-A2 | 同机成功路径 → `completed` 或可轮询的 `in_progress`→`completed` |
| D-A3 | 锁冲突 → `busy`；探测失败 → `failed` + 短 message（无 raw stderr） |
| D-A4 | banner Restart 调用同一 API |
| D-A5 | `updates.py` agent 目标：gateway 未证明重启 → 更新失败 |

### 5.5 非目标

- 分容器假成功伪装（探测失败 → `failed` + 诚实文案）
- Journey P1-3

---

## 6. 项 #5 — B4 wakeup / credential-exhaustion pause

### 6.1 目标

凭证耗尽 / rate-limit 不可恢复时：可审计暂停 + **禁止**同 model/provider 空转自动 wakeup；成功 run / 换模型 / 凭证变化后 clear。

### 6.2 SoT 预锁（开 #5 前不再摇摆；不挡 #1–4）

| 部件 | 拍板 |
|------|------|
| **存储** | **Profile-home 级**文件：`{INTELLECT_HOME}/webui/wakeup_pause.json`（单活动 home；多 session 共享同一 fingerprint 门禁） |
| **Fingerprint** | `provider` + `model`（规范化小写）；可选 `credential_fingerprint` 若 pool 可稳定导出 |
| **门禁** | chat/start（及等价 `start_session_turn`）：paused 且 fingerprint 匹配 → HTTP/JSON `403`/`409` + `code: wakeup_paused`；若已有 live stream 则另发 SSE `process_wakeup_paused` |
| **Clear** | 成功终态 run；用户换 model/provider；pool 凭证变化检测；显式 `POST` resume（若做 UI） |
| **SSE 载荷（最小）** | `{ "v": 1, "reason": "credential_exhausted"\|"rate_limit", "provider": "...", "model": "...", "paused_at": <unix> }` |

### 6.3 与 #1 的关系

- **不**阻塞 B2+B3 合入
- P0 完成宣称 = #1 + #5（B1 已在 W1）

### 6.4 验收

- 耗尽后不再自动 wakeup 同 fingerprint
- clear 条件可测
- WebUI 有可感知状态（banner 或 `process_wakeup_paused` 至少其一）

---

## 7. 依赖与并行边界

```text
#1 B2+B3 ──────────────────────────► 宣称「可恢复 SSE」
         │
#2 P1-B MVP ── 可紧随 #1（scroll/snapshot 对接）
#3 Opt-C ───── 独立；宜在 #1 稳定后减闪烁
#4 Opt-D ───── 运维；本拍串行排在 #3 后（覆盖 DECIDED #3 并行建议）
#5 B4 ──────── 独立；完成后宣称 P0
```

用户要求 **严格 1→5 顺序执行**（本拍遵守）。若中途 #4 运维紧急，可例外插队，但须书面记录。

---

## 8. 计划评审清单（签字用）

| ID | 问题 | 建议答案 | 签 |
|----|------|----------|----|
| R1 | B2+B3 可否拆 PR？ | 可拆，**同 release train**；禁止只合 B2 | ☑ |
| R2 | malformed→0 breaking？ | **是**；两 parser；按 RFC 改 snapshot | ☑ |
| R3 | idle 后 hold vs close？ | **close** + B3 reconnect suppress（§2.3.8 / §2.4.3） | ☑ |
| R4 | P1-B 是否依赖 B2+B3？ | MVP **不**硬依赖；本拍排在 #1 后便于对接 snapshot scroll | ☑ |
| R5 | Opt-C 写服务端 scene？ | **否**；不发明 journal 字段 | ☑ |
| R6 | Opt-D 作用域？ | 活动 profile / INTELLECT_HOME；DECIDED #1/#4 **均进 DoD** | ☑ |
| R7 | B4 绑死 SSE RFC？ | **否**；独立事件 + §6.2 SoT | ☑ |
| R8 | W2 后宣称 P0？ | 仅当 #1+#5 完成 | ☑ |
| R9 | Feature flag？ | **否**长期双路径；直接演进 chat/stream | ☑ |
| R10 | 回放 caps？ | provisional 2000 / 8MiB | ☑ |

---

## 9. 文档历史

| Date | Note |
|------|------|
| 2026-07-12 | DRAFT v1 — 用户确认串行 1–5；技术评审入口 |
| 2026-07-12 | 技术评审 **Request changes**（C1 idle 重连环；C2 Opt-D DECIDED #4；I1–I7） |
| 2026-07-12 | 修订 → **REVIEWED（可执行）**：C1/C2 + T9–T12 + 无 cursor 双分支 + 双 parser + caps + flag 拍板 + B4 SoT + #2–4 验收行 |
| 2026-07-12 | 质量/安全评审 Request changes → 修复：`subscribe_after_seq` 去重、B4 SSE/UI/fingerprint clear、`get_active_intellect_home`、gateway prove 先于 WebUI restart、Opt-C 降为 disclosure-only |
| 2026-07-12 | 完善遗留：`iter_run_events` 流式回放、D-A1 auth/CSRF 断言、`--profile` 用 `get_active_profile_name`、用户行 minHeight 清理 |
