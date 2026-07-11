# W3 细化稿 — Turn Anchors P1-A（RFC 定稿 → MVP 收口 → journal scene 首切片）

> **日期**：2026-07-12  
> **状态**：**APPROVED** — 2026-07-12 用户 Approve；按 0→3 执行中（**#1 RFC REVIEWED 已完成**）  
> **策略**：先技术评审，再按 **0→3** 串行（项 0 为合入门槛，非功能特性）  
> **前置**：W2 功能已落地（B2+B3 / P1-B MVP / Opt-C / Opt-D / B4），**工作树仍未合入** — 见 §0  
> **父文档**：[`2026-07-12-w2-session-sse-and-parity-backlog.md`](./2026-07-12-w2-session-sse-and-parity-backlog.md)、[`2026-07-11-p1-journey-and-webui-parity-refinement.md`](./2026-07-11-p1-journey-and-webui-parity-refinement.md)、[`2026-07-11-webui-hermes-parity-analysis.md`](./2026-07-11-webui-hermes-parity-analysis.md)  
> **契约 SoT**：[`docs/webui/rfcs/stable-assistant-turn-anchors.md`](../webui/rfcs/stable-assistant-turn-anchors.md)（**REVIEWED** — 2026-07-12；项 #1 已定稿）  
> **SSE 依赖**：[`docs/webui/rfcs/session-sse-contract-v1.md`](../webui/rfcs/session-sse-contract-v1.md)（**REVIEWED**；W2 已实施客户端）  
> **评审**：[`code-reviewer`](8eb6a4a5-9b73-4594-9ae6-cdbfc91ba267) — Verdict **Request changes** → 本修订锁定 C1–C3 / I1–I8

---

## 0. 为何是「下一个」

父计划 §2.5：

```text
P0 Session SSE (契约+客户端) ──► P1-A 全量 / P1-B 全量 virt
```

| 轨 | W2 后状态 | W3 取舍 |
|----|-----------|---------|
| P0 SSE + B4 | ✅ 代码就绪（待合入） | **可宣称 P0**（合入后） |
| P1-A Opt-C | ✅ disclosure convert（无 scene） | **本拍主路径** |
| P1-A RFC | **REVIEWED**（A1–A8 + wire envelope 已锁） | **项 #1 完成**；#2/#3 按冻结 schema 执行 |
| P1-B 全量 virt | MVP 已硬化 | **不进 W3 DoD**（跟 P1-A MVP 后） |
| Journey P1-3 | 仍延后 | **不进 W3 DoD** |

**本拍不做**：spacer 全量 virt、平行 activity journal、改 agent prompt-cache、Hermes 整包移植、完整 `transparent_stream` UI、Idle-deferred worklog 实现。

**S5 对齐**：Session SSE S5 禁止 *本 RFC* 强制每帧 scene 双写；明确允许 P1-A **稍后**在同一 `run_journal` 追加可选行。W3 A1/A2 = 该可选路径的首切片，**不是** S5 违约。

---

## 0.1 项 #0 — 合入门槛（执行任何功能前）

| | 内容 |
|---|------|
| **动作** | 将 W2 未提交变更整理为可审 PR（可 1–2 个 commit：SSE+B4 / Opt-C+P1-B+Opt-D）并 **合入 main（或 release 基线）** |
| **DoD（硬）** | **#3 开始前**：W2 **已 merge** 到 main/release；CI 绿；Session SSE + wakeup + restart 冒烟通过 |
| **DoD（软）** | **#1 文档-only** 允许在 W2 未 merge 时起草/合入（纯 RFC） |
| **#2 门禁** | 允许在 W2 **已 push 可审分支**上开发，但 **合入 #2 前** W2 应已 merge（避免混杂 diff） |
| **原因** | P1-A journal 双写叠在未合入 SSE 面上会制造不可回滚的混杂 diff |

---

## 1. 评审摘要（目标形态）

| # | 项 | 估时 | 合入形态 |
|---|----|------|----------|
| **0** | W2 **merge** 到 main/release | 0.5d | 前置硬门禁（对 #3） |
| **1** | Turn Anchors RFC **DRAFT → REVIEWED** | 0.5–1d | 文档 PR；可先于 W2 merge |
| **2** | **P1-A MVP 收口**（localStorage scene + display alias） | 2–4d | 功能 PR；**仍不**写 journal scene；schema 冻结见 §1.2 |
| **3** | **P1-A journal scene 首切片**（scene→terminal 双写 + replay） | 3–5d | 功能 PR；依赖 **#0 merge + #1 REVIEWED** |

**串行**：0 → 1 → 2 → 3。  
**#3 禁止**在 RFC 未 REVIEWED 或 W2 未 merge 前合入。

### 1.1 W3 Definition of Done

| 必须 | 明确不在 W3 DoD |
|------|-----------------|
| #0 W2 **已 merge** | P1-B spacer 全量 virt |
| #1 RFC → **REVIEWED**（A1–A8 + R 表签字） | Journey P1-3 |
| #2 inflight 含有界 `activity_scene_v1`；display alias；中刷新 Activity 不丢 disclosure | 完整 `transparent_stream` UI |
| #3 terminal 前一条 `activity_scene`（同 `run_journal` + `put`）；replay 可重建 Activity（无重复） | Idle-deferred worklog 全量（可只定 N 阈值） |
| A-M5 / A-M6（toggle + 长 turn scroll）进 #2 DoD | C-A1 闪烁减量（可选增强，不阻塞 #3） |

### 1.2 Schema 冻结规则（I2）

| 阶段 | 规则 |
|------|------|
| **#1** | RFC 升格 A3 **最小字段集** + wire envelope（§4.0）；Status → REVIEWED |
| **#2** | 仅实现 RFC 中已冻结的 A3 子集；**同一 PR** 须含 RFC 中该子集的最终文案（若 #1 尚未 REVIEWED，#2 不得合入——或 #2 与 #1 同 train 且 #1 先 merge） |
| **#3** | 必须等 #1 全量 REVIEWED；服务端 scene 遵守同一 A3 + §4 线序 |

禁止：#2 先发明 localStorage 字段名，#3 再改 wire 名导致双实现。

---

## 2. 项 #1 — RFC 定稿（锁定开放题）

更新 `docs/webui/rfcs/stable-assistant-turn-anchors.md`：

| ID | 议题 | **锁定答案**（本修订） |
|----|------|----------------------|
| **A1** | Scene 落点 | **run_journal** 同行 cursor（与 SSE S1 一致）；wire `event:` = `activity_scene`；**禁止**平行 log |
| **A2** | 何时双写 | **仅 terminal 路径一次**：先 `activity_scene`，再 `done`/`cancel`/`apperror`/`stream_end`（见 §4.3.1）；live 中只更新 inflight localStorage |
| **A3** | Payload | 见 §4.0 wire；最小集：`v,turn_id,stream_id,session_id,mode,display,disclosure,segments[],elapsed_ms` |
| **A3a** | 作者（C2） | **服务端**从 journal 已写 tool/thinking/text 组 segments；`disclosure` **默认** `{expanded:false,user_intent:null}`；客户端真实 disclosure **仍仅 localStorage**（W3 不上传） |
| **A4** | Segment cap | provisional **max_segments=40**，drop-oldest tool/thinking（保留最新 text） |
| **A5** | Deferred worklog | W3 **只文档** `N≥8`；实现 W4 |
| **A6** | Display alias | `chat_activity_display_mode`: `compact_worklog`\|`transparent_stream` → 映射 `simplified_tool_calling` |
| **A7** | Opt-C 范围 | convert = **disclosure remap**（W2 已有）；**C-A1 闪烁**属 #2 可选，非 RFC / #3 阻塞 |
| **A8** | Seq / 终端性（R4） | Scene **推进** journal/SSE `seq`；`terminal: false`；**不得**进 `_TERMINAL_SSE_EVENTS`；不得当 `latest_run_summary` 终端 |

**#1 DoD（必须改 RFC 正文）**：

1. Status → **REVIEWED**  
2. 重写 §1 / §5：删除「`_convertLiveActivityGroupToSettled` does not exist」；写明 W2 已实现 disclosure remap，MVP #1 变为 localStorage scene + alias  
3. §3 sketch → v1 契约 + §4.0 wire envelope  
4. 交叉引用 Session SSE **S5** + §4.4：本 RFC 是「later optional rows」，非每帧强制  
5. Companion 行保持：「Session SSE 不强制 scene」；Turn Anchors 自身定义何时写入  

---

## 3. 项 #2 — P1-A MVP 收口（客户端）

### 3.1 目标

让 **live / inflight / settled** 共享同一 `activity_scene_v1` 内存/localStorage 表示；中刷新与切会话不丢 Activity disclosure / tool 计数骨架。

### 3.2 锚点

| 层 | 路径 |
|----|------|
| Inflight | `webui/static/ui.js` — `saveInflightState` / `loadInflightState` / `_compactInflightState` |
| Activity | `ensureActivityGroup`, `_convertLiveActivityGroupToSettled`, disclosure keys |
| Done | `webui/static/messages.js` — `done` 路径 |
| Config | `webui/api/config.py` + `boot.js` + `panels.js`（`simplified_tool_calling`） |
| 测试 | **主门禁**：`tests/webui/test_activity_scene_contract.py`（序列化 / cap / alias 映射，纯 Python）；DOM harness 可选、不阻塞 CI |

### 3.3 必须行为

1. **构建 scene**：从 live DOM / 进行中 toolCalls + thinking 文本组装 `segments[]`（kind: thinking|tool|text）。  
2. **写入 inflight**：`saveInflightState` 增加 `scene` 字段（与 A3 同形）；`_compactInflightState` 应用 A4 cap。  
3. **恢复**：`loadInflightState` / session 切回时，若有 scene 则优先按 scene 重建 Activity（fallback 今日 messages/toolCalls）。  
4. **Display alias**：读 `chat_activity_display_mode`；未设则从 `simplified_tool_calling` 推导。  
   - **写路径双写**：一拍内同时写旧 key + 新 alias。  
   - **退出标准（I7）**：alias 合入后 **一个 release** 停写旧 key；**读路径永久**接受两者（旧→新推导）。  
5. **（可选增强）Settle 闪烁**：convert 成功且 messages 已对齐时，推迟或跳过一次全量 wipe — **失败则 fallback 今日路径**；**不进 W3 DoD 表**。  
6. **Toggle（A-M5）**：`chat_activity_display_mode` / simplified 切换在 live **与** settled Activity 上立即生效，**无需**整页 reload。  
7. **长 turn scroll（A-M6）**：≥20 tools 的 turn 在 settle 过程中保持既有 `_scrollAfterMessageRender` / open-tool 签名稳定（不劣于 W2）。

### 3.4 验收行

| # | 验收 |
|---|------|
| A-M1 | Mid-stream refresh：Activity 展开态与 tool 数与刷新前一致（localStorage scene） |
| A-M2 | 快速切会话再切回：无跨 session Activity（#1366） |
| A-M3 | `chat_activity_display_mode=compact_worklog` ≡ 今日 simplified on |
| A-M4 | Cap：>40 segments 时 oldest tool/thinking 被丢且 inflight 仍可写 |
| A-M5 | Display mode toggle：live + settled 无需 full reload |
| A-M6 | 20+ tools：settle 过程 scroll 稳定（不劣于 W2） |

### 3.5 非目标（#2）

- 不写 `run_journal` scene 行（归 #3）  
- 不实现 `transparent_stream` 全 UI  
- 不上传 disclosure 到服务端  

---

## 4. 项 #3 — journal scene 首切片（服务端 + replay）

### 4.0 Wire envelope（N3 — 锁定）

```text
SSE / journal row:
  event: activity_scene
  id:   <seq>          # 推进 cursor；非控制帧
  data: <JSON = activity_scene_v1 object 本体>
        # 即 data 根就是 scene，不是 { "scene": {...} }
```

- `v: 1` 在 scene 对象内。  
- 旧客户端：未知 event → 忽略（与今日未知 SSE 一致）。  
- 与 `session_snapshot` **不同**：snapshot 无 journal seq；scene **有** seq。

### 4.1 目标

在同一 `run_journal`、经同一 `streaming.put()` 路径，于 terminal 事件 **之前** 写入一条 `activity_scene`，replay / 死 worker 回放时用 scene 重建 Activity，避免仅靠扁平 tool SSE 猜树。

### 4.2 锚点

| 层 | 路径 |
|----|------|
| Writer | `webui/api/streaming.py` `put()` — **与其它 durable 事件同路径**（journal append + channel + offline buffer） |
| Builder | 服务端从本 stream 已 journal 的 tool/thinking/text 组装 segments（A3a）；disclosure 默认 |
| Reader | `run_journal.iter_run_events`；`session_sse` replay |
| Client | `messages.js` — 与 #2 共用 `applyActivityScene(scene, {source})` |
| 测试 | `tests/webui/test_activity_scene_journal.py`（线序、seq、非终端、截断、旧 journal） |

### 4.3 必须行为

#### 4.3.1 线序（C1 — 锁定）

```text
… tool* → build scene → put(activity_scene) → put(done|cancel|apperror|stream_end)
```

| 规则 | 说明 |
|------|------|
| **禁止**「terminal 成功后再写 scene」 | live 客户端在 `done` 后常 `_streamFinalized`，晚到的 scene 无法应用 |
| **禁止** MVP 把 scene 嵌进 `done` payload | 与「可选 journal 行」模型冲突；若未来要嵌，另开 RFC 修订 |
| Scene 上 `turn_id` | 暂用 `live:{stream_id}`；`done` 后客户端 remap disclosure → `assistant:{idx}`（W2 已有路径） |
| Cancel / apperror（I8） | **必须**仍写恰好 1 条 scene；`mode: interrupted`（或等价）；segments 可为截断/不完整 |

#### 4.3.2 发射与消费（C3 — 锁定）

| 路径 | 行为 |
|------|------|
| **写入** | 必须 `put(event="activity_scene", data=scene)` → `append_sse_event` + StreamChannel + offline buffer |
| **Live 已 finalized** | 客户端 `_streamFinalized` 时 **忽略** 迟到的 live `activity_scene`（不应发生若线序正确；防御） |
| **Replay / 死 worker** | 见 `activity_scene` → `applyActivityScene`；见 §4.3.4 状态机 |
| **Journal-only 追加** | **禁止**（会破坏 Last-Event-ID / gap / offline buffer 统一） |

#### 4.3.3 Seq / 体积（I1 — R4 展开）

| 规则 | 说明 |
|------|------|
| 推进 `seq` | **是**（R4）— durable SoT，非 `session_snapshot` 类控制帧 |
| 非终端 | 不进 `_TERMINAL_SSE_EVENTS`；不关闭 stream；不更新 terminal summary |
| 截断 | 超限时 **shrink segments/detail**，**永远保留该 seq 行**（禁止为省字节跳号 → 假 gap） |
| 双 cap | 单事件 ≤ StreamChannel `DEFAULT_MAX_BYTES`（**2 MiB**）；整段 replay 仍受 `MAX_REPLAY_EVENTS`（2000）+ `MAX_REPLAY_BYTES`（**8 MiB**）约束 |

#### 4.3.4 Replay 状态机（I3 — 锁定）

共享 `applyActivityScene`（#2 已引入）：

```text
on activity_scene(stream_id):
  if sceneAppliedForStream[stream_id]: idempotent no-op (or replace-in-place once)
  else: build Activity tree from segments; set latch sceneAppliedForStream[stream_id]=true

on tool | tool_complete | thinking (same stream, replay window):
  if latch set: ignore for Activity tree construction
  else: legacy flat rebuild (旧 journal 无 scene)

on done | cancel | apperror:
  settled path / disclosure remap live:{id} → assistant:{idx}
  NEVER create a second live Activity group for this stream
```

验收核心：**禁止**「scene 树 + 扁平 tool 再渲一套」。

#### 4.3.5 兼容

无 `activity_scene` 的旧 journal → 行为与今日扁平 replay 相同（latch 永不置位）。

### 4.4 验收行

| # | 验收 |
|---|------|
| A-J1 | 每个 stream 在 terminal **前** journal 含恰好 1 条 `activity_scene`（含 cancel/apperror） |
| A-J2 | 死 worker + after_seq：Activity 不重复；settle 后 disclosure key = `assistant:{idx}` |
| A-J3 | 旧 journal（无 scene）：回归不破 |
| A-J4 | 超大 scene 截断后仍可 parse；seq 连续；不触发假 `gap` |
| A-J5 | Scene 事件不进 `_TERMINAL_SSE_EVENTS`；channel 不因 scene 关闭 |

### 4.5 非目标（#3）

- Live 每 tool 都写 scene  
- `requestIdleCallback` deferred worklog（W4）  
- P1-B virt  
- 客户端上传 disclosure / scene（另开切片）  

---

## 5. 依赖与风险

```text
#0 W2 merge ──► #1 RFC REVIEWED ──► #2 localStorage MVP ──► #3 journal 首切片
     │                │                    │
     │                └── 可先于 #0 合入文档
     │                    #2 合入需 schema 冻结（§1.2）
     └── #3 硬依赖 merge + REVIEWED
```

| 风险 | 缓解 |
|------|------|
| Scene 撑爆 StreamChannel / replay | A4 + 2 MiB / 8 MiB 双 cap；截断保 seq |
| replay 双渲染 | §4.3.4 latch；测 A-J2 |
| RFC 未锁就双写 | #3 门禁 + §1.2 |
| W2 未合入叠 diff | #0 merge 硬门禁 |
| Scene 写在 done 后 | §4.3.1 线序锁死 |
| 服务端猜不准 disclosure | A3a：默认 disclosure；真实态仅 localStorage |

---

## 6. 计划评审清单（签字用）

| ID | 问题 | **锁定答案** | 签 |
|----|------|--------------|----|
| R1 | W3 主路径？ | P1-A（非 P1-B virt / 非 Journey P1-3） | ☐ |
| R2 | Scene 落 run_journal？ | **是**（A1） | ☐ |
| R3 | 双写频率？ | **仅 terminal 路径一次**（A2） | ☐ |
| R4 | Scene 是否推进 SSE seq？ | **是**；非终端；截断保行；≠ snapshot 控制帧 | ☐ |
| R4a | Scene vs done 顺序？ | **scene → terminal**（禁止 after-done） | ☐ |
| R4b | 谁写服务端 scene？ | **服务端** segments；disclosure 默认 | ☐ |
| R4c | 发射路径？ | **同 `put()`**（禁止 journal-only） | ☐ |
| R5 | transparent_stream？ | W3 不做全 UI；仅 alias | ☐ |
| R6 | Deferred worklog？ | W3 只锁 N≥8；实现 W4 | ☐ |
| R7 | #0 / #3 门禁？ | #3 前 W2 **必须 merge**；#1 可文档先行 | ☐ |
| R8 | #2 schema？ | 仅冻结 A3；与 #1 同 train 或等 REVIEWED | ☐ |

---

## 7. 技术评审记录

| Date | Verdict | 摘要 |
|------|---------|------|
| 2026-07-12 | **Request changes** | C1 线序未锁；C2 作者未锁；C3 发射路径未锁；I1–I8 / N3 需写入计划 |
| 2026-07-12 | **REVISED** | 上表已吸收；**待产品/工程 Approve 后执行**（本拍不自动开工 #1–#3） |

### 7.1 评审要点（已关闭）

- **R4 类决策正确**：scene 推进 seq；控制帧无 id 仅适合 `session_snapshot`。  
- **S5 / 范围 / 串行**：原方向正确，细节不够。  
- **执行前 checklist**（原评审）：C1–C3、I1–I8、N3 均已写入本文件对应章节。

---

## 8. 文档历史

| Date | Note |
|------|------|
| 2026-07-12 | DRAFT v1 — W2 后下一拍：Turn Anchors P1-A |
| 2026-07-12 | REVISED v2 — 吸收 tech review Request changes；锁定线序/作者/put/门禁/状态机 |
| 2026-07-12 | 项 #1 完成 — Turn Anchors RFC → **REVIEWED**；Session SSE S5/§4.4 companion 澄清 later optional rows ≠ S5 违约 |
|
