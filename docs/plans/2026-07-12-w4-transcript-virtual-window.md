# W4 细化稿 — P1-B Transcript 全量虚拟窗（spacer）

> **日期**：2026-07-12  
> **状态**：**APPROVED** — 2026-07-12 用户 Approve；按 0→1+2→3 执行中  
> **策略**：先技术评审，再按 **0→2** 串行（#1+#2 同 train）  
> **前置**：W2+W3 已合入 main（[#38](https://github.com/ontoweb-cn/intellect-agent/pull/38)、[#39](https://github.com/ontoweb-cn/intellect-agent/pull/39) @ `3b7558f`）  
> **父文档**：[`2026-07-11-p1-journey-and-webui-parity-refinement.md`](./2026-07-11-p1-journey-and-webui-parity-refinement.md) §2.3 / §2.5（B4）、[`2026-07-12-w3-turn-anchors-p1a.md`](./2026-07-12-w3-turn-anchors-p1a.md)  
> **评审**：[`code-reviewer`](2b35327c-559e-4166-af57-9506ce033f82) — Request changes → 本修订锁定 C1–C5 / I1–I7  
> **日历更正**：W3 A5 原写「worklog 实现 W4」→ **改为 W5**（本拍占 B4 virt）

---

## 0. 为何是「下一个」

| 轨 | 现状 | W4 取舍 |
|----|------|---------|
| P0 SSE + B4 wakeup | ✅ main | 基线 |
| P1-A journal scene | ✅ main | **残留** deferred worklog / transparent_stream → **W5**（非本拍） |
| P1-B MVP | ✅ 尾窗 50 + 用户行高度 Map + follow-intent + cache bounds | **本拍升级为变高 spacer 虚窗** |
| Journey P1-3 | 仍挂 profile | **不进 W4** |
| P1-C | `_applyPendingSessionModelForSession` 已存在 | 仅回归 |

**本拍不做**：Journey `/journey`、Idle-deferred worklog（→W5）、完整 `transparent_stream`、平行 SessionChannel、prompt-cache、侧栏 virt 大重构。

---

## 1. 代码现实（计划前提）

| 事实 | 路径 |
|------|------|
| 消息区 **尾切片**：`windowStart = len-renderWindowSize`，无 top spacer | `ui.js` `renderMessages` ~6611–6716 |
| 用户行高度 Map + ResizeObserver；cap `_userMessageHeightMax=400` | `ui.js` |
| 侧栏固定行高 virt：`SESSION_VIRTUAL_ROW_HEIGHT=52`，threshold 80 | `sessions.js` `_sessionVirtualWindow` |
| Load-earlier / jump 今日常 **扩大** `_messageRenderWindowSize` 逼近全量 DOM | 须在 virt-on 下改语义（§5） |
| `#messages` 可滚；`#msgInner` 内容宿主 | |

---

## 2. 评审摘要

| # | 项 | 估时 | 合入 |
|---|----|------|------|
| **0** | 锁 V1–V8 契约（本文件 §3；可选短 RFC） | 0.5–1d | 文档 / PR 头 |
| **1+2** | **变高虚窗核心 + 锚点/jump/load-older**（**同 train**） | 5–8d | 单一功能 PR |
| **3** | P1-A / SSE 回归（virt-on 复跑 W3 A-M/A-J 意图） | 1–2d | 同 PR 尾 |

**门禁**：#1 不得在 V-A* 未绿时单独合入（I7）。

### 2.1 W4 Definition of Done

| 必须 | 不在 W4 DoD |
|------|-------------|
| Flag on + `visCount>T`：DOM = 可视邻域 + top/bottom **变高** spacer | Deferred worklog（W5） |
| Flag off：与 W2 尾窗 **bit-identical** | Journey P1-3 |
| Jump / load-older / session-start 在 virt-on 下不「扩窗骗全量」 | transparent_stream 全 UI |
| Live 流式可上滚历史且 live 行仍挂载（C1 模型） | 服务端分页 |
| HTML cache 含 bounds + pad/height generation | |

---

## 3. 项 #0 — 锁定契约（V1–V8）

| ID | 议题 | **锁定答案** |
|----|------|--------------|
| **V1** | 阈值 `T` | **`T=80`**（与侧栏 threshold 对齐）。`visCount≤T` 且 flag on → 仍走 **W2 尾窗**（无 spacer） |
| **V2** | Flag | **`transcript_virtual_window` 默认 off**；settings 与/或 `?virt=1` canary；可随时关回 W2。**禁止** W4 默认 on |
| **V3** | 行为矩阵 | 见下表 |
| **V4** | Live 行（C1） | **Preferred：非虚窗兄弟节点** — `#liveAssistantTurn` 挂在 bottom spacer **之后**（或 sticky 区），流式期间始终挂载；虚窗 `[start,end)` **只覆盖历史**。**禁止**靠平移 `start` 强行把 live 塞进 slice |
| **V5** | 索引空间（C3） | 窗口下标基于 **`visWithIdx`**；高度键 `sid:rawIdx`；**扩展到 assistant-turn 边界**（禁止切开 `.assistant-turn`）；compression / load-older chrome **在 slice 外** |
| **V6** | 高度算法（C2） | 新 helper `_variableHeightVirtualWindow({ heightAt(i)|prefixSums, scrollTop, viewport, buffer, pinIndices })`。Pads = **前缀高度和**，**禁止**用 `SESSION_VIRTUAL_ROW_HEIGHT` / `_sessionVirtualWindow` 直接算消息 pad。侧栏只复用 **rAF / threshold / spacer DOM 模式** |
| **V7** | 缺省估高 | 用户行 / 助手 turn 分常数；观测后写回 Map；Map cap **≥2000/sid 或全局 LRU**（替换今日 400 FIFO thrash） |
| **V8** | Overscan | Buffer 以 **vis 行数或 px** 计（建议先 **px ≈ 1.5× viewport**）；不照搬侧栏 12 固定行 |

### 3.1 行为矩阵（V3）

| Flag | `visCount` | Behavior |
|------|------------|----------|
| **off** | any | W2 尾窗 50（bit-identical） |
| **on** | ≤80 | W2 尾窗（无 spacer） |
| **on** | >80 | 变高虚窗；**废除**无 spacer 硬尾 |

### 3.2 滚动锚点（I4）

1. 记录锚点：第一个完全可见的 `visIndex` + `offsetBefore`（相对 `#messages`）。  
2. 重切 `[start,end)` 后：按高度账本恢复 `scrollTop`。  
3. 一次 rAF resync（镜像 `_resyncSessionVirtualWindowAfterRender`）。  
4. 某行学得高度使 pad 变化 >ε → bump **height-generation**、使 HTML cache 失效、再 resync。

### 3.3 Cache（I2）

`_sessionHtmlCache` 签名必须含：`vStart,vEnd, padTop,padBottom` **或** `heightGeneration`（学高后递增）。仅 `vStart/vEnd` **不够**。

### 3.4 Endless-scroll（N4）

`_isSessionEndlessScrollEnabled`：**本拍保持现状**；若与虚窗冲突，flag on 时优先虚窗语义，并在 #0 注释一行。不扩 endless-scroll 功能。

---

## 4. 项 #1+#2 — 核心虚窗 + 锚点（同 train）

### 4.1 锚点文件

| 层 | 路径 |
|----|------|
| Helper | 新 `webui/static/virtual_window.js`（或 `ui.js` 顶部）：`_variableHeightVirtualWindow` + `_messageVirtualSpacer` |
| Render | `ui.js` `renderMessages` |
| Scroll | `#messages` → rAF（模式参考 `sessions.js`，算法用变高 helper） |
| Jump / earlier | `ui.js` / `messages.js` / `commands.js` 中 `_showEarlierRenderedMessages`、`jumpToTurnQuestion`、`jumpToSessionStart` |
| 测试 | `tests/webui/test_transcript_virtual_window.py`（算法 + 边界）；jump/load-older 补偿测 |

### 4.2 Load-earlier / jump（C4 — 锁定）

| 路径 | Flag **on** 且虚窗激活时 |
|------|-------------------------|
| 内存「更早」 | **滚动 + 平移窗口**；**禁止**靠扩大 `_messageRenderWindowSize` 逼近全量 DOM（可达行） |
| `_messagesTruncated` 服务端再取 | 保留按钮 / fetch；与虚窗正交 |
| `jumpToTurnQuestion` | 确保目标 vis 入窗 → render → `scrollIntoView`（不 full expand） |
| `jumpToSessionStart` | `start=0` 窗口 + `scrollTop=0` |

### 4.3 验收

| # | 验收 |
|---|------|
| V-M1 | 200+ 消息 + flag on：DOM 子节点 ≪ 消息数（有变高 spacer） |
| V-M2 | 快速上下滚：跳动不劣于 W2；锚点/resync 生效 |
| V-M3 | flag off：与 W2 尾窗一致 |
| V-M4 | 单元：顶/底/buffer；变高 prefix 和 |
| V-A1 | Jump 早期用户消息入视口；Activity/disclosure 不坏 |
| V-A2 | Load-older（内存平移）后不跳顶 |
| V-A3 | 压缩锚点仍命中 |
| V-A4 | Jump **不**触发全量 expand |
| V-A5 | Session-start：窗在顶且 scrollTop≈0 |

---

## 5. 项 #3 — P1-A / SSE 共存

| 规则 | 说明 |
|------|------|
| Settle | `renderMessagesWithFollowIntent` 后重建 spacer；flag off 路径不变 |
| Live + scene | Live 在 slice 外挂载（V4）；scene latch 不依赖「全历史在 DOM」 |
| Snapshot | follow-intent ≥ W2 |
| 回归门禁（I6） | virt-on 下复验 W3 **A-M1 / A-M6** 意图与 **A-J2**（无双 Activity）；可标 manual + 能单测的部分自动化 |

| # | 验收 |
|---|------|
| V-S1 | 流式中上滚历史再回底：live 仍挂载（兄弟模型） |
| V-S2 | done / snapshot 贴底 ≥ W2 |
| V-S3 | 中刷新 + inflight scene ≥ W3 A-M1 |
| V-S4 | virt-on 不破坏 Opt-C disclosure remap |

---

## 6. 依赖与风险

```text
main(W3) ──► #0 锁 V1–V8 ──► #1+#2 同 train ──► #3 回归
                              │
                              └── merge 门禁：V-M* + V-A* 绿
```

| 风险 | 缓解 |
|------|------|
| 误用侧栏固定行高 | V6 硬禁止；CR 拒收 |
| Live 强行入窗拽滚动 | V4 兄弟挂载 |
| Map 400 thrash | V7 抬 cap / LRU |
| 文档争 W4 | worklog → **W5**（见文首） |

---

## 7. 计划评审清单

| ID | 问题 | **锁定答案** | 签 |
|----|------|--------------|----|
| R1 | W4 主路径？ | P1-B 变高 spacer virt | ☐ |
| R2 | Flag 默认？ | **off** | ☐ |
| R3 | T / 矩阵？ | T=80；§3.1 | ☐ |
| R4 | 高度？ | 变高 prefix；扩 Map | ☐ |
| R5 | Live？ | 非虚窗兄弟 / dual-mount | ☐ |
| R6 | Worklog？ | **W5** | ☐ |
| R7 | 共享算法？ | **只共享模式**；新变高 helper | ☐ |
| R8 | #1+#2？ | **同 train** | ☐ |

---

## 8. 技术评审记录

| Date | Verdict | 摘要 |
|------|---------|------|
| 2026-07-12 | **Request changes** | C1 live vs 上滚；C2 禁止侧栏固定高；C3 turn 边界；C4 jump/earlier；C5 flag off |
| 2026-07-12 | **REVISED** | 上表已吸收；**待 Approve** |

---

## 9. 文档历史

| Date | Note |
|------|------|
| 2026-07-12 | DRAFT v1 — P1-B 全量 virt |
| 2026-07-12 | REVISED v2 — 吸收 tech review；worklog 日历改为 W5 |
|
