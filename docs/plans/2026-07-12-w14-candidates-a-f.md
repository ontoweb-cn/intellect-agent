# W14 细化稿 — 候选 A–F 全量细化（收口债 + 下一拍菜单）

> **日期**：2026-07-12  
> **状态**：**APPROVED → 执行中** F+A（用户选定；W14-F 文档先行，W14-A Tier C 接续）  
> **前置**：W13 DONE @ `feeba35`（#64）— Gateway 层 C + openat Tier A/B  
> **父文档**：[`2026-07-12-w13-gateway-lifecycle-and-openat.md`](./2026-07-12-w13-gateway-lifecycle-and-openat.md)；[`2026-07-11-webui-hermes-parity-analysis.md`](./2026-07-11-webui-hermes-parity-analysis.md)；W4 virt / W11 / W12  
> **产品锁**：永久单用户；SessionChannel **不进**；Profiles 默认仍 false；分容器 gateway **不进**

> **For agentic workers:** 本文件细化 **全部** A–F；**不**默认全部实施。用户选定组合后再开实施 PR。评审签 R 表。

**Goal:** 把 W13 后残留与 P3/卫生候选项钉成可排期、可丢弃、可评审的任务包；默认推荐 **F 文档收口 + 一主轴（A 或 C）**。

**Architecture:** 六候选相互独立（除 F 为所有拍的前置文档债）。A=安全树 ops；B=Journey 小 polish；C=virt 硬化；D=P3 大体验/架构；E=卫生；F=文档。

**Tech Stack:** `workspace_io` / `workspace.py` / `routes.py`；`virtual_window.js` / `ui.js`；`learning_mutations`；`auth.py` cookie；docs/plans + RFC。

---

## 0. 方案对比与推荐

| 组合 | 内容 | 估时 | 评价 |
|------|------|------|------|
| **F-only** | 文档 DONE 勾选 + parity 刷新 | 0.5d | 必须做，但不推进产品 |
| **F+A** | 文档 + openat Tier C | 4–6d | **安全收口**；承接 W13 residual |
| **F+C** | 文档 + virt 硬化 | 3–5d | **长会话体验**；W4 DoD 洞 |
| **F+E** | 文档 + Secure cookie（±删 wiki_merge） | 1–2d | 低风险卫生 |
| **F+B** | 文档 + Journey restore_hint | 1–2d | 可丢；MVP 已够用 |
| **F+D** | 文档 + outline **或** gateway-steer RFC | 7–14d+ | 须先选 D1/D2；忌双开 |
| **A+C 同拍** | Tier C ∥ virt | 7–10d | 可并行但评审面大 |
| **全做 A–F** | — | 20d+ | **禁止**；D 未选子项前勿批 |

**推荐默认：** **F+A**（审计/安全收口 W13 residual）或 **F+C**（长会话 / virt canary）。决策口诀：要关 openat 债 → A；要稳 virt UX → C。E/B 可作旁路 tip。D 仅产品点名后开 RFC。

---

## 1. 锁（L1–L12）

| ID | 锁 | 决议 |
|----|-----|------|
| **L1** | F 前置 | 任一实施 PR 前或同 PR：**W13 计划 DONE 勾选** + parity「W13 DONE @ feeba35」 |
| **L2** | A 范围 | Tier C = **仅** `list_dir` + folder-zip collect + `rmtree_under_root` 分量链 openat |
| **L3** | A 非目标 | git discard、upload inbox、agent `tools/`、mkdir 父链、**create 的 optional last-hop openat**、autocomplete `iterdir` **不进** A merge gate（create 可另 tip） |
| **L4** | A 宣称 | 完成后模块头必须写：「POSIX：list_dir / folder-zip walk / `rmtree_under_root` 三分量链 openat 路径上 tree-walk closed」。**禁止**「全仓 / workspace I/O / TOCTOU-closed」。Windows = degraded fallback 文档化 |
| **L5** | B | **可丢 tip**；最小 = `restore_hint` 字段；**不**默认迁 `uninstall_skill` |
| **L6** | C | 硬化 W4 已合 virt：**测 + 闭合 I4（visIndex+offsetBefore）+ assistant 流式高度补强**；**不**默认开 `transcript_virtual_window` |
| **L7** | D | 必须拆 **D1 outline** / **D2 gateway-steer**；禁止混成一 PR；D3 SessionChannel 默认不做。**相对 W12/W13「outline 不进」：本菜单改为「仅产品点名 → RFC → 实施」；未点名则仍不进** |
| **L8** | E-wiki | 默认 **删除或继续延期**（永久单用户无 contribution merge）；**不**默认真接线 API |
| **L9** | E-cookie | Secure 对齐：`clear_auth_cookie` + `build_profile_cookie` 与 auth cookie 同 `_is_secure_context` |
| **L10** | Canvas 孤儿 | 指 WebUI **Markdown Canvas**（`canvas.js` / `/api/canvas`），**不是** Journey Star Map（HP-401p–r icebox）。**决议：API-only stub** — 文档标明 API-only；**不**接线 `canvas.js`；**不**删 API（本拍） |
| **L11** | 拆 PR | F 可先合；A/C/E/B 各独立；D 仅 RFC 或单子项 |
| **L12** | 永久单用户 | 不恢复 members UX；不默认开 profiles.management |

---

## 2. 候选细化

### A — openat Tier C（W13-O-C）

**问题：** Tier A/B 后，树操作仍 `iterdir` / `os.walk` / `shutil.rmtree`。

**触点：**

| Op | 路径 |
|----|------|
| list_dir | `webui/api/workspace.py` ~675–749；route ~9276 |
| zip collect | `routes.py` `_folder_download_collect` ~10245–10286 |
| rmtree | `workspace_io.rmtree_under_root` ~238–253 |

**DoD：**

- [x] A1 POSIX：`open_root_dir_fd` + 分量链 `openat(O_NOFOLLOW|O_DIRECTORY)`  
- [x] A2 改写三 ops；API 形状不变  
- [x] A3 Windows degraded 文档 + 测 skip/fallback  
- [x] A4 测：穿越/escape symlink；树删；zip 收集不含逃逸  
- [x] A5 模块头 Tier 表：Status=done（仅三 ops）；**Claim 列按 L4 改写**（禁止裸 "tree closed" / 全仓 closed）  

**估时：** 3–5d。**非目标：** git discard（可另 tip 走 Tier B helper）。

---

### B — Journey P1-2 stretch

**问题：** MVP（`deleteMode` / hub uninstall / UI 文案）已合；缺服务端 `restore_hint`；`uninstall_skill` 未委托。

**DoD（最小）：**

- [ ] B1 `node_detail` / `delete_node` 可选 `restore_hint`  
- [ ] B2 `journey.js` 优先读字段；i18n fallback  
- [ ] B3 mutations + 一层 API 测  

**DoD（可丢）：** provenance 表；证明后迁 `uninstall_skill`。

**估时：** 1–2d 最小；整 tip 可丢。

---

### C — P1-B transcript virt 硬化

**问题：** W4 已合变量窗 + spacer（flag 默认 off）。残留：DOM/跳转测不足；scroll 锚点未按 W4 I4（visIndex + offsetBefore）落地；assistant-turn **流式长高 / settle** 高度账本仍弱（已有 RO 挂载 ≠ I4 闭合）。

**触点：** `ui.js` virt 块；`virtual_window.js`；`#msgInner`；flag `transcript_virtual_window`。

**DoD：**

- [ ] C1 测：load-earlier pan、jump 不全量 expand、session-start（能自动化则自动化）  
- [ ] C2 **闭合** W4 I4：snapshot = visIndex + offsetBefore；重切后按高度账本恢复（**不是**「关闭 I4」）  
- [ ] C3 assistant-turn：流式/多 segment 后高度写回或 settle pass（补强，非从零加 RO）  
- [ ] C4 **不**默认开 flag；回归 checklist V-S1/S2  

**估时：** 3–5d。**非目标：** 服务端分页；默认开 virt。

---

### D — Outline / Gateway-owned steer

**必须先选子项：**

| 子项 | 内容 | 估时 |
|------|------|------|
| **D1** | Conversation outline + 三栏可读下限 | 7–10d+（含 RFC） |
| **D2** | Steer 队列网关化；WebUI 不依赖 `SESSION_AGENT_CACHE` 命中 | 7–14d+（含 RFC） |
| **D3** | SessionChannel | **默认不做** |

**现状：** WebUI steer **已能工作**（本地 `SESSION_AGENT_CACHE`）；parity **§3 P3 项 11** gateway-owned steer **未达**。Outline = **§3 P3 项 10**（gap matrix 行「Outline / 三栏」）— **零代码**。引用时**禁止**裸写「parity #11」（§2 矩阵 #11 = outline，§3 列表 #11 = steer）。

**DoD（若选 D1/D2）：** 先 RFC → Approve → 实施；parity 行更新。

---

### E — wiki_merge / Secure cookie

| 子项 | 现状 | DoD |
|------|------|-----|
| **E1 wiki_merge** | `intellect_cli/wiki_merge.py` **零调用方** | 删模块 **或** 计划明示 continue-defer + CI grep；**不**默认真接线 |
| **E2 Secure cookie** | auth cookie 有 Secure；`clear_auth_cookie` / profile cookie 缺齐 | 统一 `_is_secure_context`；测 `INTELLECT_WEBUI_SECURE` + proxy |

**估时：** E1 0.5d；E2 1d。

---

### F — 文档与索引收口

| 项 | DoD |
|----|-----|
| F1 | W13 计划头 **DONE @ feeba35**；Task 1–5 按合并实况勾选；5.4 Tier C = 跳过→W14-A |
| F2 | parity：W13 DONE；刷新 §2 过时行（restart/openat）；**标 compression_exhausted = W9 DONE**（勿当 P3 未做） |
| F3 | Canvas（Markdown Canvas，非 Star Map）：**API-only stub** — 文档标明；不接线 `canvas.js`；不删 `/api/canvas`（本拍） |
| F4 | 可选：刷新 **plans backlog 索引**（勿称 “backlog canvas”） |

**估时：** 0.5–1d。**可先合。**

---

## 3. 实施切片（选定后）

```text
F（文档）──可先合──┐
                   ├─ 主轴 A（Tier C）     ← 推荐安全
                   ├─ 主轴 C（virt）       ← 推荐体验
                   ├─ 旁路 E2 / B / E1
                   └─ D 仅 RFC（若产品点名）
```

| PR | 内容 |
|----|------|
| **W14-docs** | F1–F3 |
| **W14-A** | Tier C |
| **W14-C** | virt 硬化 |
| **W14-E** | Secure cookie ± wiki_merge |
| **W14-B** | restore_hint tip |
| **W14-D-RFC** | outline 或 steer RFC |

---

## 4. 任务清单（评审用）

### Task 0 — 本文件

- [x] 0.1 READY FOR REVIEW  
- [x] 0.2 技术评审 → **REVISED**（L4/L7/C2/parity 引用/回滚/R13–R14）  
- [x] 0.3 用户选定组合：**F+A**（文档收口 + openat Tier C）  

### Task F — 文档（默认必做）

- [ ] F1–F4 见 §2  

### Task A / B / C / D / E — 见 §2 DoD（选定后勾）

---

## 5. 非目标

- SessionChannel；真 multi-user；默认开 Profiles  
- A+C+D 同 merge gate；A–F 全做  
- 夸大「W14 = 全仓 openat closed」  
- 默认打开 transcript virt flag  
- P3 Office sidecar / 新 i18n 语言包（另排）  
- git discard → openat tip（非 A；可另旁路）  

---

## 6. 风险与回滚

| 风险 | 缓解 |
|------|------|
| Tier C 面大拖垮 | L2 钉死三 ops；git discard 外置 |
| D 范围膨胀 | L7 强制 D1/D2 二选一 + RFC；未点名不进 |
| F 不做导致计划漂移 | L1 F 前置 |
| B 无用户价值 | L5 可整 tip 丢 |
| 宣称夸大 | L4 + A5 改写 Claim 列 |

| 回滚 | 做法 |
|------|------|
| A | 三 ops 可退回 Path/`os.walk`/`shutil.rmtree`（**API 形状不变**） |
| C | flag 保持默认 off；I4/测为可独立 revert 的 JS/测 PR |
| E2 | cookie Secure 可按部署回退明文 Secure 省略（HTTPS 关闭时） |

---

## 7. 评审清单（R1–R12）

| ID | 问题 | 期望 | 签 |
|----|------|------|-----|
| R1 | F 为默认必做？ | **是** | ☑ |
| R2 | 推荐主轴 F+A 或 F+C？ | **是（二选一）** | ☑ |
| R3 | A 仅三树 ops？ | **是** | ☑ |
| R4 | git discard 不进 A？ | **是** | ☑ |
| R5 | B 可丢？ | **是** | ☑ |
| R6 | C 不默认开 flag？ | **是** | ☑ |
| R7 | D 须拆 D1/D2？ | **是** | ☑ |
| R8 | SessionChannel 不做？ | **是** | ☑ |
| R9 | E1 默认删或 defer？ | **是（不接线）** | ☑ |
| R10 | E2 Secure 对齐？ | **是** | ☑ |
| R11 | Canvas 三选一进 F？ | **是** | ☑ |
| R12 | 禁止 A–F 全做？ | **是** | ☑ |
| R13 | Tier C 宣称已按 L4 改写？ | **是** | ☑ |
| R14 | outline 解锁仅产品点名？ | **是** | ☑ |

---

## 8. 现状锚点速查

| 候选 | 关键现状 |
|------|----------|
| A | Tier A/B @ W13；三树 ops 仍 Path/walk/rmtree |
| B | MVP deleteMode/uninstall 已合；无 `restore_hint` |
| C | W4 virt 已合；flag 默认 off；I4/测/assistant 高度缺口 |
| D | 无 outline；steer 靠 `SESSION_AGENT_CACHE` |
| E | wiki_merge 死代码；cookie Secure 不全 |
| F | W13 计划勾选滞后；parity §2 多行过时 |
