# W6 细化稿 — Transparent Stream 全 UI（chronological cockpit）

> **日期**：2026-07-12  
> **状态**：**EXECUTED** — 2026-07-12；合入 main @ `609f705`（PR [#45](https://github.com/ontoweb-cn/intellect-agent/pull/45)）  
> **下一拍**：[`2026-07-12-w7-p2-security.md`](./2026-07-12-w7-p2-security.md)  
> **策略**：先技术评审，再按 **0→2** 串行  
> **前置**：W2–W5 已合入 main（SSE、scene、virt、deferred worklog @ `d6a94d7`）；计划合入 @ `9e7eab0`  
> **契约**：[`docs/webui/rfcs/stable-assistant-turn-anchors.md`](../webui/rfcs/stable-assistant-turn-anchors.md) **A6**（alias 已落地）+ Hermes RFC 方向（`hermes-webui` `transparent-stream-activity-mode.md`）  
> **探索**：[`explore`](0599696e-6d26-456d-8aa9-8a13c39b7f11)  
> **计划评审**：[`code-reviewer`](42974198-bb9d-40e8-bc92-290dd46e9684) Request changes → REVISED；[`确认评审`](6024d818-839c-4db8-9b2b-5da8bec0620b) **Approve**

---

## 0. 为何是「下一个」

| 轨 | 现状 | W6 取舍 |
|----|------|---------|
| P1-A scene + Opt-C + deferred worklog | ✅ | **本拍：真 `transparent_stream` 渲染缝** |
| P1-B virt | ✅ canary off | 共存：测高；**不**做 transparent 专属 virt 优化 |
| `transparent_stream` | A6 alias + legacy `!simplified` flat | **本拍主路径** |
| Journey P1-3 | 仍挂 profile | **不进** |
| P2 anchored I/O / trusted-proxy | backlog | **不进** |

**问题（与 Hermes #3820 同构）**：

1. 今日「关 Compact tool activity」≈ 扁平行 tool-card，**不是** chronological cockpit。
2. `applyActivityScene` **承认** `scene.display` 但不改渲染器——一律走 `isSimplifiedToolCalling()` 分支。
3. Settle / reload：thinking 与 tools **分桶**；done 时无 Activity → Opt-C 失败 → `clearLiveToolCards()` 闪烁。
4. Live：`#thinkingRow` 默认 open 可把 running tool 顶出视口。

---

## 1. 代码现实（锚点）

| 事实 | 路径 |
|------|------|
| Mode predicate | `ui.js` `isSimplifiedToolCalling` / `chatActivityDisplayMode` |
| Scene apply 忽略 display 渲染 | `ui.js` `applyActivityScene` |
| Settled compact vs flat | `ui.js` `renderMessages` settled tool loop（`ensureActivityGroup` vs flat `buildToolCard`） |
| Live append | `ui.js` `appendLiveToolCard` / `appendThinking` |
| Done Opt-C | `messages.js` done handler（`_convertLiveActivityGroupToSettled` → else `clearLiveToolCards`） |
| W5 门控 | `_shouldDeferActivityWorklog` → **compact only**（D-M11） |
| Server segments 序 | `webui/api/activity_scene.py` → `segments[]`；journal 在 terminal 前写 `activity_scene` |

---

## 2. 评审摘要

| # | 项 | 估时 | 合入 |
|---|----|------|------|
| **0** | 锁 TS1–TS8 + C1–C4（本文件 §3） | 0.5d | 文档 / PR 头 |
| **1** | Transparent renderer seam MVP | 4–6d | 功能 PR |
| **2** | virt-on / scene / W5 / Opt-C 回归 | 1–2d | **同 train** |

### 2.1 W6 Definition of Done

| 必须 | 不在 W6 DoD |
|------|-------------|
| `chatActivityDisplayMode()==='transparent_stream'`（或 `isTransparentStream()`）驱动 **live + settled + inflight + replay** 渲染 | Hermes 整包 `assistant_turn_anchors.js` / `activity_rows` schema |
| **Hermes 准则 1、3、4 + 准则 2 的 thinking+tool 子集**（见 TS2 / C4）可测 | 完整 progress/`text` 交错（R3） |
| Preference **始终**选 renderer；`scene.display` = 元数据戳（C2） | Journey P1-3 / Gateway / SessionChannel |
| Transparent **不**建 `.tool-call-group`；W5 D-M11 不回归 | C-A1 闪烁彻底消灭 |
| Toggle（A-M5）无整页 reload | Settings segmented control |
| 默认仍 **compact_worklog**；transparent opt-in | Agent prompt-cache / 平行 journal |
| Settled 序：有 scene 时按 `segments[]`（C1）；无 scene → legacy flat | Transparent 专属 virt 优化 |

---

## 3. 锁（TS1–TS8 + 评审 C/I）

| ID | 锁 | 决议 |
|----|-----|------|
| **TS1** | 准则 1 | Transparent：**禁止** Activity 汇总壳；每 tool = 一流 `.tool-card-row` |
| **TS2** | 准则 2（**部分**） | Thinking + tool **按执行序**交错。Progress / `text` segment **不**进 DOM（R3 / C4）。权威序见 **C1** |
| **TS3** | 准则 3 | Tool 默认 collapsed compact preview（`buildToolCard`） |
| **TS4** | 准则 4 | Live → done → reload → inflight：**同一结构类集**（flat rows，无 worklog）。**范围**：有 scene 可用时（C1）；无 scene 旧会话 = legacy flat，不宣称 TS4 |
| **TS5** | Predicate | 渲染门控：`isTransparentStream()` ≡ `chatActivityDisplayMode()==='transparent_stream'`。Compact 可继续用 `isSimplifiedToolCalling()`。必改点：`appendLiveToolCard` / `appendThinking` / settled tool loop / `applyActivityScene` / done 分支 |
| **TS6** | Done（**唯一算法**，C3） | 若 transparent：（1）**不**把 `_convertLiveActivityGroupToSettled` 当成功路径；（2）**跳过** `clearLiveToolCards()`；（3）仅 strip `#liveAssistantTurn` id / live attrs（若仍挂着）；（4）交 `renderMessages` 在 `!S.busy` 路径 wipe+重建。残闪非阻塞（C-A1 仍 out of DoD） |
| **TS7** | Scene vs preference（C2） | **Preference 始终选 renderer**。`scene.display` 只作 journal 戳；apply **不**因 scene 覆盖用户偏好；mismatch 可 debug log once |
| **TS8** | 共存 | W4：估高回归。W5：transparent **零** defer。Toggle：清 `_worklogTurnState`、cancel idle、清 `data-worklog-deferred`、invalidate HTML cache、bump heightGeneration |

### 3.1 C1 — Settled / reload 序源（**Option A，分相**）

| 阶段 | 源 | 义务 |
|------|-----|------|
| Live | SSE DOM 序 / inflight `scene.segments` | 已有 |
| Inflight restore | `INFLIGHT[sid].scene.segments` | 已有；改走 transparent renderer |
| Replay（journal attach） | journal `activity_scene` 行 | `applyActivityScene` + preference |
| **Same-session settle** | done 前 stash：inflight/terminal scene → turn-keyed map（`stream_id` / `assistant:{idx}`） | **必须**；供紧随其后的 `renderMessages` |
| **Cold reload** | 优先：session 可附上的 last-run journal `activity_scene`（已有 journal 读路径则复用；必要时小 API/字段，**禁止**平行第二 journal） | 有 scene → chronological；**无** → legacy flat（不失败 DoD，但 T-M2 标 N/A） |

**禁止**用「单块 thinking + 按 `assistant_msg_idx` 扁桶 tools」冒充有 scene 时的 chronological。

### 3.2 I1 — 禁止双重 thinking

Transparent +（scene \| normalize 事件）时：**不要**再把 thinking 塞进 assistant 消息段（今日 `!simplified` 的 `_thinkingCardHtml` 路径）。Thinking **只**由事件 renderer 产出。

### 3.3 I5 — `normalizeTransparentEvents` 分模式输入

| Mode | Primary input |
|------|----------------|
| live | 当前 DOM / 正在 append 的 SSE；可选 inflight scene 校正 |
| inflight | `INFLIGHT[sid].scene.segments` |
| replay | journal `activity_scene.segments` |
| settled | C1 stash → else journal scene → else legacy flat（tools-only bucket） |

```text
SSE / journal / settled (+ C1 stash)
        │
        ▼
  normalizeTransparentEvents()
        │
        ▼
  renderTransparentEvent()  // thinking | tool-card-row only (no text DOM)
```

---

## 4. 实施切片（#1）

### 4.1 Live UX（cockpit）

- Thinking：**默认 collapsed**（无 `open`），短 preview；running tool / `#toolRunningRow` 保持主可见。
- **不**调用 `ensureActivityGroup`。

### 4.2 Settled

- Insert cursor per turn；按 normalize 序插入 thinking / tool。
- 有 scene：**禁止**再按 `assistant_msg_idx` 扁桶覆盖序；**禁止** segment 内再嵌 thinking（I1）。
- 无 scene：legacy flat（文档）。

### 4.3 Done

- 严格 **TS6** 唯一算法（见上）。

### 4.4 测试

| 测 | 内容 |
|----|------|
| 契约 | transparent + tools → DOM **无** `.tool-call-group` |
| 序 | fixture thinking→tool→thinking→tool ≡ segments |
| D-M11 | transparent + N≥8 → defer false |
| Done | transparent settle：**不**依赖 Opt-C；无 empty Activity 壳 |
| Toggle | mid-live + inflight；preference transparent + scene.display compact → 仍 transparent |
| 回归 | compact ≡ W5；virt-off compact bit-identical；virt-on transparent smoke |

---

## 5. 手工验收（M 表）

| ID | 验收 |
|----|------|
| T-M1 | 关 Compact（= transparent）：live 无 Activity 壳，tools 逐条；**checkbox 即可**（非 segmented） |
| T-M2 | 同 session settle + reload：**有 scene 时**结构与 live 同构 |
| T-M2b | Cold reload **无** journal scene：legacy flat，不宣称失败 |
| T-M3 | Inflight 刷新：transparent 重建 flat，不变 worklog |
| T-M4 | Toggle：A-M5 + TS8 清理清单 |
| T-M5 | Compact 默认：与 W5 bit-identical |
| T-M6 | `?virt=1` + transparent 长 turn：滚动可接受（记已知跳动） |

---

## 6. 非目标

- Journey P1-3；P2 anchored I/O / trusted-proxy  
- SessionChannel / prompt-cache  
- Hermes `activity_rows` / `display_hints` schema  
- Transparent deferred worklog；C-A1 闪烁根治  
- Settings UI 大改；virt 专优  

---

## 7. 签字表（R）

| # | 问题 | 建议 | ☐ |
|---|------|------|---|
| R1 | W6 主路径？ | Transparent Stream 全 UI | ☐ |
| R2 | 默认模式？ | 仍 **compact_worklog**；transparent opt-in | ☐ |
| R3 | `text` segments？ | **MVP：不 DOM 化**；序 = thinking+tool（C4） | ☐ |
| R4 | Done 策略？ | **TS6 唯一算法**（skip Opt-C + skip clear；renderMessages 接管） | ☐ |
| R5 | 拆文件？ | >~200 LOC → `transparent_stream.js` | ☐ |
| R6 | Journey P1-3？ | **不进** | ☐ |
| R7 | Virt 专优？ | **不进** | ☐ |
| R8 | Hermes 整包移植？ | **否** | ☐ |
| R9 | C1 序源？ | **A 分相**（stash + journal；无 scene → legacy） | ☐ |
| R10 | Renderer 谁赢？ | **Preference**（C2） | ☐ |

---

## 8. 修订历史

| 日期 | 说明 |
|------|------|
| 2026-07-12 | DRAFT v1 |
| 2026-07-12 | REVISED — C1 序源 A 分相；C2 preference 赢；C3 TS6 唯一算法；C4 部分准则 2；I1/I5 |
