# W13 细化稿 — Gateway lifecycle（RFC→层 C）∥ directory-fd openat（边界→Tier）

> **日期**：2026-07-12  
> **状态**：**DONE** @ `feeba35`（#64）  
> **策略**：双轨可拆 PR — **轨 G** = RFC + profile 生命周期契约 + Gateway 层 C；**轨 O** = 硬化边界钉死 + openat Tier A/B（Tier C 可后拆）  
> **前置**：W12 DONE @ `348140e`（#61/#62/#63）；W7 S3 / W12 L7–L8 / parity §4 DECIDED #1/#4  
> **父文档**：[`2026-07-11-webui-hermes-parity-analysis.md`](./2026-07-11-webui-hermes-parity-analysis.md) §4；[`2026-07-12-w7-p2-security.md`](./2026-07-12-w7-p2-security.md)；[`2026-07-12-w12-journey-ideal-and-openat-narrow.md`](./2026-07-12-w12-journey-ideal-and-openat-narrow.md)  
> **产品锁**：永久单用户；同机部署前提；分容器 Restart 假成功另案；SessionChannel / outline **不进**

> **For agentic workers:** 技术评审吸收后再实施；轨 G 与轨 O 可并行开发、串行或随后合入。

**Goal:** 补齐同机 Gateway 显式 lifecycle（层 C）前的契约与 profile 作用域对齐；把 WebUI workspace I/O 从「resolve + leaf O_NOFOLLOW」推进到可宣称的 dir-fd openat 硬化深度，并钉死非目标边界。

**Architecture:** 双轨 — G 先 RFC 再 API/面板；O 先边界锁再 Tier A→B（C 可选）。共享原则：诚实 residual、不混 watcher、不碰 agent `tools/`。

**Tech Stack:** `docs/webui/rfcs/`；`webui/api/gateway_lifecycle.py`；`webui/api/workspace_io.py`；`routes.py` / `panels.js`；POSIX `openat`/`O_DIRECTORY`/`O_NOFOLLOW`。

---

## 0. 方案对比（组合 1 落地）

| 方案 | 内容 | 估时 | 取舍 |
|------|------|------|------|
| **G-only** | RFC + 层 C | 4–6d | 运维收益；安全债不动 |
| **O-only** | 边界 + Tier A/B | 5–8d | 无 Gateway UX |
| **G+O（选定）** | 双轨一文档、拆 PR | 8–12d | 契约与硬化齐推；可并行 |
| **O-Tier-C 同拍** | 含 list_dir/zip 全量 | +5–8d | 面过大 → **本拍默认不做** |

**选定**：**G+O**；轨 O 默认 **Tier A+B**；Tier C = 可丢 follow-up。

---

## 1. 锁（L1–L14）

### 轨 G — Gateway

| ID | 锁 | 决议 |
|----|-----|------|
| **L1** | 前置 | **必须先**合入 / 定稿 `docs/webui/rfcs/gateway-lifecycle-same-host.md`，再开层 C 路由 |
| **L2** | 作用域 | Lifecycle **跟随 WebUI 活动 profile**（`INTELLECT_HOME` / `--profile`，与 `intellect gateway restart` 一致）— DECIDED #1 |
| **L3** | 健康探测对齐 | **禁止纯 (a)**（只读活动 profile PID、无 fallback）。本拍默认 **(a′)**：优先活动 profile `gateway.pid` **当其存在且 live**；否则 **fallback root** PID，并在 payload 暴露 `probe_scope: "active_profile" \| "root_fallback"`、`active_profile_pid` / `root_pid`（可空）。Banner/Restart 文案必须显示 scope。备选锁定 **(b)**（始终双状态并列）仅当 (a′) 不可行 |
| **L4** | 层 C API | `POST /api/gateway/{start,stop,restart}`；复用 `gateway_lifecycle.py` 锁/状态机；**禁止**新建平行 restart 模块 |
| **L5** | 冲突 / 状态机 | 进行中 → **409** + `status: busy`。`_STATE` **必须**带 `operation: "restart"\|"start"\|"stop"\|null`。`ensure_gateway_restarted_for_agent_update` **仅**在 `operation=="restart"` 且 `completed` 时算证明成功；start/stop 的 completed **不得**让 updates prove 通过 |
| **L6** | 同步等待 | 设置面板默认 `wait=true`，硬上限 **60s**（CLI subprocess 可仍 120s 内部）；banner 可保持短轮询 |
| **L7** | 非目标 | 不重启 WebUI；不调用/混用 `gateway_watcher`；不分容器假成功；不 `--all` 误杀多 profile |
| **L8** | 更新挂钩 | 保持 DECIDED #4：`busy`/`failed` → 更新硬失败；`in_progress` 可算成功 |

### 轨 O — openat

| ID | 锁 | 决议 |
|----|-----|------|
| **L9** | 边界文档 | 计划 §2 + `workspace_io` 模块头必须写明：**Tier 深度表**、Windows residual、禁碰 agent `tools/` |
| **L10** | Symlink | **保持 W7 S3 / W12 L8**：in-root 允许；escape 拒；open = resolve + leaf `O_NOFOLLOW`；**禁止**一律拒 symlink leaf |
| **L11** | 本拍范围 | **Tier A + Tier B**（见 §2）；**Tier C**（`list_dir` / folder-zip 全量 fd 漫步 / 树内 rmtree 加固）= **可后拆 tip**，不进 G/O merge gate |
| **L12** | 平台 | POSIX（Linux/macOS）dir-fd 全能力；Windows = containment + 现有 symlink leaf 拒绝；文档标 degraded |
| **L13** | 非目标 | agent `tools/path_security` / approval / Rust sandbox；attachment inbox 整仓 openat（可另 RFC）；Gateway 层 C 与 O **无代码耦合** |
| **L14** | 拆 PR | **W13-RFC**（轨 G 契约）→ **W13-G**（层 C）∥ **W13-O**（Tier A/B）；可选 **W13-O-C** tip |

---

## 2. 轨 O — 硬化深度与非目标边界（钉死）

### 2.1 Tier 表（SoT）

| Tier | 深度 | Ops | TOCTOU 宣称 | 本拍 |
|------|------|-----|-------------|------|
| **今日（W7/W12c）** | resolve + leaf `O_NOFOLLOW`；delete/rename 集中 | read/write/open；unlink/rmtree/rename | **非** TOCTOU-closed | 已合入 |
| **A** | 消灭裸 `Path.open`/`read_bytes` 热路径 | `_serve_file_bytes`、inline HTML、`read_file_content` 经 helpers | 仍 leaf-only | **必须** |
| **B** | workspace **dir fd** + `openat`/`unlinkat`/`renameat`/`mkdirat`（相对名） | unlink、rename、mkdir、单文件 create/open；**API 仍接受多分量路径**（如 `nested/a.txt`） | **宣称**仅限「resolve 后最后一跳」POSIX closed；父链 residual → Tier C | **必须** |
| **C** | 路径分量链式 `openat(O_NOFOLLOW)` | `list_dir`、folder zip walk、加固 `rmtree` | 树操作 closed | **可选 tip** |

### 2.2 明确不进本拍

- 改 agent `tools/` 沙箱 / `path_security`  
- 把 attachment root 与 session workspace 合成一套 openat  
- CORS / Trusted proxy 重开（W7 已拍）  
- 「营销式」宣称 W12c 已 TOCTOU-closed  

### 2.3 触点速查（轨 O）

| 层 | 路径 |
|----|------|
| Core | `webui/api/workspace_io.py` |
| Serve | `routes.py` `_serve_file_bytes` / inline HTML ~9816–10407 |
| List | `workspace.py` `list_dir`（**Tier C only**） |
| Git discard | `workspace_git.py` ~979 — Tier B 可选迁入 helpers |
| Tests | `tests/webui/test_workspace_anchored_io.py` |
| Keep out | `tools/path_security.py`、`tools/file_tools.py`、Rust sandbox |

---

## 3. 轨 G — RFC 契约要点（实施前写进 RFC 文件）

### 3.1 问题

1. 层 A/B 已有（`gateway_lifecycle.py` + health restart + updates 挂钩），**层 C 缺失**。  
2. **健康探针读 root PID**，Restart 跟活动 profile → 多 profile 同机易误导。  
3. Settings 仅只读 `GET /api/gateway/status`；无 start/stop。  
4. 易与 `gateway_watcher`（WebUI 内 state.db 轮询）混淆。

### 3.2 HTTP 契约（草案 → RFC 定稿）

| Method | Path | Body | 成功 | 冲突 |
|--------|------|------|------|------|
| POST | `/api/gateway/restart` | `{wait?: bool}` 默认 true | 200 + status | 409 busy |
| POST | `/api/gateway/start` | `{wait?: bool}` | 200 | 409 busy / already running |
| POST | `/api/gateway/stop` | `{wait?: bool}` | 200 | 409 busy / not running |
| GET | `/api/gateway/status` | — | 扩展：`active_profile`、`scope`、双 PID 字段（若选 L3-b） | — |
| GET | `/api/health/restart/status` | — | **保留**（banner）；可与层 C 共享 `_STATE` | — |

**CLI 映射：** `intellect gateway {start,stop,restart}` + 活动 `--profile` / `INTELLECT_HOME`。  
**安全：** 认证用户 + 现有 CSRF；不回传完整 stdout/stderr。  
**同机诚实：** cross-container / PID namespace 不明时 status 标 `inconclusive`，禁止假 `completed`。

### 3.3 UI

- Settings → System `#gatewayStatusCard`：Start / Stop / Restart（busy 禁用 + 短错误）  
- Banner：文案含「当前 profile」；Restart 可继续走 `/api/health/restart` **或** 统一到 `/api/gateway/restart`（RFC 钉死其一；**推荐统一到层 C，health 变薄包装**）

---

## 4. 实施切片

```text
0  本文件 REVISED → 开干
        │
        ├──────────────────────────────┐
        ▼                              ▼
W13-RFC（轨 G 契约）              W13-O Tier A/B（可不依赖 RFC 先合）
        │                              │
        ▼                              ▼
W13-G 层 C API + 面板            （模块头 Tier 表 + 测）
        │                              │
        └────────── tip: W13-O-C ──────┘
```

| PR | 内容 | Merge gate |
|----|------|------------|
| **W13-RFC** | `gateway-lifecycle-same-host.md` + L3(a′) | 契约自洽 |
| **W13-G** | start/stop/restart + status 对齐 + 面板 + 测 | **RFC 已合**（或同 PR 含 RFC） |
| **W13-O** | Tier A+B + 测 | §2 边界注释在模块头；**不依赖** W13-RFC |
| **W13-O-C** | Tier C tip | 可丢 |

**回滚：** L3 探针可独立回退到纯 root；轨 O Tier B 可 fallback 今日 resolve+leaf helper，**不改**对外 API 形状。

---

## 5. 任务清单

### Task 0 — 计划与评审

- [x] 0.1 本文件 **READY FOR REVIEW**  
- [x] 0.2 code-reviewer 技术评审 → 吸收为 REVISED（L3(a′)、`operation`、Tier B UX）  
- [x] 0.3 parity 分析追加 W13 指针  
- [x] 0.4 用户确认组合 1 + 实施  

### Task 1 — RFC（轨 G 前置）

**Files:** Create `docs/webui/rfcs/gateway-lifecycle-same-host.md`

- [x] 1.1 写 RFC：L1–L8、HTTP 表、CLI 映射、watcher 分离、同机诚实、DECIDED #1/#4  
- [x] 1.2 **钉死 L3(a′)**：active live → else root_fallback + `probe_scope`  
- [x] 1.3 banner = 层 C 薄包装；health restart 委托同一 helper  

### Task 2 — Gateway 层 C

**Files:** `webui/api/gateway_lifecycle.py`；`webui/api/routes.py`；`webui/api/agent_health.py`；`webui/static/panels.js`；`webui/static/ui.js`；`webui/static/index.html`；`tests/webui/test_gateway_lifecycle_and_wakeup.py`

- [x] 2.1 扩展 lifecycle：`request_gateway_start/stop/restart` 共享锁 + **`operation` 字段**  
- [x] 2.2 路由 `POST /api/gateway/{start,stop,restart}`；409 busy；wait 60s；**CSRF 非豁免**  
- [x] 2.3 L3(a′)：health/status 探针 + `probe_scope` 字段  
- [x] 2.4 Settings 按钮 + banner「当前 profile / scope」；in-gateway 拒 **start/stop/restart**  
- [x] 2.5 测：busy 409；profile argv；start when running / stop when stopped；**stop completed 不得让 updates prove 成功**；CSRF 403；L3 matrix（root-only + profile WebUI）  
- [x] 2.6 `scripts/run_tests.sh` 相关绿  

### Task 3 — openat 边界注释 + Tier A

**Files:** `workspace_io.py`；`routes.py` serve 路径；`workspace.py`；tests

- [x] 3.1 模块头写入 Tier 表 + Windows residual + keep-out `tools/`  
- [x] 3.2 `_serve_file_bytes` / inline HTML / 多余裸 open → `read_bytes_under_root` / `open_under_root`  
- [x] 3.3 测：穿越仍拒；in-root symlink 可读  

### Task 4 — openat Tier B

**Files:** `workspace_io.py`；routes mkdir/delete/rename/create；tests

- [x] 4.1 `open_root_dir_fd(root)` + `openat`/`unlinkat`/`renameat`/`mkdirat` 封装（POSIX）  
- [x] 4.2 改 unlink/rename/mkdir/create：**多分量路径仍接受**；dir-fd 仅最后一跳；父链 residual 诚实  
- [x] 4.3 Windows：fallback 今日 containment；测 skip 或 degraded assert  
- [x] 4.4 测：多分量 `nested/a.txt` 不回归；symlink escape；契约测  
- [x] 4.5 **不**改 `list_dir`（留给 Tier C）；git discard **不进** merge gate  

### Task 5 — 合入

- [x] 5.1 W13-RFC merge  
- [x] 5.2 W13-G merge  
- [x] 5.3 W13-O merge  
- [ ] 5.4 可选 W13-O-C — **跳过 → W14-A**（[`2026-07-12-w14-candidates-a-f.md`](./2026-07-12-w14-candidates-a-f.md)）  
- [x] 5.5 本文件勾选 + parity 索引  

---

## 6. 非目标

- SessionChannel；outline；Secure cookie；wiki_merge  
- 默认开 Profiles management；真 multi-user  
- 分容器 gateway 管控  
- agent tools 沙箱与 WebUI openat 合并  
- Tier C 作为本拍 merge gate  

---

## 7. 风险与回滚

| 风险 | 缓解 |
|------|------|
| L3 改 health 探针破坏「只盯 root 守护进程」的部署 | RFC 写清；若用户坚持 root-only 则退回 L3(b) |
| start/stop 误伤 systemd 托管实例 | 复用 CLI 既有 systemd/launchd 探测路径；测 mock CLI |
| Tier B 收窄路由拒 nested | **禁止**；多分量 UX 保留；closed 宣称仅最后一跳 |
| 夸大 TOCTOU 关闭 | 文档按 Tier 表宣称；禁止「W13 = 全仓 closed」 |

---

## 8. 评审清单（R1–R10）

| ID | 问题 | 期望 | 签 |
|----|------|------|-----|
| R1 | 双轨拆 PR？ | **是** | ☐ |
| R2 | L3 默认 (a′) 含 root fallback？ | **是**（禁纯 (a)） | ☑ |
| R3 | 层 C 复用 `gateway_lifecycle` + `operation`？ | **是** | ☑ |
| R4 | wait 默认 60s 面板？ | **是** | ☑ |
| R5 | Tier C 不进 merge gate？ | **是** | ☑ |
| R6 | 禁碰 agent tools？ | **是** | ☑ |
| R7 | Symlink 保持 W7 S3？ | **是** | ☑ |
| R8 | banner 统一到层 C？ | **是**（薄包装） | ☑ |
| R9 | Windows degraded 可接受？ | **是** | ☑ |
| R10 | W13-O 可不依赖 RFC 先合？ | **是** | ☑ |

---

## 9. 现状锚点（评审用）

| 项 | 现状 |
|----|------|
| 层 A/B | `webui/api/gateway_lifecycle.py`；`POST /api/health/restart`；updates 挂钩 |
| 层 C | **缺失**；仅 `GET /api/gateway/status` 只读 |
| Health PID | `agent_health._gateway_root_pid_path()` → **root** |
| Restart PID | 活动 profile `get_intellect_home()` |
| Watcher | `gateway_watcher` = state.db SSE；**无** `restart_watcher_for_profile` |
| openat | W7 leaf `O_NOFOLLOW`；W12c delete/rename 集中；**非** dir-fd |
| 裸 open 热路径 | `_serve_file_bytes`、`list_dir`、mkdir、zip walk、git discard |
| RFC 缺口 | `docs/webui/rfcs/gateway-lifecycle-same-host.md` **未写** |
