# 分阶段实施路线图（主计划 + 深潜 → 可执行分期）

> **日期**：2026-08-31
> **输入**：`2026-08-31-intellect-improvement-plan-from-hermes.md`（主计划，G/MP/BT/PT 任务定义）
> 与 `2026-08-31-hermes-new-features-deep-dive.md`（实现级深潜，真实差距与移植细节）。
> **读法**：本文不重复任务定义，只做**分期、排序、依赖、门禁、风险**四件事。每个任务包（= 一个
> PR 粒度）标注来源条目、规模（S≤1d / M=2-4d / L=1-2w）、触碰文件面。
> **制定期验证**：①`apply_wal_with_fallback` 实测会回退 DELETE journal（G-14 读连接池必须
> 门控 WAL）；②`agent/conversation_loop.py` 4,587 行单函数热点，A 轨五个任务都碰它（须串行规则）；
> ③rust 绑定无版本校验（G-20 范围确认）。这三点已直接写入方案。
> **评审修订（同日）**：覆盖率核验 100%（G×21/MP×8/BT×4/PT 全落位）；补 A1-4 前置门（压缩器
> 原地缩减核验）、rust 文件冲突矩阵（tool_utils/sanitize 排序）、MP-01 降位规则（ADR 选 (a) 时
> 移出关键路径）、门-1 逐字节回归语义明确、A1-6 两段验收、基准脚本落位 scripts/bench/。
> **第二轮深度评审（同日）**：7 项源码验证（3 利好实证 / 2 前置门缺失实锤 / 2 复核通过）——
> A1-2 补恢复部分前置门（nudge 剥离钩子零命中、interim 机制半可用，规模可能上修并在门-0
> 收口裁决）；门-1 补超时等价验收（A1-1）；A1-3 风险下调（`_close_request_client_once` 已存在）；
> A1-1 依赖实证（psutil/`load_config_readonly` 现成）；A2-3 补 marker 体系三件套（新建非照抄）；
> A1-6 补 `state.read_pool` 回退杆；R5 缓解方向修正；排期声明 2 人假设与单人换算。

---

## 一、里程碑总览

| 里程碑 | 内容 | 出口门 |
|---|---|---|
| **M0** | 地基与基线：rust 绑定硬化、基准 harness、MP-00 ADR、凭据迁移、G-08 核验 | 门-0 |
| **M1** | 可靠性主干：断路器/deadline/跨 turn stale/usage 锚定/SessionDB 读池 | 门-1 |
| **M2** | 上下文治理 + delegation：prune/清洁/steering+tail+durable 队列/MoA 补齐 | 门-2 |
| **M3** | multiplex 开放：supervisor + 路由 + 前缀 + WS 解锁 + 观测 | 门-3 |
| **M4** | Bot Mode：Bot Chat/roster/message_agent/房间体验 | 门-4 |
| **M5** | 协议硬化 + 生态长尾 + pets | 门-5 |

M1/M2（A 轨）与 M3/M4（B 轨）**并行推进**；M5 吸收双轨长尾；PT（pets）无依赖随时插入。

---

## 二、Phase 0 · 地基与基线（M0）

| 包 | 来源 | 规模 | 内容与关键约束 |
|---|---|---|---|
| P0-1 | G-20 | M | rust 绑定硬化：`intellect_community_core` import 时版本 fail-fast、CI 加 `cargo test + maturin develop + import 冒烟` job。**先于一切 rust 触碰任务** |
| P0-2 | 新增 | S | 性能基准 harness：`scripts/bench/` 下三条基线脚本入库——`list_sessions_rich`（10k sessions）、流式解析、压缩耗时（G-14/G-21 的前后对照依据；防「迁移无收益」争议） |
| P0-3 | MP-00 | M | 架构 ADR：①~④全局状态审计清单落盘 + supervisor (a) vs in-process (b) 裁决（预荐 (a)）+ 两 profile spike（起停/前缀转发/杀 B 不影响 A 冒烟） |
| P0-4 | MP-01 | M | 凭据面审计 + `get_secret` 迁移：全量清单（含 `auxiliary_client.py:424` auth.json 模块缓存、provider 插件、平台 config 流）+ CI grep 门禁。独立有价值，不做 multiplex 也交付。**降位规则（评审裁定）**：若 P0-3 选 (a) supervisor——凭据隔离已由进程边界成立，MP-01 移出关键路径（降为 A3 纵深防御项，不再阻塞 B1 激活）；选 (b) 则维持前置 |
| P0-5 | G-08 前置 | S | 深潜勘误核验：`message_sanitization` 等路径有无既有别名处理——有则 G-08 降级为补测试 |

**门-0**：`cargo test` + 全量测试绿；基准基线数字入库；ADR 落盘；CI 新门禁生效且存量豁免清单冻结；**A1-2 恢复路径裁决（新建剥离钩子 vs 复用 interim）落记录，A1 包规模据此复核（第二轮评审补）**。

## 三、Phase A1 · 可靠性主干（M1）

| 包 | 来源 | 规模 | 触碰面 | 依赖 |
|---|---|---|---|---|
| A1-1 | G-02 | L | 新文件 `agent/deadline.py` + `tool_executor`/`mcp_tool` 接线。依赖已验证现成（评审实证）：psutil（`pyproject.toml:76` pinned）、`load_config_readonly`（`intellect_cli/config.py:4878`） | P0-1（纯 Python，不依赖 rust） |
| A1-2 | G-01 | M（恢复部分或上修，见⚠️） | `tool_guardrails.py` 扩展 + `tool_utils.rs`（canonical args）+ loop 注入点 | P0-1 |
| A1-3 | G-03 | M | `chat_completion_helpers.py` streak + 三处 provider-swap reset。**风险下调（评审实证）**：`_close_request_client_once`（`:156`）及 idle-kill 实战用法已存在，「关连接重试」基础现成 | A1-1（resolver 缩放，软依赖） |
| A1-4 | G-04+G-05 | M | `model_metadata` anchor + `usage.rs` + 413 分支字节化。**前置门（深潜 §4 遗留）**：先核验 intellect 压缩器能做**同消息数原地缩减**（否则字节永无进步，413 字节化降级为仅剥图兜底） | P0-1 |
| A1-5 | G-08 | M | `message_sanitization` 变体索引 + `tool_utils.rs` 扩展 | P0-5 结论 |
| A1-6 | G-14 | L | backend 只读连接池 + `list_sessions_rich` 迁 `backend.rs`。**回退杆（评审补）**：`state.read_pool` config 开关（off 直退现有单锁路径），不依赖发版回滚 | P0-1, P0-2（基线） |

**⚠️ WAL 门控（制定期新发现）**：`apply_wal_with_fallback` 在 NFS/SMB 等文件系统回退 **DELETE**
journal（实测返回 `"wal" | "delete"`）。DELETE 模式下多连接并发读+写 = `database is locked`
风暴。A1-6 硬约束：**读连接池仅当 `journal_mode == "wal"` 时启用；DELETE 回退路径保持现有单锁
行为**，并以注入测试覆盖两种模式。

**⚠️ conversation_loop 串行规则**：A1-2/A1-3/A1-4 都要改 `agent/conversation_loop.py`（4,587 行
热点，单 `run_conversation`）。规则：三包**按序合并**（2→3→4），每包的 loop 改动收敛到最小
接入点（注入/读取各一处），PR 描述附改动行数；A1-1/A1-5/A1-6 不碰 loop，可与前述任一包并行。

**⚠️ rust 文件冲突矩阵**（同仓并行的第二组热点）：
- `tool_utils.rs`：A1-2（canonical args 哈希）与 A1-5（id 变体索引）**同文件**——按 2→5 顺序
  合并，或同一 PR 内分两个 commit；
- `sanitize.rs`：A2-2 依赖 A1-5 的变体配对矩阵——A2-2 排 A1-5 之后；
- `delegation.rs`（A2-3）、`usage.rs`（A1-4）、`error_classifier.rs`（A3-1）、`backend.rs`（A1-6）
  各自独占，无冲突。

**⚠️ A1-2 恢复部分前置门（第二轮评审实锤）**：continue-intent 恢复依赖「合成 user nudge 在
持久化 transcript 中的识别与剥离」——实测 intellect **零命中**（`context_compressor`/
`conversation_compression` 无 synthetic/nudge 剥离钩子）；但 interim 机制**部分可用**
（`run_agent.py:359` `interim_assistant_callback`、`:3678` streamed 判定）。A1-2 开工首日
二选一并记录：a) 新建剥离钩子（规模上修 M→M+/L）；b) 复用 interim 回调 + 显式接受 nudge
入史（设计取舍记入 PR）。裁决在门-0 收口，据此复核 A1 包规模。

**门-1**：断路器/stale/锚定的行为测试全绿 + **既有会话上下文逐字节回归**（prompt-cache 不变性：未开启新开关的会话，发送的 API messages 与主干基线逐字节一致）+ **超时等价验收（A1-1，评审补）**：未显式配置 `timeouts.*` 时，各既有超时点的生效值与主干基线逐一相等（断言表对比，防 resolver 迁移无声改变默认超时）；A1-6 按两段验收（先读连接池+双 journal 模式测试，后 rust 迁移+基准对比，基线显著提升且 DELETE 模式回归通过）；`ruff check .` 绿。

## 四、Phase A2 · 上下文治理 + delegation（M2）

| 包 | 来源 | 规模 | 关键约束 |
|---|---|---|---|
| A2-1 | G-06 | M | 默认 **off**；`replace_messages` 原语已确认存在；**开项**：rearm 值持久化位置（model_config 单键 vs 等价）在 PR 内裁决并记录 |
| A2-2 | G-07 | M | `sanitize.rs` 扩展：sidecar/未配对清理 + 与 G-08 变体矩阵复用 |
| A2-3 | G-12 | L | ①subagent steering（registry 锁内身份校验 + **marker 体系三件套新建**——marker 常量 / 精确 peel / 压缩器识别，实测 intellect 零命中，是「建体系」而非「照抄格式」）②live transcript（直搬）③durable 完成队列（已确认缺失；与 delivery ledger 同构，claim 三态） |
| A2-4 | G-13 | M | every_n cadence + guidance peel + 事件族（facade 已存在，只补差距） |

依赖：A2-1/A2-2 依赖 P0-1；A2-3 的 rust 扩展依赖 P0-1；A2-3 ③ 的持久化与 gateway delivery
ledger 模式对照（可复用 `gateway/delivery_ledger.py` 骨架）。

**门-2**：G-06 默认配置下行为与现状逐字节一致（验收硬条款）；steering 伪造 owner 拒绝测试；
durable 队列崩溃恢复测试（kill -9 后重启补投且不重复）。

## 五、Phase B1 · multiplex 实装（M3，依赖 M0 的 P0-3 裁决）

| 包 | 来源 | 规模 | 内容 |
|---|---|---|---|
| B1-1 | MP-02 | S | `profile_routing.py` 纯单测模块（不依赖裁决结果） |
| B1-2 | MP-03 | L | 按裁决实装：supervisor（拉起/监护/按 profile 重启/端口冲突预检）或 in-process（①~④ 全清） |
| B1-3 | MP-07 | S | 政策文档（AGENTS.md 增补 + website 指南 + 鉴权取舍），与 B1-2 同 PR |
| B1-4 | MP-04 | M | HTTP `/p/<profile>/` 前缀 + 二级端口冲突启动即拒 |
| B1-5 | MP-05 | M | WS 守卫升级路由器（off 时保持 4404 fail-closed，既有测试复用）+ per-profile token |
| B1-6 | MP-06 | M | `served_profiles` 观测 + doctor + control socket `profile` 字段 |

**门-3**：双 profile 隔离注入测试（key/会话/skills 互不可见，参照 Hermes
`test_profile_isolation_runtime.py`）；杀 B 不影响 A；multiplex off 全量回归零变化。

## 六、Phase B2 · Bot Mode（M4，依赖 B1-2）

| 包 | 来源 | 规模 | 内容 |
|---|---|---|---|
| B2-1 | BT-01 | M | roster（serve 集派生）+ Bot Chat 会话（`intellect -p <name> chat` 单轮 + background 唤醒底座，全已有） |
| B2-2 | BT-02 | M | `message_agent`：`get_tool_definitions` 后处理注入 + dispatch 双层 title-gate（深潜勘误后的机制）+ 0o700 temp 文件协议 |
| B2-3 | BT-04 | S | 房间预算 + blob 头像（FNV-1a 确定性 PRNG，纯本地） |
| B2-4 | BT-03 | 可选 | peer-URL relay（裁剪版，不阻塞 M4） |

**门-4**：非 Bot Chat 会话永不出现 `message_agent` schema（forged call 结构化错误）；跨 profile
DM 端到端（本地 roster 模式）；roster 含离线状态。

## 七、Phase A3 + PT · 协议硬化与生态长尾（M5）

| 包 | 来源 | 规模 | 备注 |
|---|---|---|---|
| A3-1 | G-09+G-10 | M | `error_classifier.rs`：图片损坏矩阵 + billing 边界 |
| A3-2 | G-11 | S | per-model 用量（`usage.rs` per-key 维度 + `/usage` 展示） |
| A3-3 | G-15 | M | keyless 池；**默认 false**（裁定）；`intellect tools` tier 选择 |
| A3-4 | G-16 | M | 项目技能 + 信任门；**默认 true 但检疫门同 PR**（裁定） |
| A3-5 | G-17 | S | `foreign_sessions` 直搬 + `sessions import` 接线 |
| A3-6 | G-18 | M | MCP 50K 门槛 + 引用桩（复用 A1-2 实现）+ `mcp doctor` |
| A3-7 | G-19 | S | 更新机制硬化 |
| A3-8 | G-21 | M | stream_consumer/delivery rust 迁移收尾，**基准门控**（P0-2 基线，无收益即关闭） |
| PT-1..3 | PT | M | pets 包 + CLI + TUI（无依赖，可插任意窗口） |

**门-5**：keyless 默认关回归；G-16 检疫门负例测试（trust 后注入恶意技能被拦）；全量测试 + ruff。

---

## 八、依赖图（压缩）

```
P0-1 ──┬─► A1-2/4/5/6, A2-1/2/3, A3-1/2/6/8 的 rust 部分
P0-2 ─► A1-6, A3-8（基准门控）
P0-3 ─► B1-2 ─► B1-4 ─► B1-5 ─► B1-6        B1-2 ─► B2-1 ─► B2-2 ─► B2-3(, B2-4 可选)
P0-4 ─► (multiplex 激活前置；不阻塞 B1 代码)
A1-1 ─► A1-3            A1-2 ─(stub 复用)─► A3-6
A1-2 → A1-3 → A1-4 串行（conversation_loop 热点）
```

## 九、统一阶段门（每门必过）

1. `scripts/run_tests.sh` 相关目录全绿 + 全量套件无回归；
2. `ruff check .` 绿（含 ASYNC）；rust 任务加 `cargo test`；
3. 行为默认值清单复核：新增开关一律记录默认值与理由（G-06 off / keyless off / prune off …）；
4. 文档联动：CHANGELOG 行 + 涉及用户面的 website docs + 政策类改 AGENTS.md；
5. 基准类任务附前后数字（P0-2 基线对照）。

## 十、风险登记册

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R1 | DELETE journal 下读连接池 = locked 风暴 | 高 | A1-6 WAL 门控 + 双模式注入测试（已写入） |
| R2 | conversation_loop 4.6k 行多点编辑合并冲突 | 中 | A1 串行规则 + 最小接入点纪律 |
| R3 | rust 绑定漂移（site-packages vs 源码目录） | 中 | P0-1 前置 fail-fast + CI 冒烟 |
| R4 | prompt-cache 回归（G-01/G-04/G-06 注入点） | 高 | 「默认 off + 开启后逐字节验收」双条款；注入全部 append-only |
| R5 | multiplex 内存足迹（supervisor 多进程） | 中 | P0-3 spike 实测 RSS 记入 ADR；超预算按序缓解：限制 serve 集 → 前端合并冷 profile → 接受足迹并文档化（(b) in-process 更贵，**不作为缓解手段**——评审修正） |
| R6 | 检疫门滞后于项目技能发现 | 高 | 同 PR 硬条款（不可拆） |
| R7 | durable 队列重复投递 | 中 | claim 三态 + 幂等键 + kill-9 恢复测试（沿用 ledger 模式） |
| R8 | 工作量低估（L 包历史偏差） | 中 | 每 L 包拆两段验收（模块+接线分开 demo） |

## 十一、排期建议（日历视图，双轨并行）

```
周1-2   P0 全部（P0-1..5 可两人并行：rust/基准/ADR/凭据）
周3-5   A1（A1-1/5/6 与 A1-2→3→4 串行链并行）      ‖ B1-1/B1-2 启动（supervisor 线）
周6-8   A2                                          ‖ B1-2 收尾 + B1-4/5/6
周9-10  A3-1..6（长尾可穿插）                       ‖ B2-1/2/3
周11+   A3-7/8 + PT + 缓冲（L 包超支吸收）
```
**人力假设（评审补）**：以上按 **2 人并行**计（一人走 A 轨 conversation_loop 串行链，一人走
B 轨 + A 轨非 loop 并行包）。单人执行：B 轨整体后移一个 A 阶段，且 A1 自身 ≈5 周+
（串行链 3 周叠两个 L 包）；若门-0 裁决 A1-2 恢复路径为「新建剥离钩子」，A1 再 +2~4 天。
PT 填充任意窗口。

## 十二、配置/文档联动清单（分散到各包，汇总核对）

- AGENTS.md：MP-07（multiplex 边界 + 模块级缓存适用前提修订）
- website docs：多 profile 服务指南（B1-3）、keyless 隐私说明（A3-3）、curator 式新功能页
- CHANGELOG：每门一条汇总
- TODO.md：TODO-013（watchdog config 化）归入 A3 长尾顺手项
