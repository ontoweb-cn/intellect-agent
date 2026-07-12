# W11 细化稿 — 永久单用户卫生清扫

> **日期**：2026-07-12  
> **状态**：**DONE** — docs #58 + sweep #59 @ `3fba844`  
> **策略**：先文档声明 → 再 **单 PR sweep**（registry+wiki+auth+FE）；保留 OAuth shim  
> **前置**：W0–W10 已合入（tip @ `fef7fcb` / #57）；membership 空壳清理 #53–#57  
> **产品决议**：intellect-agent **永久单用户**；隔离 = profile / `INTELLECT_HOME`；用户选卫生档 **2**  
> **计划评审**：Request changes → 本修订钉死 S9 保 status / Task 2–4 触点  
> **父文档**：[`2026-07-12-w10-profile-journey-p1-3.md`](./2026-07-12-w10-profile-journey-p1-3.md)、[`2026-07-11-p1-journey-and-webui-parity-refinement.md`](./2026-07-11-p1-journey-and-webui-parity-refinement.md) §1.4  

> **For agentic workers:** 执行时用 subagent-driven-development 或 executing-plans，按任务勾选推进。

**Goal:** 在永久单用户前提下，关掉「假 multi-user」表面与误导文案，保留 `MembershipDB` 给 `OAuthEngine`，并结清 W10 验收债。

**Architecture:** 不重做 members；删除 CommandDef 幽灵 slash；卸载 wiki contributions HTTP；auth 去掉全部 member oauth/register/login/redeem 公开展开但 **保留** `/api/members/status`；FE 收敛 login/register 多用户 chrome；`member-auth.js` status 轮询不破。

**Tech Stack:** Python WebUI (`webui/api/*`)、`intellect_cli/commands/registry.py`、docs / CHANGELOG。

---

## 0. 为何是「下一个」

| 轨 | 现状 | W11 取舍 |
|----|------|---------|
| W10 进程-profile Journey | ✅ | **不改** |
| W10.1 真 members | 曾 backlog | **WONTFIX** |
| 死表面 | wiki 挂载→今日 **401**；registry 仍有 CommandDef（#57 已藏 help）；auth 多条 member 公开路径 | **删/卸** |
| OAuth provider | `MembershipDB` + `agent/oauth/` 活着 | **保留 shim** |
| Profiles 门控 | 默认 false（W10） | **保留** |

**一句话**：诚实单用户；清假 multi-user 皮；**status 公开 + OAuth shim** 不动。

---

## 1. 锁（S1–S13）

| ID | 锁 | 决议 |
|----|-----|------|
| **S1** | 用户模型 | **永久单用户** |
| **S2** | 隔离 | 仅 profile / `INTELLECT_HOME`（`-p`） |
| **S3** | W10.1 | **WONTFIX**；P1-3 member 半截 **关闭** |
| **S4** | OAuth shim | **保留** `agent/membership.py` + `agent/oauth/`；**保留** `oauth-providers.js`（provider OAuth） |
| **S5** | DB schema | **不**删 member/wiki_contributions DDL |
| **S6** | Profile 门控 | **保留** `profiles.management_enabled`（默认 false） |
| **S7** | wiki contributions | **卸载** HTTP 挂载（今日挂载→401；卸后→**未路由 404**）；删 handler；清 badge/catalog/文档技能指针 |
| **S8** | Gateway slash | **删除** CommandDef：`team`/`teams`/`project`/`projects`/`join`/`join-project`/`login`/`logout`。**保留** `gateway/run.py` 内联「removed」回复 |
| **S9** | Auth carve-out | 去掉 **全部** member oauth/register/login/redeem 公开路径与 `check_auth` 分支；**硬性保留** `/api/members/status` ∈ `PUBLIC_PATHS` |
| **S10** | 迁移 | **不** bump `_config_version`；**不**改写用户 yaml |
| **S11** | FE | 主攻 **loaded**：`login.js` / `register.js` / login·register HTML / i18n `members_*`。**硬性保留** `member-auth.js` → `/api/members/status`。`members.js`/`teams.js`/`projects.js` 可选孤儿删 |
| **S12** | 非目标 | 重做 members；删 oauth 包；默认开 Profiles；SessionChannel；outline；Secure cookie；`wiki_merge.py` 可留（明示延期） |
| **S13** | CSRF | **不**改 `_csrf_exempt_path`（member oauth 本就不在列表；member CSRF 旁路已死） |

---

## 2. Definition of Done

| 必须 | 不在 W11 DoD |
|------|-------------|
| 文档：永久单用户 + W10.1 WONTFIX | 真 multi-user |
| wiki contributions **未挂载**（404） | 删 SQLite DDL |
| `resolve_command("team"|"login"|…)` is None；help/KNOWN 无这些名 | 改 W7–W10 |
| 未登录：`/api/members/oauth/providers`（及 login/register）→ 401/302；**`/api/members/status` → 200** | 删 `MembershipDB` |
| FE：无可用 multi-user login/register OAuth 流程误导 | `_config_version` bump |
| 相关单测绿 | M1–M4 CI 强制 |

---

## 3. 实施切片

```text
0  docs（本 PR 可先合；0.2–0.3 已部分落地 → 校验）
        │
        ▼
1  sweep（单 PR）: registry + wiki + auth(S9) + FE(S11) + tests
        │
        ▼
2  可选 M1–M4 手工
```

| PR | 内容 |
|----|------|
| **W11-docs** | 计划 REVISED + AGENTS/用户文档 + CHANGELOG |
| **W11-sweep** | Task 1–4 + 测试（**勿拆开** auth 与 FE） |
| **W11-qa** | 可选手工勾选 |

---

## 4. 任务清单

### Task 0 — 文档声明（W11-docs）

- [x] 0.1 本文件状态 **REVISED/执行中**；修订历史追加评审行  
- [x] 0.2 **校验** W10 计划已 WONTFIX（幂等）  
- [x] 0.3 **校验** parity 索引指向 W11（幂等）  
- [x] 0.4 `AGENTS.md`：永久单用户；W10.1 WONTFIX  
- [x] 0.5 `website/docs/user-guide/profiles.md`（+ zh-Hans）：隔离 = profile；`members.enabled` 无效  
- [x] 0.6 `CHANGELOG.md` Unreleased  
- [x] 0.7 Docs PR merge  

### Task 1 — Registry 幽灵 slash

**Files:** `intellect_cli/commands/registry.py`；可选清 `gateway/command_handlers` 死方法 / `DEPRECATED_COMMANDS`

- [x] 1.1 删除 CommandDef：`team`, `teams`, `project`, `projects`, `join`, `join-project`, `login`, `logout`  
- [x] 1.2 **保留** `gateway/run.py` 内联 deprecation「removed in v0.5.0」  
- [x] 1.3 测试：`resolve_command` 上述名为 None；∉ `GATEWAY_KNOWN_COMMANDS` / `gateway_help_lines()`；yaml `members.enabled: true` 仍不出现  
- [x] 1.4 （可选）删未接入 dispatch 的 `_handle_team_*` / `_handle_member_login_*`  

### Task 2 — Wiki contributions 卸载

**Files:** `webui/api/routes.py`；`wiki_contributions_handlers.py`；`wiki-panel.js`；catalog pending；用户/技能文档

- [x] 2.1 卸 `routes.py` list/get/create/post 挂载（今日 401 → 卸后 **404**）  
- [x] 2.2 删 `wiki_contributions_handlers.py`  
- [x] 2.3 清 `wiki-panel.js` pending badge；`_build_wiki_global_catalog_entry` pending count  
- [x] 2.4 更新 `website/docs/.../llm-wiki.md` + `skills/research/llm-wiki/SKILL.md`（及 bundled 镜像）：去掉 contribution review API  
- [x] 2.5 `wiki_merge.py`：**延期**（S12），计划注明  
- [x] 2.6 测试：`GET/POST /api/wiki/contributions` → 未处理/404  

### Task 3 — Auth carve-out（钉死 status）

**Files:** `webui/api/auth.py`

- [x] 3.1 从 `PUBLIC_PATHS` 移除：`/api/members/oauth/providers`、`register*`、`/api/members/login`（**保留** `/api/members/status`）  
- [x] 3.2 删 `check_auth` 分支：`/api/members/oauth/`、`/api/members/redeem`、`register`/`login` 前缀  
- [x] 3.3 **不**改 CSRF exempt 列表（S13）  
- [x] 3.4 测试：auth 开启时未登录 → oauth/providers + login + register → **401/302**；status → **200** `enabled:false`  
- [x] 3.5 （可选）`_members_flag_from_config` raw yaml 残差 → 已知 S12 或随手收紧  

### Task 4 — FE（loaded 表面）

**Files:** `login.js`；`register.js`；`routes.py` login/register HTML；`i18n.js`；**勿伤** `member-auth.js`

- [x] 4.1 隐藏/折叠 multi-user login（OAuth/password/redeem tabs）与 register 流程  
- [x] 4.2 i18n `members_*` → removed / 指向 `intellect oauth` 或 provider OAuth UI  
- [x] 4.3 确认 `member-auth.js` `fetchMembersStatus` 仍工作  
- [x] 4.4 （可选）删未加载的 `members.js` / `teams.js` / `projects.js`  
- [x] 4.5 **勿动** `oauth-providers.js`（S4）  

### Task 5 — 回归

- [x] 5.1 `pytest`：membership stub / members thin / members security + 本拍新测  
- [x] 5.2 Sweep PR merge  
- [x] 5.3 可选 M1–M4  

### 手工验收（W11-qa）

- [x] **M1** 无显式 key：Profiles 隐藏；CLI create 拒  
- [x] **M2** 显式 `true` + 重启：Profiles 可用  
- [x] **M3**（可选）`-p A` vs `-p B` `/journey list`  
- [x] **M4** 管理开时 WebUI Journey 按 cookie profile  

> 2026-07-12 验收：M1–M4 用自动化等价检查通过（DEFAULT/CLI create、explicit true、
> process-home A≠B、management off→default / on→TLS cookie profile）；并跑
> `test_profile_gate` + `test_journey_profile_home`。

---

## 5. 触点速查

| 层 | 路径 |
|----|------|
| Docs | 本文件；W10；parity；`AGENTS.md`；profiles.md；CHANGELOG；llm-wiki 用户/技能文档 |
| Registry | `intellect_cli/commands/registry.py`；`gateway/run.py`（保留 removed） |
| Wiki | `routes.py`；handlers；`wiki-panel.js`；catalog pending |
| Auth | `webui/api/auth.py`（**keep status**） |
| FE | `login.js`；`register.js`；`member-auth.js`（keep）；`i18n.js` |
| Keep | `agent/membership.py`；`agent/oauth/**`；thin `members.py`；`oauth-providers.js`；`profile_gate` |

---

## 6. 非目标

- W10.1 / 真 members  
- 删 `MembershipDB` / `agent/oauth` / DDL  
- 默认开 Profiles；自动清 yaml `members.*`  
- SessionChannel；outline；Secure cookie；强制清 `wiki_merge.py`  

---

## 7. 签字表

| # | 问题 | 决议 | ☐ |
|---|------|------|---|
| R1 | 永久单用户？ | **是** | ☑ |
| R2 | 清扫档？ | **2** | ☑ |
| R3 | 保留 OAuth shim？ | **是** | ☑ |
| R4 | 删 DB DDL？ | **否** | ☑ |
| R5 | 拆 PR？ | docs → **单** sweep | ☑ |
| R6 | W10.1？ | **WONTFIX** | ☑ |
| R7 | 保留 `/api/members/status` 公开？ | **是（钉死）** | ☑ |
| R8 | FE 主攻 loaded login/register？ | **是** | ☑ |

---

## 8. 修订历史

| 日期 | 说明 |
|------|------|
| 2026-07-12 | DRAFT → APPROVED（用户选档 2） |
| 2026-07-12 | REVISED — 评审：S9 全量 carve-out+keep status；Task2 docs/badge；Task4 改 login/register；Task1 保留 run.py removed；S13 CSRF |
