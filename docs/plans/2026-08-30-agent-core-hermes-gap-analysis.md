# Agent Core 改进方案 — 基于 Hermes 更新分析（2026-06-15 → 2026-08-30）

> **日期**：2026-08-30
> **状态**：**分析完成 + 已评审**；建议按 P0 → P1 → P2 分批落地
> **数据来源**：`../hermes-agent/docs/update-summary-2026-06-15-to-2026-08-30-agent-core.md`（HEAD `4209d371aa`）
> **本仓 HEAD**：`e9546dd`（分支 `feat/w15-tool-search-l2`）
> **关联**：既有 `2026-07-08-hermes-v0.16-v0.18-port-todo.md` 已覆盖 v0.16→v0.18 阶段；本文覆盖其后的 v0.18→v0.2x 窗口，**无重复跟踪**

---

## 一、结论先行

intellect-agent 的 agent core 已高度收敛到 Hermes 架构：上下文 usage 锚定、流式 stale 断路器、tool-call 去重、Rust 错误分类器、413/图片恢复、MoA 循环、并行 fan-out 均已存在且成熟。本窗口 Hermes 新增能力中，真正构成差距的只有**少数几条**，且集中在「可靠性补一层」「用量按模型区分」「MoA 省调用」三类。

**最重要的判断**：与 Hermes 的差距不是"缺很多"，而是"缺几个关键原语 + 几个半成品"。改进应聚焦，不机械移植 Hermes 数字（沿用 W15 P5 备忘的纪律）。

---

## 二、差距总览（主题 × 现状 × 优先级）

| Hermes 主题 | intellect-agent 现状 | 关键差距 | 优先级 |
|---|---|---|---|
| 墙钟运行预算 `run_budget` | **完全缺失**（全仓 grep 零命中） | 无 `--run-budget`、无 80% wrap-up 注入、无 deadline 比例缩放 stale | **P0** |
| Reasoning 模型 stale 下限 | 只按上下文缩放（`run_agent.py:1086/1101/1103`、`chat_completion_helpers.py:2437`），不识别 reasoning 模型 | `_is_reasoning_model`（`run_agent.py:4273`）未接 stale 下限 | **P0** |
| 确定性空响应不重复计费 | 空响应重试存在（`conversation_loop.py:3885`），计费无条件执行（`:1544-1626`） | 缺"确定性空"标记，会双计费 | **P0** |
| SessionDB 读锁分离 | 单一 `threading.Lock` 同守读写（`intellect_state.py:1362/1478/1487/1508/1539`） | 39 个纯读方法未脱离写锁（Hermes 已做） | **P0** |
| 并发/迭代上限 | 3 / 50（`delegate_tool.py:132/512`） | Hermes 已上调 3→10、50→250；但配额机制已收敛（HP-202e） | **P1（门控）** |
| Micro-compaction per-turn | 仅一次性全量 `compress()`（`context_compressor.py:1827`） | 缺摊薄到每 turn 的微压缩 + 节奏 + 遥测 | **P1** |
| Per-model token 聚合 | 仅 per-call DB 记录（`conversation_loop.py:1626`），`TokenAccumulator` 扁平无 model 维度 | 中途换模不区分用量 | **P1** |
| MoA cadence / 鲁棒性 / 可见性 | 每次跑满 N+1（`moa_loop.py`）；有 aggregator fallback + 全失败抛出，但无中断中止、无 per-model 裁剪、无进度 | 缺 `user_turn`/`every_n`、中断中止、per-model clip | **P1** |
| delegate_task 结构化输出 schema | 签名无 schema 参数（`delegate_tool.py:1989`） | 缺可选 `response_schema` | **P1** |
| 项目上下文注入 | 仅 `workspace_path` hint（`delegate_tool.py:573-606`） | 缺 project-facts/briefing 注入子代理 prompt | **P1** |
| 用户可触发 `/review` 评审子代理 | 仅自动 memory/skill review fork（`background_review.py`） | 缺用户手动拉起、带父级 skills 的代码评审子代理 | **P1** |
| tool_call_id 变体配对 | 仅单 id 匹配（`tool_dispatch_helpers.py:366/388`） | 缺 id 变体规范化/跨变体保留 | **P2** |
| merged tool-call carrier 保留 | 缺失 | 缺合并 carrier 结果 reconcile | **P2** |
| 子代理隔离 | 共享父 SessionDB（`delegate_tool.py:1137`）、无 worktree、ACP env 全量继承（`copilot_acp_client.py:106`） | 缺独立 SessionDB / git worktree / env scrub | **P2** |

> **不列为改进项**（intellect-agent 已存在且更成熟）：上下文 usage 锚定（`context_compressor.py` 的 anti-over-count deferral）、positional prune、`_sanitize_tool_pairs` 配对清理、tool_call 去重、413 恢复、图片损坏恢复、Rust `classify_api_error` 分类 taxonomy。

---

## 三、分优先级方案

### P0 —— 补上关键稳定性原语（首批，1–2 周）

**P0-1 墙钟运行预算 `run_budget_seconds` / `--run-budget`**
唯一完全缺失的稳定性层，补在现有三层防护（循环断路器 `tool_guardrails.py` → 迭代预算 `iteration_budget.py` → 流式 stale 断路器）之上：
- 会话开始处起预算计时，到 **80%** 注入 wrap-up 提示（复用 `context_compressor.py` wrap-up 措辞风格）；
- 按 deadline 剩余比例缩放 stale 阈值（把 `_compute_non_stream_stale_timeout` / `chat_completion_helpers.py:2437` 的静态 240/300 改预算感知）；
- 配置走 `intellect_cli/timeouts.py` 现有解析路径，加 `providers.<id>.run_budget_seconds` + CLI flag。

**P0-2 Reasoning 模型 stale 下限（低改动）**
在 `run_agent.py:1101-1105` 与 `chat_completion_helpers.py:2437-2443` 两处，把现有"按 token 估算"缩放叠加 `_is_reasoning_model`（`run_agent.py:4273`，已存在）下限：reasoning 模型恒得 `max(base, reasoning_floor)`。目前该 helper 只用于 `extra_body`，直接扩用。

**P0-3 确定性空响应不重复计费**
在 `conversation_loop.py` 空响应重试路径（`:1131-1140` eager fallback、`:3869-3896` retry）加"确定性空"标记，命中后跳过该次计费（`:1504-1626`）。
**精确定义**（评审修正）：`finish_reason != "length"`（截断是真实输出，须计费）**且** 0 内容 **且** 无 tool_calls。避免漏计截断续写成本。

**P0-4 SessionDB 读方法脱离写锁（中等工程量，直接支撑主循环会话加载性能）**
把纯读方法（`get_session`/`get_session_title`/`get_session_*` batch）切到独立读连接，仅写路径持有写锁。
**评审标注**：非低改动——涉及 `RustSQLiteBackend` 的 `_python_conn` 读回退（`sqlite_backend.py:201`）与 `SESSIONDB_USE_RUST_RW` 标志（`:226`），须保证 WAL 下读写一致性；大量读方法当前返回同一个 `_conn`，需逐一确认可切只读连接。

### P1 —— 用量精细化 + MoA 省调用（第二批）

**P1-1 并发/迭代上限上调（门控）**
`_DEFAULT_MAX_CONCURRENT_CHILDREN` 3→10、`DEFAULT_MAX_ITERATIONS` 50→250。
**评审修正**：**不机械移植**——配额共享机制已收敛（`port-todo.md:117` HP-202e 已做）；仅在实测触发并发竞争 / 迭代触顶时上调，且带 DB migration（复用 `intellect_state.py:506 _reconcile_columns`）。

**P1-2 Micro-compaction per-turn**
把 `context_compressor.py:1827 compress()` 拆成可摊薄的 per-turn 增量压缩（先做"阈值内每 turn 压缩最旧 N 条"最小版本），配节奏配置 + token 遥测，避免长会话一次性全量压缩卡顿。

**P1-3 Per-model token 聚合**
给 Rust `TokenAccumulator`（`rust-core/src/usage.rs`）加 model 维度，或在 Python 侧 `run_agent.py:605` 的 `session_*` 计数旁加 `per_model` dict，支撑 `switch_model`（`agent_runtime_helpers.py:1342`）时的用量区分。

**P1-4 MoA cadence + 鲁棒性 + 可见性**
- `moa_config.py` preset schema 加 `cadence: user_turn | every_n`，避免每次跑满 N+1；
- `moa_loop.py:145` `asyncio.gather` 改 `TaskGroup`，接用户中断（`tools/interrupt.py` 已有）中止 reference fan-out；
- `_build_aggregator_messages`（`moa_loop.py:101-122`）按各 reference 模型上下文窗口裁剪。

**P1-5 delegate_task 结构化输出 schema**
给 `delegate_task` 签名（`delegate_tool.py:1989`）加可选 `response_schema`；注意 `max_iterations` kwarg 目前被 config 覆盖（`:2026-2040`），需一并放开。

**P1-6 项目上下文注入 + 用户 `/review`**
- 建 project-facts/briefing 泵，注入子代理 system prompt（替换目前仅 `workspace_path` hint 的 `_build_child_system_prompt`）；
- 把 `background_review.py` 的内存/技能 review fork 泛化为用户手动触发的代码评审子代理（已具备"继承父级 skills"能力 `:442-459`），接入 CLI/TUI/desktop 三界面。

### P2 —— 协议健壮性（视多后端节奏再定）

**P2-1** `tool_call_id` 变体配对（`tool_dispatch_helpers.py` 单 id → 变体规范化）；**P2-2** merged tool-call carrier 保留；**P2-3** 子代理隔离（独立 SessionDB、opt-in git worktree、ACP env scrub）。三者取决于是否同时挂多 provider + Codex/Responses/MoA facade——若当前主要后端单一，可后置。

---

## 四、落地顺序与验收

**第一批（P0）**：P0-2（reasoning 下限，~0.5d）→ P0-3（空响应防双计费，~0.5d）→ P0-1（墙钟预算，~1–2d）→ P0-4（读锁分离，~2–3d）。前两条低风险先落地验证方向，后两条是主要投入。

**第二批（P1）**：P1-2（micro-compaction）→ P1-3（per-model 用量）→ P1-4（MoA）→ P1-5（delegate schema）→ P1-6（上下文注入）→ P1-1（门控后并发上调）。

**第三批（P2）**：协议变体/隔离类，随多后端节奏。

### 最小验收清单（P0 批）

- [ ] `--run-budget` / `providers.<id>.run_budget_seconds` 三态解析；80% 注入 wrap-up；stale 阈值随 deadline 比例缩放
- [ ] reasoning 模型 stale 下限生效（流式 + 非流式两处），非 reasoning 模型行为不变
- [ ] 确定性空响应（0 内容 + 无 tool_calls + finish_reason≠length）不计费；`finish_reason="length"` 仍计费
- [ ] 读方法脱离写锁后，WAL 下读写一致性不回归（现有 `pytest` 会话相关用例全绿）
- [ ] 各改动不改变默认行为（新增能力默认关 / 仅当对应模型类型触发）

---

## 五、评审记录

- 全部 P0 断言经源码逐条验证（见各 `file:line`）。
- 修正 4 处：P0-4 并发上调降级 P1 且门控；SessionDB 读锁分离如实标注中工程量 + 一致性风险；空响应防双计费补精确定义；确认无既有文档重复跟踪。
- 参照 W15 P5 备忘纪律：**不机械移植 Hermes 数字与名单**，逐项按 Intellect 实际权衡。

---

## 六、实施结果（2026-08-30，分支 `feat/agent-core-hermes-gap-p0`）

**已完成并测试（P0-1/2/3 + 1 项顺带修复）**：

| 项 | 结果 | 测试 |
|---|---|---|
| P0-2 reasoning 模型 stale 下限 | ✅ 复用 `_supports_reasoning_extra_body()`（`run_agent.py:4233`），流式/非流式两处各加下限（240s/300s） | 新增 `test_reasoning_model_gets_floor_with_tiny_context`，3 passed |
| P0-3 确定性空响应不重复计费 | ✅ `finish_reason=="stop"` + 无 tool_calls + 空内容时跳过同模型重试（`conversation_loop.py`） | `test_empty_response_recovery_persistence.py` 3 passed |
| P0-1 墙钟运行预算 | ✅ `get_provider_run_budget` + `_resolved_run_budget_seconds` + loop 内 80% wrap-up 注入 + 100% hard-stop；config schema 加 `run_budget_seconds`；env `INTELLECT_RUN_BUDGET_SECONDS` | `test_timeouts.py` 15 passed |
| **顺带修复** | ⚠️ `timeouts.py` 三个函数用 `isinstance(config, dict)` 判断，但 `load_config_readonly()` 返回 `MappingProxyType`（非 dict 子类）→ config 级超时/预算配置**全部失效**（预存 bug）。改为 `collections.abc.Mapping` | 修复后 `test_timeouts.py` 15/15（base commit 上配置类测试全挂） |

**P0-4 已评估并回退（发现跨后端可见性缺陷）**：

- 尝试：新增专用 Python `_read_conn` + `_query_conn` 返回它 + 转换 10 个热点纯读方法脱离写锁。
- **失败原因**：当 `SESSIONDB_USE_RUST_RW=1`（本环境默认）时，主连接 `_conn` 是 **RustConnection**（rusqlite），写入走 Rust 后端；专用 Python `sqlite3` 读连接**看不到 rusqlite 的提交**（`_conn` 读 = 2 行，`_read_conn` 读 = 1 行），引入 1 个新测试失败。
- 结论：专用读连接必须与写后端**同库**——`RW=0`（Python 写）时 Python 读连接可行，`RW=1`（Rust 写）时需 Rust 侧第二读连接（rust-core 改造）。当前 Rust 后端只暴露单连接，**需 rust-core 支持才可安全落地**。
- 已 `git checkout` 回退，基线测试无回归（`test_intellect_state.py` 20 failed / 213 passed 与 base 一致，均为预存 `KeyError:0` 环境问题）。

**待办**：
- [ ] P0-4：rust-core 暴露独立读连接后，按 `SESSIONDB_USE_RUST_RW` 分支选择同库读连接。
- [ ] P0-1 的 `--run-budget` CLI flag（当前 config + env 已覆盖能力，flag 为薄封装）。
- [ ] P1 批次（micro-compaction / per-model 用量 / MoA cadence / delegate schema / 项目上下文注入）。
