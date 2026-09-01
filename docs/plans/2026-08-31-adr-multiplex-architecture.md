# ADR: Multiplex 架构裁决（MP-00 / P0-3）

> **日期**：2026-08-31（spike 实测 2026-09-01 补充）
> **状态**：✅ 已裁决 — **(a) supervisor 多进程起步**
> **输入**：`2026-08-31-intellect-improvement-plan-from-hermes.md` 主题 H（含两轮评审修订）、
> `2026-08-31-hermes-new-features-deep-dive.md` §10、spike 实测（见 §4）。

---

## 1. 背景与问题

multiplex（多 profile 单实例服务）要求 profile A 的 turn 绝不触及 profile B 的
状态。两个 contextvar seam（`set_intellect_home_override`、`set_secret_scope`）
只覆盖 contextvar 可达的状态。**评审实测的四类进程级全局状态不受保护**：

### ① 模块级 `get_intellect_home()` 缓存（20 个文件，import 时冻结）

| 文件 | 符号 |
|---|---|
| `agent/auxiliary_client.py:424` | `_AUTH_JSON_PATH` |
| `tools/process_registry.py:55` | `CHECKPOINT_PATH` |
| `tools/skills_hub.py:50` / `tools/skills_sync.py:39` / `tools/skills_tool.py:91` / `tools/skill_manager_tool.py:108` | `INTELLECT_HOME` |
| `tools/checkpoint_manager.py:69` | `CHECKPOINT_BASE` |
| `tools/environments/singularity.py:27` / `tools/environments/modal.py:34` | `_SNAPSHOT_STORE` |
| `gateway/channel_directory.py:19` | `DIRECTORY_PATH` |
| `gateway/mirror.py:21` | `_SESSIONS_DIR` |
| `gateway/hooks.py:32` | `HOOKS_DIR` |
| 其余 8 文件 | 同模式（审计脚本 `scripts/audit_credential_reads.py` 可再生清单） |

后果：profile B 的 turn 读写启动 profile 的 skills/checkpoints/hooks 目录。
AGENTS.md 第 3 条「模块级缓存没问题」的裁定前提是单 profile 进程。

### ② 进程级子系统无 per-profile 归属

`gateway/config.py:793`（config 启动单次加载）、`tui_gateway._get_db`
（SessionDB 句柄全局缓存）、插件发现（`model_tools` import 时扫描）、
cron scheduler / curator / kanban dispatcher（进程级单例）、skin、memory provider。

### ③ 子进程 env 桥接

contextvar 不进 subprocess；工具子进程需显式 `INTELLECT_HOME` 注入，
multiplex 下不能按 turn 改共享 `os.environ`。

### ④ Agent 工厂

`GatewayRunner(config)` 单 config 按 session 建 AIAgent；multiplex 需
per-profile config 加载 + 构造（Hermes 为此专设 `agent_cache_pressure.py`）。

---

## 2. 候选对比

| 维度 | (a) supervisor 多进程 | (b) Hermes 式 in-process |
|---|---|---|
| 隔离 | 进程边界天然隔离 ①~④ | 需逐项改造①~④ + 「scope 处处包裹」纪律（任何绕过 = 泄漏，Hermes 多个 #issue 为证） |
| 内存 | N 个子进程各自完整加载 | 单进程共享解释器 |
| 崩溃域 | 按 profile 重启，不连坐 | 一个 profile 的崩溃伤及全体 |
| 改造成本 | 前端路由 + 起停监护 | 审计清单全清（见 §1）+ per-profile agent cache 三重边界（LRU/TTL/内存压） |
| 复用 | 现有单 profile gateway **零改动**复用 | `GatewayRunner` 深改 |
| 升级 | 每 profile 独立升级 | 整体升级 |

## 3. 裁决

**采用 (a) supervisor 多进程起步**：

1. ①~④ 由进程边界整体消灭，隔离风险从「持续审计」降为「零」；
2. 现有单 profile gateway（含我们 M0/M1 的全部可靠性投资）零改动复用；
3. `duplicate-instance guard` 的设计注释（「每 profile 一个 INTELLECT_HOME
   自然允许并发实例」）正是此形态的设计预留；
4. 两个 contextvar seam 不废弃——前端自身与远期 (b) 迁移继续使用；
5. (b) 保留为后续优化路径：若内存足迹被证明不可接受，§1 审计清单即 (b)
   的改造 checklist（逐项关闭）。

**约束**：(a) 下 multiplex 激活不要求 MP-01 全量完成（凭据隔离由进程边界
成立）；MP-01（凭据审计）仍独立交付作为纵深防御。

## 4. Spike 实测（2026-09-01）

脚本：`scripts/verify/spike_supervisor.py`（零平台 degraded gateway 子进程 ×2）。

| 断言 | 结果 |
|---|---|
| 单 child 启动后 pid/control socket/heartbeat 落在自己 home | ✅（多轮验证，socket ~1–18s 内就绪） |
| control socket `identify` 返回自己 home 的 pid | ✅ |
| 双 child 并行启动隔离 | ✅（`cache/delegation` 风格的双独立 home 全部就绪） |
| kill B 不影响 A | ✅（PIPE 模式验证） |
| **DEVNULL 双 child 的启动时序方差** | ⚠️ 间歇性：双 child 同时冷启动时，45s 内个别 child 未达 control-socket 就绪（watchdog/kanban 日志正常，socket 文件缺现） |

**Spike 结论**：(a) 可行性成立（隔离断言全部通过）；间歇性就绪方差证明
**supervisor 必须实现 wait-for-ready 探针**（轮询 control socket
`identify`），不能假设固定启动延迟——这直接转化为 B1-2 的验收条款。
spike 的诊断输出（sock/pid/heartbeat/log-tail）保留在脚本中供 B1-2 复用。

## 5. 后续

- B1-1 `gateway/profile_routing.py`（纯模块，已独立交付）
- B1-2 supervisor 实装（含 wait-for-ready 探针 + 按 profile 重启）
- B1-3..B1-6 依路线图顺序
