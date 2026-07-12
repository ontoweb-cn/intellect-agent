# Intellect WebUI 改进分析（对照 Hermes 2026-06-07 → 2026-07-11）

> **日期**：2026-07-11  
> **状态**：REVIEWED — 分析评审稿（非实施计划）  
> **来源**：`~/workspace/hermes-webui/hermes-webui-updates-2026-06-07-to-2026-07-11.md`  
> **Hermes Git 范围**：`f1f56a90`（v0.51.312）…`62a185e4`（exp-v0.52.28）  
> **部署前提（已确认）**：Intellect WebUI 与 messaging gateway **同机**部署（共享 PID namespace，可 shell-out）  
> **对照代码**：`intellect-agent/webui/`（HEAD 于分析日）

---

## 0. 评审摘要

本文件是对「Hermes 本窗口更新 → Intellect WebUI 改进计划」的**技术评审定稿**。相对口头梳理，评审修正了以下点：

| # | 原稿/口头表述 | 评审结论 |
|---|---------------|----------|
| R1 | 「gateway 重启模块 Hermes 专有、不宜照搬」 | **同机部署下应升级为可做项**；照搬的是模式，不是文件名/CLI 字符串 |
| R2 | 「Intellect gateway 是 root-level 单例」 | **部分正确、需细化**：`agent_health` 健康探测刻意读 root `gateway.pid`；但 CLI/`stop_profile_gateway` 支持 **INTELLECT_HOME 作用域** 与 systemd `--profile` 后缀。重启 helper 必须与 `intellect gateway restart` 的 profile 约定对齐，不能假设「永远只重启 root」 |
| R3 | 「Hermes Session SSE Phase-1 可直接对齐」 | Hermes **明确尚无客户端消费**；Intellect 已有 stream 级 `run_journal` + `after_seq` replay。应对齐**契约思想**，在现有 journal 上演进，并**同里程碑做客户端**，避免重复「只做服务端」 |
| R4 | 「Turn Anchors 完全缺失」 | Intellect 已有 `assistant-turn` + live tool-call / agent-activity group；缺的是 **持久化 `activity_scene_v1`、分层 live/replay/settled、transparent_stream 模式、settled worklog 延迟 materialize** |
| R5 | 「工作区安全完全落后」 | `safe_resolve_ws` / 系统根拦截已有；缺的是 Hermes 式 **`openat` + `O_NOFOLLOW` 组件遍历**（TOCTOU），属硬化而非从零建设 |
| R6 | 更新通道 / OIDC / outline / i18n pl·vi·cs | 保持低优先级或按需；不阻塞 P0/P1 |

**推荐落地路径**：契约优先（先 RFC，再实现），同机运维闭环（gateway 重启）可与 P0 并行小项推进。

---

## 1. 范围与方法

### 1.1 输入

- Hermes 更新总结（评审修正版）：十四节主题 + 新增模块表 + 热点文件 Δ
- Intellect `docs/webui/{README,ARCHITECTURE}.md` + `webui/api/*` + `webui/static/*` 对照检索
- 用户确认：同机部署

### 1.2 非目标

- 不整仓移植 Hermes `static/` / `api/routes.py`
- 不把 Hermes 上一窗口（05-29→06-07）的全部安全项重开一遍（仅保留与本窗口相关的 workspace hardening 缺口）
- 不把纯 i18n 语言包扩张当作核心里程碑

### 1.3 Intellect 已有、不宜重复建设的能力

| 能力 | 锚点（示意） |
|------|----------------|
| Run journal 写入 + stream replay | `webui/api/run_journal.py`，`routes._replay_run_journal` |
| StreamChannel 广播 + 离线缓冲 | `webui/api/config.py` `StreamChannel`（**无界 list，待封顶**） |
| 会话列表 SSE | `GET /api/sessions/events`，`sessions.js` EventSource |
| Kanban SSE + `Last-Event-ID` | `kanban_bridge.py`（可作 resumable SSE 参考实现） |
| Assistant turn / live activity DOM | `static/ui.js`（`assistant-turn`、`data-live-tool-call-group`） |
| `/steer` | `commands.js` + `streaming._handle_chat_steer` |
| Profile 系统 | `webui/api/profiles.py`（`INTELLECT_HOME` + cookie TLS） |
| Gateway 存活探测 | `agent_health.py` + `GET /api/gateway/status` |
| CORS preflight `Content-Length: 0` | `routes.py` 已有 |
| 侧栏会话虚拟化 | `sessions.js` `_sessionVirtualWindow` |
| 健康告警 banner | `index.html` `#agentHealthBanner`（Dismiss + Restart；W13 @ `feeba35`） |

---

## 2. 差距矩阵（Hermes 本窗口主题 → Intellect）

| # | Hermes 主题 | Intellect 现状 | 差距等级 | 建议优先级 |
|---|-------------|----------------|----------|------------|
| 1 | 可恢复会话 SSE（`/api/sessions/{id}/events` + Last-Event-ID） | stream journal + after_seq；无 per-session 可恢复契约 | **高** | P0 |
| 2 | StreamChannel 有界 deque / gap | 无界 `_offline_buffer` list | **高** | P0 |
| 3 | Wakeup 凭证耗尽暂停 | credential pool / goal pause；无 `process_wakeup_paused` 门禁 | **高** | P0 |
| 4 | Turn Anchors / Transparent Stream | 有 turn/activity DOM；无 scene 持久化与双模式 | **高** | P1 |
| 5 | Transcript 消息虚拟化 | 仅侧栏虚拟化；消息区全量 DOM | **高** | P1 |
| 6 | Provider / 会话加载竞态 | 部分 generation 注释；需审计 | **中** | P1 |
| 7 | Gateway 重启（健康 + 更新挂钩） | **W13 DONE** @ `feeba35`：层 C `POST /api/gateway/{start,stop,restart}` + banner/Settings + updates 挂钩；L3(a′) `probe_scope` | **已收口** | — |
| 8 | Trusted-proxy-first / 更新通道 | 部分 CORS/同源；无 stable/experimental 通道 | **中/低** | P2 |
| 9 | Workspace anchored I/O | **W7/W12/W13 DONE**：resolve + leaf `O_NOFOLLOW`；Tier A/B dir-fd `openat`；Tier C（`list_dir`/zip/rmtree 树 walk）→ **W14-A** | **部分收口** | W14-A |
| 10 | Office sidecar / CSV 预览下载 | MIME 有；无 OOXML 提取 sidecar | **低** | P3 / 按需 |
| 11 | Outline / 三栏布局 | 无 outline.js | **低** | P3 |
| 12 | compression_exhausted 恢复包 | **W9 DONE** @ `e04cc95`：focused continuation + `compression_anchor` | **已收口** | — |
| 13 | SessionChannel（Option X） | 无（不同于 run-journal） | **低** | P3 |
| 14 | i18n pl/vi/cs | 已有 en/zh/…/tr 等 | **低** | 按需 |

---

## 3. 分阶段改进计划

### P0 — 会话韧性

1. **Resumable Session SSE 契约**
   - 新增或演进：`GET /api/sessions/{id}/events`（`Last-Event-ID` / `after_event_id`）
   - Headers 提交前锁定 resume cursor + journal baseline（防 TOCTOU）
   - 有界回放（行字节上限 + 事件数上限）；缺口返回诚实 `session_snapshot`
   - `StreamChannel` 离线缓冲改为有界 deque（drop-oldest）+ 重连 gap 检查
   - **前端同里程碑**消费该契约（参考 `kanban_bridge` 的 Last-Event-ID 模式与 Hermes RFC `session-sse-contract-v1`）
   - 在现有 `run_journal` / stream replay 上演进，避免平行两套 journal

2. **Wakeup / 凭证耗尽暂停**
   - 暂停常量 + auditable metadata
   - `start_session_turn`（或等价入口）门禁
   - 同 model/provider 抑制重复自动 wakeup
   - 成功 run / 换模型 / 凭证变化后 clear

**验收**：断线重连不丢/不乱序关键事件；凭证耗尽后不再空转 wakeup；缓冲有上限可测。

### P1 — 长对话体验与渲染一致性

3. **Stable Assistant Turn Anchors**
   - 持久化 activity scene；live / replay / settled / inflight 一致
   - settled worklog 延迟 materialize
   - 可选 `chat_activity_display_mode`: `compact_worklog` | `transparent_stream`

4. **Transcript 虚拟窗口**
   - 消息区虚拟窗口 + 用户行固有高度记忆
   - 强化移动端 scroll / SSE 断线 follow-intent（CSS 已有 `overflow-anchor: none`）

5. **会话加载 generation token / provider 下拉硬化**
   - 快速切换不污染当前 pane
   - model/provider 状态从匹配 option value 解析，不读 stale selectedOptions

**验收**：长会话滚动稳定；刷新/重连后 turn 结构与 live 一致；快速切会话无串台。

> **P1 细化 + 技术评审（2026-07-11）**：见 [`2026-07-11-p1-journey-and-webui-parity-refinement.md`](./2026-07-11-p1-journey-and-webui-parity-refinement.md) §2（轨 B）。决议：P1-C 立即；P1-A RFC 可与 Session SSE P0 并行；P1-A/B 全量跟 P0 客户端之后。  
> **W1（2026-07-12）**：P1-C / Journey P1-1·2 已合入；下一拍见 [`2026-07-12-w1-journey-e2e-and-session-sse.md`](./2026-07-12-w1-journey-e2e-and-session-sse.md)（P1-4 E2E ∥ Session SSE RFC）。  
> **W5（2026-07-12）**：deferred worklog 已合入 `d6a94d7`；下一拍见 [`2026-07-12-w6-transparent-stream.md`](./2026-07-12-w6-transparent-stream.md)。  
> **W6（2026-07-12）**：transparent stream 已合入 `609f705`；下一拍见 [`2026-07-12-w7-p2-security.md`](./2026-07-12-w7-p2-security.md)（P2 anchored I/O + trusted proxy）。  
> **W7（2026-07-12）**：P2 I/O + trusted proxy 已合入 `e91d30c`；下一拍见 [`2026-07-12-w8-update-channel.md`](./2026-07-12-w8-update-channel.md)（P2 #9 update channel 收口）。  
> **W8（2026-07-12）**：update channel 已合入 `8e434b1`（#49）；下一拍见 [`2026-07-12-w9-compression-exhausted.md`](./2026-07-12-w9-compression-exhausted.md)（P3 #12 focused continuation）。  
> **W9（2026-07-12）**：compression_exhausted 已合入 `e04cc95`（#51）；下一拍见 [`2026-07-12-w10-profile-journey-p1-3.md`](./2026-07-12-w10-profile-journey-p1-3.md)（profile 恢复 + Journey P1-3）。  
> **W10（2026-07-12）**：profile DEFAULT false + Journey 进程-home MVP 已合入 `c567744`（#54/#55）；W10.1 WONTFIX（永久单用户）；下一拍见 [`2026-07-12-w11-single-user-hygiene.md`](./2026-07-12-w11-single-user-hygiene.md)。  
> **W11（2026-07-12）**：永久单用户卫生清扫已合入 `3fba844`（#58/#59）。
> **W12（2026-07-12）**：DONE @ `348140e` — Journey 本地 memory id（#61）+ delete/rename `workspace_io` 集中（#62）；详见 [`2026-07-12-w12-journey-ideal-and-openat-narrow.md`](./2026-07-12-w12-journey-ideal-and-openat-narrow.md)。  
> **W13（2026-07-12）**：DONE @ `feeba35`（#64）— Gateway lifecycle RFC→层 C + openat Tier A/B；Tier C 跳过 → W14-A；详见 [`2026-07-12-w13-gateway-lifecycle-and-openat.md`](./2026-07-12-w13-gateway-lifecycle-and-openat.md) + [`gateway-lifecycle-same-host.md`](../webui/rfcs/gateway-lifecycle-same-host.md)。  
> **W14（2026-07-12）**：**执行中 F+A** — 文档收口 + openat Tier C；详见 [`2026-07-12-w14-candidates-a-f.md`](./2026-07-12-w14-candidates-a-f.md)。

### P2 — 安全与同机运维

6. **Gateway lifecycle（同机，见 §4）**
7. **Workspace anchored I/O**（`openat` + `O_NOFOLLOW`）
8. **Trusted-proxy-first** 再读 `X-Forwarded-*`
9. **更新通道** `stable` / `experimental`（可选）

### P3 — 体验增强（择优）

10. Conversation outline + 三栏可读下限  
11. Gateway-owned steer 排队（无本地 agent cache）+ 可读超时可配  
12. `compression_exhausted` focused continuation  
13. SessionChannel（若 P0 会话 SSE 仍不够覆盖后台进程事件）  
14. Office 上传 sidecar / i18n 新语言  

---

## 4. Gateway / Profile 重启模块 — 专项评审

### 4.1 Hermes 实际有什么

Hermes 不是单一「profile 重启模块」，而是三层：

| 层 | 路径/入口 | 作用 |
|----|-----------|------|
| A | `api/gateway_restart.py` → `restart_active_profile_gateway()` | 活动 profile 下 `hermes gateway restart`；锁 + completed/in_progress/busy/failed |
| B | `POST /api/health/restart` + UI Restart Service | 健康告警一键恢复（#3285） |
| C | `POST /api/gateway/{start,stop,restart}` | 设置面板显式 lifecycle；`--profile`；同步 60s；冲突 409 |
| （易混） | `gateway_watcher.restart_watcher_for_profile` | **仅**重启 WebUI 内会话同步 watcher，**不**重启 messaging gateway |
| （挂钩） | `updates.py` agent 目标成功后 | 必须证明 gateway 已重启，否则更新不算成功（#5181） |

### 4.2 为何曾标「专有」

- 假设 WebUI 可 shell-out 管控同机 gateway  
- Hermes 用 `HERMES_HOME` / `--profile` 绑定活动 profile  
- 与「对话正确性」无关，属运维 UX  

在 **分容器** 部署下，PID/flock 不可靠，Restart 易假成功 —— 故不宜盲搬。

### 4.3 同机部署下的修订结论

用户确认 **Intellect 同机** → 层 A/B（及可选 C）**值得做**。

建议形态：

```
webui/api/gateway_lifecycle.py
  ├─ resolve CLI: shutil.which("intellect") 或 sys.executable 旁路
  ├─ 注入 INTELLECT_HOME / 或传递 --profile（与 intellect_cli/gateway.py 约定一致）
  ├─ 非阻塞锁；2s 快返回；后台 drain（对齐 CLI drain 超时）
  ├─ 状态：completed | in_progress | busy | failed
  └─ 调用方：
       • POST /api/health/restart  + banner Restart 按钮
       • updates.py 仅 target=agent 成功路径
       • （可选）POST /api/gateway/{start,stop,restart}
```

### 4.4 Intellect 特有约束（实施前必须写进 RFC）

1. **健康探测 vs 重启作用域不一致风险**  
   - `agent_health._gateway_root_pid_path()` 注释：健康检查读 **root** `gateway.pid`  
   - CLI `stop_profile_gateway()` / systemd 后缀：支持 **当前 INTELLECT_HOME / profile**  
   - 重启 helper 必须明确：重启的是 root gateway 还是活动 profile gateway；UI 文案不得误导  

2. **多 profile 同机**  
   - 若实际只有一个 root gateway：Restart 影响整机 messaging，文案需说明  
   - 若已按 profile 起多个 gateway：必须带正确 `--profile` / `INTELLECT_HOME`，禁止串重启  

3. **安全**  
   - 仅认证用户；与现有 CSRF/同源策略一致  
   - 不把 stdout/stderr 原样回传浏览器（可记日志，API 返回短 message）  

4. **与 watcher 分离**  
   - Profile 切换继续只动 watcher（若需要 per-profile watcher）；不要调用 gateway restart  

### 4.5 优先级

同机下建议：**P2 或与 P0 并行的独立小项**（工作量远小于 Session SSE，但运维收益直接）。  
不阻塞 P0 会话韧性。

---

## 5. 落地路径选择

| 路径 | 做法 | 评价 |
|------|------|------|
| **A. 契约优先（推荐）** | 先写 Intellect RFC（Session SSE / Turn Anchors / Gateway lifecycle），再实现 | 可测、可审、少带 Hermes 假设 |
| B. 热点移植 | 对照 Hermes 锚点文件直接搬 | 快，但易引入 HERMES_HOME / 无客户端 Phase-1 等债务 |
| C. 只做安全+运维 | 仅 P2（anchored I/O + gateway restart） | 适合审计驱动；体验债继续积 |

**推荐 A**；同机 gateway 重启可作为 A 之下的独立 RFC 小节或独立小 PR。

---

## 6. 明确不照搬

- Hermes 整包 `i18n.js` 语言扩张作为主线  
- 「只做服务端、不做客户端」的 Session SSE Phase-1  
- 把 `restart_watcher_for_profile` 与 gateway restart 混为一个模块  
- 假设分容器也可无条件 Restart（本仓库当前前提是同机；若未来分容器需另开设计）  
- Git RCE 父提交范围外项（Hermes 文档已排除）  

---

## 7. 建议的文档/RFC 产出顺序

1. `docs/webui/rfcs/session-sse-contract-v1.md`（或 `docs/plans/…`）— P0  
2. `docs/webui/rfcs/gateway-lifecycle-same-host.md` — 同机重启（可与 1 并行）  
3. `docs/webui/rfcs/stable-assistant-turn-anchors.md` — P1  
4. 本分析文件保持为 **背景与差距权威摘要**；实施计划另开 `writing-plans` 产出  

---

## 8. 开放问题 — DECIDED（2026-07-11）

| # | 问题 | 决定 | 理由（摘要） |
|---|------|------|----------------|
| 1 | Gateway 重启作用域 | **跟随 WebUI 活动 profile**（`INTELLECT_HOME` / `--profile`，与 `intellect gateway restart` 一致） | 同机多 profile 不误杀；与 CLI 对齐；仅 default/root 时行为等价。健康探测若仍读 root PID，Restart 前用活动 profile 状态再确认；UI 文案写明「当前 profile」 |
| 2 | P0 Session SSE 数据面 | **扩展现有 stream `run_journal`**，不新建平行 per-session event log | 已有写入与 `after_seq` replay；契约层补 Last-Event-ID / 有界回放 / `session_snapshot` / TOCTOU cursor；避免双写 |
| 3 | Gateway 重启排期 | **与 P0 并行的独立小 PR**（不阻塞 Session SSE，也不并入 P2 大批次） | 同机交付快、运维收益直接；与 P0 无耦合；边界清晰便于评审 |
| 4 | Agent 更新 × gateway restart | **`busy`/`failed` → 更新 API 硬失败**（`ok: false` + `gateway_restart` 状态 + 手动命令提示）；**`in_progress` → 可算成功** | 对齐 Hermes #5181，避免旧 gateway 代码踩 stale-module；`ok`+警告易被当成已可用 |

---

## 9. 参考锚点

### Hermes

- 更新总结：`hermes-webui/hermes-webui-updates-2026-06-07-to-2026-07-11.md`  
- `api/gateway_restart.py`  
- `api/routes.py`：`_handle_health_restart`、`_handle_gateway_lifecycle`  
- `api/updates.py`：`_ensure_gateway_restart_for_agent_update`  
- RFC：`docs/rfcs/session-sse-contract-v1.md`、`stable-assistant-turn-anchors.md`、`transparent-stream-activity-mode.md`  

### Intellect

- `webui/api/run_journal.py`、`config.StreamChannel`、`agent_health.py`  
- `webui/api/profiles.py`、`gateway_watcher.py`  
- `intellect_cli/gateway.py`（`--profile` / `stop_profile_gateway` / drain）  
- `webui/static/index.html` `#agentHealthBanner`（无 Restart 按钮）  

---

## 10. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07-11 | 初稿：对照 Hermes 窗口梳理差距与分期 |
| 2026-07-11 | 评审定稿：纳入同机部署前提；修正 gateway 作用域表述；明确 Session SSE 须带客户端；标出 R1–R6 |
| 2026-07-11 | §8 四项开放问题拍板为 DECIDED（活动 profile 重启；扩展 run_journal；并行小 PR；更新硬失败） |
