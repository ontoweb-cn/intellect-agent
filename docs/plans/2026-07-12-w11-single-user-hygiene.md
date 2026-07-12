# W11 细化稿 — 永久单用户卫生清扫

> **日期**：2026-07-12  
> **状态**：**APPROVED**（设计签字；待技术评审后执行）  
> **策略**：先文档声明 → 再代码清扫；保留 OAuth shim  
> **前置**：W0–W10 已合入（tip @ `fef7fcb` / #57）；membership 空壳清理 #53–#57  
> **产品决议**：intellect-agent **永久单用户**；隔离 = profile / `INTELLECT_HOME`；用户选卫生档 **2**  
> **父文档**：[`2026-07-12-w10-profile-journey-p1-3.md`](./2026-07-12-w10-profile-journey-p1-3.md)、[`2026-07-11-p1-journey-and-webui-parity-refinement.md`](./2026-07-11-p1-journey-and-webui-parity-refinement.md) §1.4  

> **For agentic workers:** 执行时用 subagent-driven-development 或 executing-plans，按任务勾选推进。

**Goal:** 在永久单用户前提下，关掉「假 multi-user」表面与误导文案，保留 `MembershipDB` 给 `OAuthEngine`，并结清 W10 验收债。

**Architecture:** 不重做 members；删除/卸载死路由与幽灵 slash；auth 不再为 member OAuth 留公开展开；文档/W10 将 W10.1 标 WONTFIX。Profile 门控与 `agent/oauth/` 不动。

**Tech Stack:** Python WebUI (`webui/api/*`)、`intellect_cli/commands/registry.py`、docs / CHANGELOG。

---

## 0. 为何是「下一个」

| 轨 | 现状 | W11 取舍 |
|----|------|---------|
| W10 进程-profile Journey | ✅ | **不改** |
| W10.1 真 members | 曾 backlog | **WONTFIX** |
| 死表面 | wiki contributions 恒 401；registry 仍定义 `/team`…；auth oauth carve-out | **删/卸** |
| OAuth provider | `MembershipDB` + `agent/oauth/` 活着 | **保留 shim** |
| Profiles 门控 | 默认 false（W10） | **保留** |

**一句话**：诚实单用户；清假 multi-user 皮；OAuth 与 profile 门控不动。

---

## 1. 锁（S1–S12）

| ID | 锁 | 决议 |
|----|-----|------|
| **S1** | 用户模型 | **永久单用户** |
| **S2** | 隔离 | 仅 profile / `INTELLECT_HOME`（`-p`） |
| **S3** | W10.1 | **WONTFIX**；P1-3 member 半截 **关闭** |
| **S4** | OAuth shim | **保留** `agent/membership.py`（`MembershipDB`/`Store`）+ `agent/oauth/` |
| **S5** | DB schema | **不**删 `state.db` member/wiki_contributions 表 DDL（升级路径） |
| **S6** | Profile 门控 | **保留** `profiles.management_enabled`（默认 false） |
| **S7** | wiki contributions | **卸载路由**；可删 handler 文件或留 unreferenced（优先删挂载 + 文件若无引用） |
| **S8** | Gateway slash | **移除** members 门控那批 `CommandDef`（`team`/`teams`/`project`/`projects`/`join`/`join-project`/`login`/`logout`）出 registry，或等价永不进 gateway/CLI help |
| **S9** | Auth carve-out | 去掉 `check_auth` / `PUBLIC_PATHS` 中 `/api/members/oauth*` 特殊放行 |
| **S10** | 迁移 | **不** bump `_config_version`；**不**改写用户 yaml 里的 `members.*` |
| **S11** | FE | `members.js` / i18n member-oauth 文案：隐藏入口或标注 removed（随 WebUI 现状；无独立 multi-user UI 要求） |
| **S12** | 非目标 | 重做 members；删 oauth 包；默认开 Profiles；SessionChannel；outline；Secure cookie 大改 |

---

## 2. Definition of Done

| 必须 | 不在 W11 DoD |
|------|-------------|
| 文档：永久单用户 + W10.1 WONTFIX | 实现真 multi-user |
| wiki contributions HTTP 挂载消失（404/未路由） | 删 SQLite DDL |
| gateway help/menus **无** `/team` `/login` 等 members 命令 | 改 W7–W10 行为 |
| auth 不再为 member oauth 路径公开展开 | 删 `MembershipDB` |
| CHANGELOG Unreleased 一句 | `_config_version` bump |
| 相关单测绿 | M1–M4 可记入手工清单（CI 不强制） |

---

## 3. 实施切片

```text
0  docs: WONTFIX + 永久单用户声明 + CHANGELOG + 指针更新
        │
        ▼
1  sweep: registry 幽灵命令 + wiki 路由卸载 + auth carve-out + FE 入口
        │
        ▼
2  tests + 可选 M1–M4 手工勾选
```

### 建议 PR 拆分

| PR | 内容 |
|----|------|
| **W11-docs** | 本计划 APPROVED 合入 + W10 状态修订 + AGENTS/用户文档 + CHANGELOG |
| **W11-sweep** | §B 代码清扫 + 测试 |
| **W11-qa** | 可选：手工验收记录（可只更新计划勾选，无需代码） |

---

## 4. 任务清单（执行用）

### Task 0 — 文档声明（W11-docs）

- [ ] 0.1 将本文件状态保持 **APPROVED**；修订历史追加签字行  
- [ ] 0.2 更新 `docs/plans/2026-07-12-w10-profile-journey-p1-3.md`：W10.1 → **WONTFIX**；「member 半截」→ **关闭**  
- [ ] 0.3 更新 `docs/plans/2026-07-11-webui-hermes-parity-analysis.md`（或等价索引）下一拍指针 → W11  
- [ ] 0.4 `AGENTS.md`：永久单用户一句；W10.1 不做  
- [ ] 0.5 `website/docs/user-guide/profiles.md`（+ zh-Hans）：隔离 = profile；勿依赖 `members.enabled`  
- [ ] 0.6 `CHANGELOG.md` Unreleased：永久单用户卫生清扫说明  
- [ ] 0.7 Commit + docs PR  

### Task 1 — Registry：移除幽灵 slash（W11-sweep）

**Files:** `intellect_cli/commands/registry.py`；相关 gateway help 测试（若有）

- [ ] 1.1 删除（或移出 `COMMAND_REGISTRY`）下列 `CommandDef`：`team`, `teams`, `project`, `projects`, `join`, `join-project`, `login`, `logout`（members 门控批）  
- [ ] 1.2 确认 gateway handlers 若仍 dispatch 这些名：返回「removed」或走 unknown（优先与现有 removed 文案一致）  
- [ ] 1.3 测试：`gateway_help_lines()` / `_resolve_config_gates()` 不含上述名；即使 yaml `members.enabled: true` 也不出现  
- [ ] 1.4 Commit  

### Task 2 — Wiki contributions 卸载

**Files:** `webui/api/routes.py`；`webui/api/wiki_contributions_handlers.py`；可能的 static 引用

- [ ] 2.1 去掉 `routes.py` 中 list/get/create/post 对 wiki contributions 的挂载  
- [ ] 2.2 删除 `wiki_contributions_handlers.py`（若无其他引用）  
- [ ] 2.3 扫 `webui/static` / i18n 中贡献审稿入口；隐藏或移除  
- [ ] 2.4 测试：相关 path → 未处理（404）或明确 404  
- [ ] 2.5 Commit（可与 Task 1 同 PR）  

### Task 3 — Auth carve-out

**Files:** `webui/api/auth.py`

- [ ] 3.1 从 `PUBLIC_PATHS`（或等价）移除 `/api/members/oauth/providers` 等  
- [ ] 3.2 删除 `check_auth` 中 `parsed.path.startswith('/api/members/oauth/')` 分支  
- [ ] 3.3 确认 thin `members.handle_*` 仍对 `/api/members/*` 返回 404（status 除外）  
- [ ] 3.4 测试：未登录访问 `/api/members/oauth/providers` 走正常 auth（401/302），而非「公开」  
- [ ] 3.5 Commit  

### Task 4 — FE / i18n 误导收敛

**Files:** `webui/static/members.js`；`webui/static/i18n.js`；panels 入口（若有）

- [ ] 4.1 Members OAuth setup UI：不展示可用流程，或仅显示「removed / use intellect oauth」  
- [ ] 4.2 i18n：`members_setup_callback_hint` 等改为指向 provider OAuth 或删除死文案  
- [ ] 4.3 Commit  

### Task 5 — 回归与手工验收

- [ ] 5.1 `pytest`：`test_membership_stub`、`test_members_api_thin`、`test_members_security`、registry/help 相关  
- [ ] 5.2 手工 **M1–M4**（W10 遗留，记入下方勾选）  
- [ ] 5.3 Sweep PR + merge  

### 手工验收（W10 遗留 → W11-qa）

- [ ] **M1** 无显式 `management_enabled`：Profiles 隐藏；CLI create 拒  
- [ ] **M2** 显式 `true` + 重启：Profiles 可用  
- [ ] **M3**（可选）`-p A` vs `-p B` `/journey list` 不同  
- [ ] **M4** 管理开时 WebUI Journey 仍按 cookie profile  

---

## 5. 触点速查

| 层 | 路径 |
|----|------|
| Docs | `docs/plans/2026-07-12-w11-*.md`；W10 计划；parity 索引；`AGENTS.md`；`website/docs/user-guide/profiles.md`；`CHANGELOG.md` |
| Registry | `intellect_cli/commands/registry.py` |
| WebUI | `webui/api/routes.py`；`wiki_contributions_handlers.py`；`webui/api/auth.py`；`webui/static/members.js`；`i18n.js` |
| Keep | `agent/membership.py`；`agent/oauth/**`；`webui/api/members.py`（thin status）；`profile_gate` |

---

## 6. 非目标

- W10.1 / 真 `resolve_member_id` Journey  
- 删除 `MembershipDB` / `agent/oauth` / member 表迁移  
- 默认开启 Profiles；yaml 自动清除 `members.*`  
- SessionChannel；outline；Gateway 层 C；改 W7–W9  

---

## 7. 签字表

| # | 问题 | 决议 | ☐ |
|---|------|------|---|
| R1 | 永久单用户？ | **是** | ☑ |
| R2 | 清扫档？ | **2 卫生清扫** | ☑ |
| R3 | 保留 OAuth shim？ | **是** | ☑ |
| R4 | 删 DB DDL？ | **否** | ☑ |
| R5 | 拆 PR？ | docs → sweep | ☑ |
| R6 | W10.1？ | **WONTFIX** | ☑ |

---

## 8. 修订历史

| 日期 | 说明 |
|------|------|
| 2026-07-12 | DRAFT → APPROVED（用户选档 2 + Approve） |
