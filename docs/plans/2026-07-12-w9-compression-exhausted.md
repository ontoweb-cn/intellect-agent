# W9 细化稿 — P3 #12：`compression_exhausted` Focused Continuation

> **日期**：2026-07-12  
> **状态**：**REVISED** — 待签字（已吸收计划评审 Request changes）  
> **策略**：先技术评审，再按 **0→1** 串行  
> **前置**：W0–W8 已合入 main（parity UX + P2 安全 + update channel @ `8e434b1` / #49）  
> **父文档**：[`2026-07-11-webui-hermes-parity-analysis.md`](./2026-07-11-webui-hermes-parity-analysis.md) §P3 #12  
> **探索**：[`explore`](e5349f39-26d5-4889-9341-e2d6248d91aa)  
> **计划评审**：[`code-reviewer`](860dbd70-61a3-4f78-878f-03e916f7aee6) — Request changes → 本修订锁定 C4/C7a–b / C2 次序 / C6a / T5–T6  

---

## 0. 为何是「下一个」

| 轨 | 现状 | W9 取舍 |
|----|------|---------|
| P0 / P1 UX / P2 | ✅ W0–W8 | **不改** |
| P3 #12 `compression_exhausted` | Agent 已置位；Gateway 有 auto-reset；**WebUI 零处理** | **本拍** |
| Journey P1-3 | 挂 `profiles.management_enabled` 恢复 | **仍延后** |
| P3 #10 outline / #13 SessionChannel | 体验/架构债 | **不进**（W2 已选 run_journal 演进） |
| P3 #11 steer 排队 | steer 已存在；非 context 死胡同 | **不进** |

**一句话**：关掉「压缩耗尽后 WebUI 只剩静默/泛化错误」的死胡同；复用已有 `focus_topic` 手工压缩与 `compression_anchor`，不做第二套压缩引擎。

---

## 1. 代码现实

| 事实 | 路径 / 行为 |
|------|-------------|
| Agent 耗尽信号 | `agent/conversation_loop.py` 多处返回 `"compression_exhausted": True`（413 / overflow / 无法再压） |
| Gateway | `gateway/run.py`：检测 flag → **auto-reset session** + 用户提示（messaging 轨） |
| WebUI 成功压缩 | `streaming.py` SSE `compressing` / `compressed`；`compression_anchor_*`；manual `POST /api/session/compress` + `focus_topic`（≤500） |
| WebUI 缺口 | `webui/` **零** `compression_exhausted` 匹配；`_classify_provider_error` 无 exhaustion 类型；静默失败走 generic `apperror`；且今日 silent-failure 仅在 `not _token_sent` 时触发 |
| 会话轮转风险 | 耗尽常发生在 `compress_context` **已轮转** `agent.session_id` 之后；若 early `return` 在 migration 块（~5277+）之前，CTA 会打到旧 sid |
| 可复用 CTA | `commands.js` `/compress`；`ui.js` compression cards；`sessions.js` pre_compression_snapshot 谱系 |

**Hermes 意图（非可抄 RFC）**：parity 矩阵称「恢复包 / focused continuation」。本仓无 Hermes 源 RFC — **按 Intellect 已有能力定义契约**。

---

## 2. 评审摘要

| # | 项 | 估时 | 合入 |
|---|----|------|------|
| **0** | 锁 C1–C12 + R 表 | 0.5d | 文档 |
| **1** | 分类/发射 + migration 次序 + exhaustion UI + CTA + 测试 | 2–4d | 功能 PR |

### 2.1 W9 Definition of Done

| 必须 | 不在 W9 DoD |
|------|-------------|
| `result.compression_exhausted` → `apperror` `type=compression_exhausted`（结构化 payload） | 新专用 SSE 事件名；Gateway messaging 行为变更 |
| Exhaustion **卡片**（非仅 markdown Error）：可编辑 focus + Focused compress + `/new` | 无确认自动 focused compress；自动重发本轮用户消息 |
| Flag 路径：**不论** `_token_sent`；migration/snapshot **先于** apperror | WebUI 默认 auto-reset 对齐 Gateway |
| 复用现有 `/api/session/compress` + `focus_topic` | thin recover 路由（除非 start 无法传 focus — 今日可传，**不进 DoD**） |
| T5–T6 + 误判防护测 | 改 agent 压缩算法；Journey / outline / SessionChannel |

---

## 3. 锁（C1–C12）

| ID | 锁 | 决议 |
|----|-----|------|
| **C1** | 范围 | 仅 WebUI 面：识别耗尽 → 可操作恢复；**不**重写 `context_compressor` |
| **C2** | 信号与分类次序 | **(1)** `result.compression_exhausted` → `compression_exhausted`；**(2)** cancelled/interrupted；**(3)** quota；**(4)** rate_limit（含 429 / `RateLimitError`）；**(5)** auth / model_not_found；**(6)** 可选窄 overflow 短语 **仅当**无 flag 且未命中 (3)(4) — allowlist 对齐 agent/gateway **多词**短语，**禁止**裸 `exceeded`/`token`/`limit`；**(7)** silent `no_response`；**(8)** generic `error`。**禁止**映射到 `process_wakeup_paused` |
| **C3** | 产品叉 | **Recovery-first**：保留会话 + CTA。**禁止**默认 WebUI auto-reset（opt-in 另拍） |
| **C4** | SSE 契约（钉死） | 终态 SSE **`apperror`**，`type=compression_exhausted`。Payload **必须**含：`type`、`message`、`hint`、`suggested_focus`（≤500）、可选 `compression_exhausted: true`。W9 **不**新增专用 SSE 事件名。FE **必须**特判进 exhaustion 卡片（不得只渲染 `**Error:** …`） |
| **C5** | Focus 预填 | `suggested_focus` = **本 WebUI 轮**启动时的用户文本 `msg_text`（截断 ≤500、strip）。**不是**压缩后 session 末条；空则 CTA 仍可用（空白可编辑） |
| **C6** | CTA | Primary：现有 compress start/poll + `focus_topic`；Secondary：现有 `/new`。成功后走今日 `compressed` / anchor。**不**自动重发耗尽轮用户消息 |
| **C6a** | FE 卡片契约 | 可编辑 focus（预填 `suggested_focus`）+ Focused compress + `/new`；复用 `ui.js` / `commands.js` 压缩卡模式，不平行第二套 compress 客户端 |
| **C7** | 分类器入口 | `_classify_provider_error`（或并列 helper）支持 exhaustion；flag 优先于 generic `no_response` |
| **C7a** | 发射门 | `run_conversation` 返回后若 `compression_exhausted`（或 `failed`+flag）为真 → 发 exhaustion apperror，**不论** `_token_sent` / 部分 assistant 文本。Flag 优先于 generic `done`/`no_response` |
| **C7b** | Session migration | 若耗尽前已发生 compress 轮转：在发 apperror **之前**跑现有 migration / `pre_compression_snapshot`（或抽出的共享 helper）。apperror / 后续 compress API **必须**指向 **轮转后** active `session_id` |
| **C8** | 非目标 | Journey P1-3；outline；SessionChannel；Office/i18n；Gateway auto-reset 移植；agent 无同意自动再压 |
| **C9** | 持久化 | MVP **不要求** `compression_exhausted_at`；reload 丢 CTA 可接受（若已有 `_error` 行保留文案更佳，非门槛） |
| **C10** | Prompt-cache | CTA compress ≡ 今日 manual compress 契约；禁止额外改 toolset/system prompt |
| **C10a** | 成功期望 | 成功 → 现有 compressed/anchor UX；**无**自动重提本轮用户消息 |
| **C11** | i18n | 至少 en + zh：耗尽说明、CTA、hint（键名实施时固定进 touch list） |
| **C12** | vs Gateway | WebUI 文案不声称与 messaging auto-reset 行为一致 |

---

## 4. 实施切片

```text
agent result.compression_exhausted
        │
        ▼
streaming: migration/snapshot if sid rotated (C7b)
        │
        ▼
apperror type=compression_exhausted + suggested_focus (C4/C5)
        │
        ▼
ui exhaustion card (C6a)
        ├── Focused compress → /api/session/compress (+ focus_topic)
        └── /new
```

### 建议触点

| 层 | 文件 |
|----|------|
| API | `webui/api/streaming.py`（C7a/C7b + C2；silent-failure / 成功收尾前短路） |
| FE | `messages.js`、`ui.js`、`commands.js`、`i18n.js` |
| 测试 | `tests/webui/test_compression_exhausted*.py` |

### 测试

| 测 | 内容 |
|----|------|
| T1 Flag → type | mock `compression_exhausted=True` → payload type 正确 |
| T2 误判防护 | rate limit / quota 文案 **不**标为 exhaustion |
| T3 Prefill | `suggested_focus` 来自本轮 `msg_text` 且 ≤500 |
| T4 回归 | 成功 auto/manual compress + anchor 路径仍绿 |
| **T5** Migration | flag + `agent.session_id` ≠ 轮前 WebUI sid → apperror/后续 compress 用 **新** sid |
| **T6** Partial stream | `_token_sent=True` + flag 仍发 exhaustion apperror（非裸 `done`） |

---

## 5. 手工验收

| ID | 验收 |
|----|------|
| C-M1 | mock 或文档化 repro：出现 exhaustion **卡片**，非空白 done |
| C-M2 | Focused compress 成功后有 compressed/anchor 行为；**不**自动重发原消息 |
| C-M3 | 用户改 focus 后仍可压 |
| C-M4 | `/new` CTA 可用 |
| C-M5 | 限流 / 取消 / 普通错误 **不**进 exhaustion 卡片 |
| C-M6 | 轮转后 sid：CTA compress 打到新会话 |

---

## 6. 非目标

- Journey P1-3；P3 outline / SessionChannel / Office sidecar  
- Gateway messaging 路径改写；WebUI 默认 auto-reset  
- Agent 自动 focused compress 无用户确认；自动重发本轮  
- 新并行 SSE 事件名 / 事件总线  

---

## 7. 候选对比（为何不是别的 P3）

| 候选 | 相对本拍 |
|------|----------|
| Outline / 三栏 | 可读性；非失败死胡同 |
| Steer 排队 | 正确性边角；非 context 耗尽 |
| SessionChannel | W2 已用 run_journal 演进；收益递减 |
| Journey P1-3 | 外部门禁：profile management restore |

---

## 8. 签字表（R）

| # | 问题 | 建议 | ☐ |
|---|------|------|---|
| R1 | W9 主路径？ | P3 #12 WebUI exhaustion → focused continuation | ☐ |
| R2 | 默认产品叉？ | **Recovery-first**；非 Gateway auto-reset | ☐ |
| R3 | SSE 形态？ | **钉死** `apperror` `type=compression_exhausted` + 结构化 payload（非「倾向」） | ☐ |
| R4 | Focus 预填？ | 本轮 WebUI `msg_text` ≤500 | ☐ |
| R5 | Journey / outline？ | **不进** | ☐ |
| R6 | 持久化 exhausted？ | MVP 不做 | ☐ |
| R7 | Agent 自动再压 / 自动重发？ | **禁止** | ☐ |
| R8 | 发射门？ | Flag 优先，即使 `_token_sent`；**先** migration 再 apperror | ☐ |
| R9 | 分类次序？ | flag → … → quota → rate_limit → 窄 overflow；禁 wakeup pause | ☐ |
| R10 | FE？ | Exhaustion 卡片：可编辑 focus + compress + `/new`；无自动重试本轮 | ☐ |

---

## 9. 修订历史

| 日期 | 说明 |
|------|------|
| 2026-07-12 | DRAFT v1 |
| 2026-07-12 | REVISED — C4 钉死 apperror；C7a/C7b 发射与 migration；C2 次序；C6a/C10a；T5–T6；R8–R10 |
