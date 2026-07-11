# W5 细化稿 — P1-A Idle-deferred Worklog（N≥8）

> **日期**：2026-07-12  
> **状态**：**EXECUTED (in progress)** — 2026-07-12 Approve；实现见 `feat/w5-deferred-worklog`  
> **策略**：先技术评审，再按 **0→2** 串行  
> **前置**：W2–W4 已合入 main（SSE、Turn Anchors scene、transcript virt @ `357fca1`）  
> **契约**：[`docs/webui/rfcs/stable-assistant-turn-anchors.md`](../webui/rfcs/stable-assistant-turn-anchors.md) **A5**（N≥8）  
> **评审**：[`code-reviewer`](5e19004e-321b-4a3e-ba56-35f5c968145d) — Request changes → 本修订锁定 C1–C3 / I1–I6

---

## 0. 为何是「下一个」

| 轨 | 现状 | W5 取舍 |
|----|------|---------|
| P1-A scene + Opt-C | ✅ | **本拍：settled Activity 明细延迟 materialize** |
| P1-B virt | ✅ canary off | 共存契约见 §3.2；不扩 virt |
| `transparent_stream` 全 UI | alias only | **不进 W5 DoD** |
| Journey P1-3 | 仍延后 | **不进** |

**真实成本点（C1）**：settle 主开销在 `renderMessages`（`!S.busy`）里清空并重建 `.tool-call-group` + `buildToolCard`；`_convertLiveActivityGroupToSettled` **只做 disclosure remap**，不是 materialize 源。

---

## 1. 代码现实

| 事实 | 路径 |
|------|------|
| Opt-C = disclosure remap only | `ui.js` `_convertLiveActivityGroupToSettled` |
| Settled Activity 重建 | `renderMessages` → `ensureActivityGroup` / `buildToolCard` |
| Scene `segments[]` | inflight + journal；tool `summary` ≤500 |
| Virt 高度 | `_bumpMessageHeightGeneration` + assistant-turn `ResizeObserver` |
| HTML cache | `_sessionHtmlCache`（含 virt bounds / heightGeneration） |

---

## 2. 评审摘要

| # | 项 | 估时 | 合入 |
|---|----|------|------|
| **0** | 锁 DW1–DW8（本文件 §3） | 0.5d | 文档 / PR 头 |
| **1** | Deferred worklog MVP | 3–5d | 功能 PR |
| **2** | virt-on / scene / Opt-C 回归 | 1–2d | **同 train** |

### 2.1 W5 Definition of Done

| 必须 | 不在 W5 DoD |
|------|-------------|
| `compact_worklog` + N≥8 + flag on：先 shell，idle/expand 再填明细 | 完整 `transparent_stream` UI |
| N&lt;8 或 flag off：与今日立即 materialize **bit-identical** | Journey / Gateway |
| virt recycle：pending/done 状态机；无空 body / 双 Activity | 改 agent loop |
| Cache + heightGeneration 在 materialize 后失效/bump | 服务端新 scene 字段 |

---

## 3. 项 #0 — 锁定契约（DW1–DW8）

| ID | 议题 | **锁定答案** |
|----|------|--------------|
| **DW1** | 阈值 N | **≥8** `kind===tool`（post-A4 cap 后计数） |
| **DW2** | N / body 同源 | 本次渲染用来建 body 的源：settled `renderMessages` 优先 **`S.toolCalls` / message-derived**；scene-first 重建用 **scene tool segs** |
| **DW3** | 主钩子（C1） | **Primary：`renderMessages` settled Activity body fill**；Secondary：scene rebuild 若会产出 ≥8 tools；**Opt-C convert 不是 materialize 源** |
| **DW4** | Materialize 源序 | (1) toolCalls / message cards (2) scene tool segments (3) shell + retry。**禁止**「优先 live DOM 卡片」（settle 时已 wipe） |
| **DW5** | 触发 | `requestIdleCallback(cb,{timeout:2000})`；无 rIC 时 **`setTimeout(cb,2000)`**；**或** 首次 expand |
| **DW6** | Display 门控（I1） | **仅 `compact_worklog` / `isSimplifiedToolCalling()`**；`transparent_stream` = no-op（今日扁平行） |
| **DW7** | Flag | **`deferred_activity_worklog` 默认 off**（settings + `?worklog_defer=1` canary）；与 W4 virt 同姿态 |
| **DW8** | 仅 settled | Live 路径不变 |

### 3.1 Expand vs idle（I4）

若 `data-worklog-deferred` / 状态 `pending`：  
**cancel idle → sync materialize → 再 expand**；disclosure 写在 materialize 成功之后（失败则保持折叠 + retry）。

### 3.2 Virt 共存契约（C2）

| 规则 | 锁定 |
|------|------|
| Idle handle | group `disconnect` / session switch 时 **cancel**；`!group.isConnected` → no-op |
| Remount | 读 turn-keyed 状态：`pending` → shell + 重调度 idle；`done` → 满量 body |
| Persistence | key = `assistant:{idx}`（或 `sid+idx`），不只靠 DOM dataset |
| Cache | materialize 后：invalidate `_sessionHtmlCache[sid]` + `_bumpMessageHeightGeneration()` |
| Out-of-window | 仅在窗内（再）创建 group 时 defer；禁止「虚空 materialize」 |
| 高度 | 复用现有 assistant-turn observe；不新开第二套 height API |

---

## 4. 项 #1 — MVP

### 4.1 锚点

| 层 | 路径 |
|----|------|
| Gate | `renderMessages` Activity fill（simplified path） |
| Shell | compact body；`data-worklog-deferred="1"` + turn state map |
| Idle / expand | rIC + `_toggleActivityGroup` 前置 materialize |
| Config | `webui/api/config.py` + settings（与 `transcript_virtual_window` 同级） |
| 测试 | `tests/webui/test_deferred_worklog.py`（N、门控、源序）；DOM manual |

### 4.2 验收

| # | 验收 |
|---|------|
| D-M1 | 7 tools：立即满量 |
| D-M2 | 8+ + flag on：先 shell，idle 后 rows |
| D-M3 | 8+ idle 前 expand：sync materialize + 展开 |
| D-M4 | Flag off：8+ 立即满量 |
| D-M5 | Replay + scene：无双 Activity |
| D-M6 | Settle scroll / follow-intent 不劣于 W3 A-M6（可测 scrollTop 稳态） |
| D-M7 | Flag off + 8+：card 数 / 结构 bit-identical to pre-W5 |
| D-M8 | virt-on：materialize → heightGeneration bump；滚动不劣于 W4 V-M2 |
| D-M9 | virt recycle：idle 前滚走再滚回 → 按 pending/done 恢复；无空 body / 双 Activity |
| D-M10 | Expand 打断 idle：单次 materialize、单次 expand |
| D-M11 | `transparent_stream`：不 defer |

---

## 5. 项 #2 — 回归门禁

同 train：virt-on + scene latch + Opt-C disclosure；D-M5/D-M7–D-M11 绿再合入。

---

## 6. 计划评审清单

| ID | 问题 | **锁定答案** | 签 |
|----|------|--------------|----|
| R1 | W5 主路径？ | Deferred worklog（非 transparent 全 UI） | ☐ |
| R2 | N？ | ≥8 tools（post-cap） | ☐ |
| R3 | Flag 默认？ | **off**（canary） | ☐ |
| R4 | Idle？ | rIC timeout 2s + setTimeout fallback | ☐ |
| R5 | 主钩子？ | `renderMessages` Activity fill | ☐ |
| R6 | 仅 compact_worklog？ | **是** | ☐ |
| R7 | Opt-C？ | disclosure only；非 materialize 源 | ☐ |

---

## 7. 技术评审记录

| Date | Verdict | 摘要 |
|------|---------|------|
| 2026-07-12 | **Request changes** | C1 钩子；C2 virt；C3 flag off；I1–I6 |
| 2026-07-12 | **REVISED** | 已吸收；**待 Approve** |

---

## 8. 文档历史

| Date | Note |
|------|------|
| 2026-07-12 | DRAFT v1 |
| 2026-07-12 | REVISED v2 — 钩子/virt/flag/验收锁定 |
|
