# W8 细化稿 — P2 收口：Update Channel（stable / experimental）

> **日期**：2026-07-12  
> **状态**：**EXECUTED** — 功能落地于 `feat/w8-update-channel`（U1–U13）  
> **策略**：先技术评审，再按 **0→1** 串行（计划已 Approve；本拍实现收口）  
> **前置**：W0–W7 已合入 main（parity UX + P2 安全 @ `e91d30c`）  
> **父文档**：[`2026-07-11-webui-hermes-parity-analysis.md`](./2026-07-11-webui-hermes-parity-analysis.md) §P2 #9  
> **探索**：[`explore`](f02826c1-a91d-4730-85b7-329111e4003e)  
> **计划评审**：[`code-reviewer`](8f3f1f8d-c4a6-42b8-b5ae-960d245c06f6) — Request changes → 本修订锁定 U8–U13 / R2

---

## 0. 为何是「下一个」

| 轨 | 现状 | W8 取舍 |
|----|------|---------|
| P2 #6–8 | ✅ | **不改** |
| P2 #9 Update channel | 单轨；check 与 apply **未共用**同一 compare 函数 | **本拍** |
| Journey P1-3 | 挂 profile | **仍延后** |
| P3 | backlog | **不进**（`compression_exhausted` → W9 候选） |

---

## 1. 代码现实（修正）

| 事实 | 路径 |
|------|------|
| Apply / force | 调用 `_select_apply_compare_ref` |
| Check | `_check_repo` → release **或** branch；branch 路径 **自行** 解析 `@{upstream}`（与 apply **未统一**） |
| 单根 | v0.6.1 后 `check_for_updates` 只查统一 git root（`REPO_ROOT.parent`） |
| Settings | `check_for_updates` 在 defaults；`ignore_agent_updates` 仅 bool key（**defaults 缺失** → 可能无法持久化） |
| `origin/experimental` | **仓库当前无此分支**；仅 CLI 文档示例 |

---

## 2. 评审摘要

| # | 项 | 估时 | 合入 |
|---|----|------|------|
| **0** | 锁 U1–U13 | 0.5d | 文档 |
| **1** | Channel + 统一 ref + UI + 测试 | 2–4d | 功能 PR |

### 2.1 W8 Definition of Done

| 必须 | 不在 W8 DoD |
|------|-------------|
| `update_channel` stable\|experimental；默认 stable ≡ 今日 | Docker/registry channel |
| `_resolve_compare_ref` **统一** check/apply/force | Journey；P3 |
| experimental：**跳过** release-tag 轨；缺 ref → 诚实失败 | 静默回落 stable |
| Apply（experimental）：与 CLI `--branch` 同精神 — **切到目标轨再 pull/reset**（Update Now = 确认） | 无确认自动切分支 |
| Channel 变更作废 update cache | 改 W7 I/O / proxy |
| Gateway restart 挂钩不回归 | 复活 dual-root webui≠agent check |

---

## 3. 锁（U1–U13）

| ID | 锁 | 决议 |
|----|-----|------|
| **U1** | 配置 | `update_channel` ∈ `{stable, experimental}`；入 `_SETTINGS_DEFAULTS`（默认 `stable`）+ `_SETTINGS_ENUM_VALUES` + load/save 校验 |
| **U2** | Stable | 今日行为：release-or-upstream（现有逻辑） |
| **U3** | Experimental ref | 默认名 `origin/experimental`；可用 `INTELLECT_WEBUI_EXPERIMENTAL_REF` 覆盖。**产品接受**：分支未创建前，选 experimental → check/apply **诚实失败**（文案引导 env/建轨）；**禁止**静默回落 stable |
| **U4** | 单入口 | `_resolve_compare_ref(path, channel)` 供 check + apply + force；消灭 check 侧重复解析 |
| **U5** | UI | Settings select + i18n；banner 标 channel |
| **U6** | 安全 | 仅影响 git ref；不绕过 auth；不改 restart 契约 |
| **U7** | 非目标 | Journey；P3；CORS；W7 回归项 |
| **U8** | Tag × channel | `experimental` **永不**走 `_check_repo_release` / tag apply；只走配置 ref |
| **U9** | Apply 语义 | experimental：**checkout/track 目标分支后**再 ff-only pull（或 force=`reset --hard` 到该 tip）。Update Now / Force = 用户确认。stable 保持今日「当前分支 pull」 |
| **U10** | 单根 | Channel 只作用于统一 git root；**不**复活 webui/agent 双检查。`ignore_agent_updates`：同 PR **补 defaults** 或标 deprecated（二选一，倾向补 defaults 保 UI） |
| **U11** | 轨所有权 | 文档写明：experimental 分支由运维创建或 env 指向真实 ref；缺省名可暂时不存在 |
| **U12** | Cache | 改 channel → invalidate `_update_cache` / 强制下次 check |
| **U13** | WebUI vs CLI | WebUI `update_channel` **不**自动改 `intellect update` CLI 默认；文档注明分流（CLI 仍用 `--branch`） |

---

## 4. 实施切片

```text
update_channel
    │
    ▼
_resolve_compare_ref(path, channel)
    ├── stable → 今日 release/upstream 选择
    └── experimental → env or origin/experimental (no tags)
            │
            ├── check: behind tip?
            └── apply/force: checkout track + pull/reset (U9)
```

### 测试

| 测 | 内容 |
|----|------|
| stable 默认 | 行为 ≡ 今日（mock） |
| experimental 跳过 tag | 即使有 release tag 也不走 tag 轨 |
| 缺 ref | 明确错误 |
| Apply 策略 | mock：experimental 触发 checkout/track 路径 |
| Cache | 改 channel 后旧 banner 不残留 |
| Gateway | apply agent 成功仍调 restart 证明 |

---

## 5. 手工验收

| ID | 验收 |
|----|------|
| U-M1 | stable：与今日一致 |
| U-M2 | experimental + 有轨：check 显示 behind；Apply 切到该轨 |
| U-M3 | experimental + 无轨：诚实失败 |
| U-M4 | Agent apply 后 gateway restart 挂钩仍在 |
| U-M5 | 切 channel 后 Check now 刷新 |

---

## 6. 非目标

- Journey P1-3；P3 outline / SessionChannel / compression_exhausted  
- Docker channel；无确认自动 checkout（Update Now 除外）  
- 改 CLI 全局默认  

---

## 7. 签字表（R）

| # | 问题 | 建议 | ☐ |
|---|------|------|---|
| R1 | W8 主路径？ | P2 #9 update channel | ☐ |
| R2 | Experimental 默认 ref？ | `origin/experimental` + env；**缺轨诚实失败**（可接受空架） | ☐ |
| R3 | 缺轨？ | 诚实失败 | ☐ |
| R4 | Journey？ | 仍延后 | ☐ |
| R5 | compression_exhausted？ | 不进（W9） | ☐ |
| R6 | 默认 channel？ | stable | ☐ |
| R7 | Apply 切分支？ | **是**（U9，Update Now = 确认） | ☐ |
| R8 | experimental 跳过 tags？ | **是**（U8） | ☐ |

---

## 8. 修订历史

| 日期 | 说明 |
|------|------|
| 2026-07-12 | DRAFT v1 |
| 2026-07-12 | REVISED — U8–U13；修正 check/apply 未统一；R2/缺轨/Apply 切分支 |
| 2026-07-12 | EXECUTED — `update_channel` + `resolve_compare_ref` + Settings UI + tests |
