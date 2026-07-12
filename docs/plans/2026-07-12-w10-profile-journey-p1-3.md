# W10 细化稿 — Profile 管理恢复（opt-in）+ Journey P1-3（Gateway scope）

> **日期**：2026-07-12  
> **状态**：**REVISED** — 待签字（已吸收计划评审 Request changes）  
> **策略**：先技术评审，再按 **0→1a→1b** 串行  
> **前置**：W0–W9 已合入 main（parity 主轴闭环 @ `e04cc95` / #51）  
> **父文档**：[`2026-06-profile-management-disabled-restore.md`](./2026-06-profile-management-disabled-restore.md)、[`2026-07-11-p1-journey-and-webui-parity-refinement.md`](./2026-07-11-p1-journey-and-webui-parity-refinement.md) §1.4  
> **探索**：[`explore`](e94251a8-5022-41d7-9577-a606b25b19ef)  
> **产品决议**：`management_enabled` **出厂默认 false**（运维显式 `true` 恢复）— 用户选 1  
> **计划评审**：[`code-reviewer`](cce6e394-4dbc-4082-aa18-511daf065bad) — Request changes → 本修订钉死 J6/J7/J13  

---

## 0. 为何是「下一个」

| 轨 | 现状 | W10 取舍 |
|----|------|---------|
| Parity P0–P2 + P3 #12 | ✅ W0–W9 | **不改** |
| Profile 管理门控 | 配置可开；**DEFAULT_CONFIG=`True` 与 gate/文档「默认关」矛盾** | **对齐默认 false + 迁移说明** |
| Journey P1-3 | Gateway `/journey` 裸 `build_learning_graph()`；语义未写死 | **本拍：进程 profile（`-p`）契约 + 双 home 测** |
| Members / `resolve_member_id` | **本仓 single-user stub** | **不进**（W10.1）；P1-3 **未关** member 半截 |

**一句话**：修好默认 false 的真源；Gateway Journey = 进程 `INTELLECT_HOME`；双 profile 不串。Member 等 stub 解开后再做。

---

## 1. 代码现实

| 事实 | 路径 / 行为 |
|------|-------------|
| Gate | `profile_gate.py`：缺 key → False |
| DEFAULT_CONFIG | `config.py`：`True` ← 深合并后许多装机**无显式 yaml 也等于开** |
| 关时 WebUI | 强制 `default`；switch/create/delete → 403 |
| WebUI Journey REST | `_active_profile_context()` 已有 |
| Gateway `/journey` | `_handle_journey_command` → 直接 `build_learning_graph()`（已是进程 home，缺文档/测） |
| Members | stub；原文「member 包装」不可诚实验收 |

---

## 2. 评审摘要

| # | 项 | 估时 | 合入 |
|---|----|------|------|
| **0** | 锁 J1–J13 | 0.5d | 文档 |
| **1a** | DEFAULT=`false` + 文档/release note + gate 测 | 0.5–1d | **先合** |
| **1b** | Journey 注释/契约 + 双 home 测 | 1d | **后合** |

### 2.1 W10 Definition of Done

| 必须 | 不在 W10 DoD |
|------|-------------|
| DEFAULT + gate + 用户文档：**一致默认 `false`** | 删门控代码；默认开启 |
| Release note：隐式 True→False 的 Profiles 可能消失 | `_config_version` bump / 改写用户 yaml |
| `true` 时手册 §2 验收通过（[restore handbook](./2026-06-profile-management-disabled-restore.md)） | 新 `journey_graph_for_process_home` wrapper |
| 双 home 测：A/B 图不交叉（ContextVar override） | 双 member 测；解 stub |
| Gateway 文案：Journey = 进程 `-p` | Session profile 驱动 Gateway Journey |
| WebUI learning profile 测不回归 | WebUI member 包装；Secure cookie 对齐 |

**完成定义表述**：「P1-3 **进程-profile MVP** 完成；**member 半截仍开** → W10.1」。

---

## 3. 锁（J1–J13）

| ID | 锁 | 决议 |
|----|-----|------|
| **J1** | 默认 | `DEFAULT_CONFIG["profiles"]["management_enabled"] = **False**`；gate 缺省 False；`website/docs/user-guide/profiles.md` 写 false |
| **J2** | 恢复 | 活跃 profile `config.yaml` 显式 `true` + 重启（手册 §2）。保留门控代码 |
| **J3** | 关时 | 与今日一致（mutating 拒；WebUI=`default`；403） |
| **J4** | 开时 | Profiles UI/chip/CRUD **恢复现有行为**；`?all_profiles=1` **不是**本拍新功能，仅随门控再现 |
| **J5** | Journey profile | Gateway `/journey` = **进程** `INTELLECT_HOME`（`-p`）。**禁止**从 messaging session 猜/切 home |
| **J6** | 实现（钉死） | **仅**在 `_handle_journey_command` 加简短注释（进程 home / 未来 member 挂钩一句）。**禁止**本拍新增 `journey_graph_for_process_home()`；第二调用点出现再抽 |
| **J7** | 双 home 测（钉死） | `tests/gateway/test_journey_*.py`：两 temp home；**必须**用 `set_intellect_home_override`（或等价 ContextVar），禁止只靠进程全局 env 跨用例串扰。断言 `build_learning_graph()`（及可选 list 格式化）A/B **零交叉**；完整 gateway handler 可选 |
| **J8** | Member | **不做**。文案：membership stub 下隔离边界 = **profile home only**；解 stub → W10.1 |
| **J9** | WebUI Journey | **不改** REST；`test_learning_api_profile` 绿 |
| **J10** | 安全 | 保持 STREAMS / `process_wide` switch 护栏；Secure cookie **不挡** |
| **J11** | Prompt-cache | 禁止活跃 stream 静默换 home（现有护栏） |
| **J12** | 非目标 | SessionChannel；outline；解 stub；Gateway 层 C；改 W7–W9 |
| **J13** | 迁移（钉死） | DEFAULT→false **不**改写磁盘 yaml；**不** bump `_config_version`。缺 key → 生效 false（曾靠旧 DEFAULT 隐式 true 的用户会关）；显式 `true`/`false` 不变。**必须** release note：「Profiles UI 可能消失，直至设置 `profiles.management_enabled: true`」 |

---

## 4. 实施切片

```text
1a: DEFAULT=false + docs + release note + G1–G3
        │
        ▼
1b: journey 注释 (J6) + T-home-A/B (J7) + 用户文档一句
```

### 建议触点

| 层 | 文件 |
|----|------|
| Config | `intellect_cli/config.py` |
| 测 | `tests/intellect_cli/test_profile_gate.py`；新 `tests/gateway/test_journey_profile_home.py` |
| Docs | `website/docs/user-guide/profiles.md`；CHANGELOG/release 片段；手册可选「W10 posture」 |
| Gateway | `gateway/command_handlers.py`（注释 only） |

### 测试

| ID | 内容 |
|----|------|
| G1 | 空/最小用户 yaml + `load_config()` → `is_profile_management_enabled()` is False |
| G2 | 显式 true → enabled |
| G3 | disabled 时 CLI create 仍拒 |
| T-home-A | override home A → graph 含 A、不含 B |
| T-home-B | 对称 |
| R1 | WebUI learning profile 测不回归 |

---

## 5. 手工验收

| ID | 验收 |
|----|------|
| M1 | 无显式 key：Profiles 隐藏；CLI create 拒 |
| M2 | 显式 `true` + 重启：Profiles 可用 |
| M3 | （可选）`-p A` vs `-p B` 的 `/journey list` 不同 |
| M4 | WebUI Journey 在管理开时仍按 cookie profile |

---

## 6. 非目标

- 默认开启管理；删门控；yaml 自动写入 false  
- 本拍抽 shared journey wrapper  
- 解 stub membership / 双 member Journey  
- Session-level Gateway Journey  

---

## 7. 与原 P1-3 文案的差异

原 MVP：`resolve_member_id` → `INTELLECT_MEMBER_ID`。  
本仓 stub → 空包装假绿。  

**W10**：进程-profile MVP；member = **W10.1**（P1-3 文档应记「半截仍开」）。

---

## 8. 签字表（R）

| # | 问题 | 建议 | ☐ |
|---|------|------|---|
| R1 | 出厂默认？ | **false** | ☐ |
| R2 | 保留门控？ | **是** | ☐ |
| R3 | Gateway Journey profile？ | **进程 `-p`** | ☐ |
| R4 | Member 本拍？ | **不做** | ☐ |
| R5 | WebUI learning？ | **不进** | ☐ |
| R6 | 拆 PR？ | **钉死** 先 1a 后 1b | ☐ |
| R7 | Secure cookie？ | 不挡 | ☐ |
| R8 | 迁移？ | 不改 yaml；不 bump version；**要** release note | ☐ |
| R9 | J6 wrapper？ | **本拍禁止**；注释 only | ☐ |
| R10 | J7 测法？ | ContextVar override + graph 断言 | ☐ |

---

## 9. 修订历史

| 日期 | 说明 |
|------|------|
| 2026-07-12 | DRAFT v1 |
| 2026-07-12 | REVISED — J6 禁 wrapper；J7 ContextVar；J13/R8 迁移+release note；R6/R9/R10 钉死 |
