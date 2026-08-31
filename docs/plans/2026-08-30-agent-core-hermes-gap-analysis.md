# Agent Core 改进方案 — 基于 Hermes 更新分析（2026-06-15 → 2026-08-30）

> **日期**：2026-08-30 分析 → 2026-08-31 实施完成
> **状态**：**已实施完成**——P0/P1/P2 主体 + MoA M1–M5 均已落地（PR #107 + #121）；仅 P2-2、MoA M3/M4 后置优化留 spec（见第六/九节）
> **数据来源**：`../hermes-agent/docs/update-summary-2026-06-15-to-2026-08-30-agent-core.md`（HEAD `4209d371aa`）
> **本仓 HEAD**：分析时 `e9546dd`；实施分支 `feat/agent-core-hermes-gap-p0`（PR #107）+ `feat/agent-core-hermes-gap-p1`（PR #121）
> **关联**：既有 `2026-07-08-hermes-v0.16-v0.18-port-todo.md` 已覆盖 v0.16→v0.18 阶段；本文覆盖其后的 v0.18→v0.2x 窗口，**无重复跟踪**

> ⚠️ **阅读提示**：第二/三节与第七节「实况对照」是**分析时**（实施前）的差距快照；第六/七节第四节/九节是**实施后**结果。两者并存，前者仅作历史依据，以「实施结果」节为准。

---

## 一、结论先行

intellect-agent 的 agent core 已高度收敛到 Hermes 架构：上下文 usage 锚定、流式 stale 断路器、tool-call 去重、Rust 错误分类器、413/图片恢复、并行 fan-out 均已存在且成熟；**MoA 是例外**（v0 单发合成，见第七节）。本窗口 Hermes 新增能力中，真正构成差距的只有**少数几条**，集中在「可靠性补一层」「用量按模型区分」两类，外加一个**独立大项 MoA 架构**。

**最重要的判断**：与 Hermes 的差距不是"缺很多"，而是"缺几个关键原语 + 几个半成品"。改进应聚焦，不机械移植 Hermes 数字（沿用 W15 P5 备忘的纪律）。

---

## 二、差距总览（主题 × 现状 × 优先级）

| Hermes 主题 | intellect-agent 现状 | 关键差距 | 优先级 |
|---|---|---|---|
| 墙钟运行预算 `run_budget` | **完全缺失**（全仓 grep 零命中） | 无 `--run-budget`、无 80% wrap-up 注入、无 deadline 比例缩放 stale | **P0** |
| Reasoning 模型 stale 下限 | 只按上下文缩放（`run_agent.py:1086/1101/1103`、`chat_completion_helpers.py:2437`），不识别 reasoning 模型 | `_is_reasoning_model`（`run_agent.py:4273`）未接 stale 下限 | **P0** |
| 确定性空响应不重复计费 | 空响应重试存在（`conversation_loop.py:3885`），计费无条件执行（`:1544-1626`） | 缺"确定性空"标记，会双计费 | **P0** |
| SessionDB 读锁分离 | 单一 `threading.Lock` 同守读写（`intellect_state.py:1362/1478/1487/1508/1539`） | 29 个纯读方法未脱离写锁（Hermes 已做） | **P0** |
| 并发/迭代上限 | 3 / 50（`delegate_tool.py:132/512`） | Hermes 已上调 3→10、50→250；但配额机制已收敛（HP-202e） | **P1（门控）** |
| Micro-compaction per-turn | 仅一次性全量 `compress()`（`context_compressor.py:1827`） | 缺摊薄到每 turn 的微压缩 + 节奏 + 遥测 | **P1** |
| Per-model token 聚合 | 仅 per-call DB 记录（`conversation_loop.py:1626`），`TokenAccumulator` 扁平无 model 维度 | 中途换模不区分用量 | **P1** |
| MoA 架构 | 单发合成：`_FakeMessage.tool_calls=None`（聚合器**无工具**）、顾问只收最后一条 user 消息（无降噪视图/角色提示）、无 prompt-cache 治理、无成本核算 | 缺「顾问只读 + 行动模型带工具」整套架构——**非单点**，已重分析为独立大项 **M**（见第七节） | **M（独立大项）** |
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
**详细分析见第八节**——移植障碍不在 summarizer 逻辑（intellect 已具备），而在四个结构性差异：持久化模型（append-only + session-rotation，无 `archive_and_compact`）、marker 身份（无 metadata key）、summary role 动态选择、无 `finalize_turn` 汇聚点。

**P1-3 Per-model token 聚合**
给 Rust `TokenAccumulator`（`rust-core/src/usage.rs`）加 model 维度，或在 Python 侧 `run_agent.py:605` 的 `session_*` 计数旁加 `per_model` dict，支撑 `switch_model`（`agent_runtime_helpers.py:1342`）时的用量区分。

**P1-4 MoA（原「cadence + 鲁棒性 + 可见性」——已重分析，不再作为 P1 单点）**
原方案把 cadence 当独立项。对照 `../hermes-agent/docs/moa-mechanism-analysis.md` 后判定**定位错误**：cadence 只在「聚合器带工具、一次 user turn 产生多个工具迭代」时才成立，而当前 MoA 是单发合成（聚合器无工具），一次 turn 本就只 fan-out 一次，没有「每次跑满 N+1」的浪费可省。真正缺口是整套架构，已重分析为独立大项 **M**（见第七节）。中断子项（读 `agent._interrupt_requested` 短路 fan-out）已作为 P1-4 的一部分落地。

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

**第二批（P1）**：P1-2（micro-compaction）→ P1-3（per-model 用量）→ P1-5（delegate schema）→ P1-6（上下文注入）→ P1-1（门控后并发上调）；**MoA 另立大项 M（见第七节），不与 P1 混排**。

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

## 六、实施结果（2026-08-30 → 08-31，分支 `feat/agent-core-hermes-gap-p0` + `feat/agent-core-hermes-gap-p1`）

**已完成并测试（P0-1/2/3 + 1 项顺带修复）**：

| 项 | 结果 | 测试 |
|---|---|---|
| P0-2 reasoning 模型 stale 下限 | ✅ 复用 `_supports_reasoning_extra_body()`（`run_agent.py:4233`），流式/非流式两处各加下限（240s/300s） | 新增 `test_reasoning_model_gets_floor_with_tiny_context`，3 passed |
| P0-3 确定性空响应不重复计费 | ✅ `finish_reason=="stop"` + 无 tool_calls + 空内容时跳过同模型重试（`conversation_loop.py`） | `test_empty_response_recovery_persistence.py` 3 passed |
| P0-1 墙钟运行预算 | ✅ `get_provider_run_budget` + `_resolved_run_budget_seconds` + loop 内 80% wrap-up 注入 + 100% hard-stop；config schema 加 `run_budget_seconds`；env `INTELLECT_RUN_BUDGET_SECONDS` | `test_timeouts.py` 15 passed |
| **顺带修复** | ⚠️ `timeouts.py` 三个函数用 `isinstance(config, dict)` 判断，但 `load_config_readonly()` 返回 `MappingProxyType`（非 dict 子类）→ config 级超时/预算配置**全部失效**（预存 bug）。改为 `collections.abc.Mapping` | 修复后 `test_timeouts.py` 15/15（base commit 上配置类测试全挂） |

**P0-4 首次尝试已回退（发现跨后端可见性缺陷，后于收尾批成功落地，见下表 `P0-4 读锁分离`）**：

- 尝试：新增专用 Python `_read_conn` + `_query_conn` 返回它 + 转换 10 个热点纯读方法脱离写锁。
- **失败原因**：当 `SESSIONDB_USE_RUST_RW=1`（本环境默认）时，主连接 `_conn` 是 **RustConnection**（rusqlite），写入走 Rust 后端；专用 Python `sqlite3` 读连接**看不到 rusqlite 的提交**（`_conn` 读 = 2 行，`_read_conn` 读 = 1 行），引入 1 个新测试失败。
- 结论：专用读连接必须与写后端**同库**——`RW=0`（Python 写）时 Python 读连接可行，`RW=1`（Rust 写）时需 Rust 侧第二读连接（rust-core 改造）。当前 Rust 后端只暴露单连接，**需 rust-core 支持才可安全落地**。
- 已 `git checkout` 回退，基线测试无回归（`test_intellect_state.py` 20 failed / 213 passed 与 base 一致，均为预存 `KeyError:0` 环境问题）。

**P1 批实施结果（同一分支，提交 `5cf73a4` + `67497a3`）**：

| 项 | 结果 |
|---|---|
| P1-3 per-model token 聚合 | ✅ `session_tokens_by_model`（归一化 key `normalize_model_name`）+ turn 结果 `tokens_by_model` 快照；`reset_session_state`/`agent_init` 双站点初始化 |
| P1-4（中断子项） | ✅ MoA 读线程无关的 `agent._interrupt_requested` 短路 fan-out（修了原 `is_interrupted` 线程错配）；其余子项重分析并入 M |
| P1-5 delegate schema | ✅ `response_schema`（解码级 `response_format` + prompt 级回退）+ `max_iterations` 放宽（int 强制转换）；穿透 `_dispatch_delegate_task` |
| P1-6（项目上下文子项） | ✅ 子代理 prompt 注入 workspace 项目上下文（每批解析一次）；`/review` 三界面见下表 |

**P1/P2 批实施结果（本次：micro-compaction + /review + tool_call_id 变体）**：

| 项 | 结果 | 测试 |
|---|---|---|
| P1-2 阶段 A+B | ✅ `ContextCompressor` 新增 `_micro_compact` 全套（cursor / exchange 定位 / rolling summary / splice+supersede / defrag / 连败跳过 / content-free 遥测）；`conversation_loop.py` final-response 分支挂钩（`completed and not interrupted` gate + `_last_flushed_db_idx` 左移调整）；`agent_init.py` 配置三项（`micro_compact` 默认关）；`compress()` 成功后 reset micro 状态 | 新增 `tests/agent/test_micro_compaction.py` 24 passed；`test_context_compressor.py` 91 passed 无回归 |
| P1-6 /review 三界面 | ✅ 新增共享 `agent/code_review.py`（`build_review_prompt` + `run_code_review`，含父级 skills/系统提示 pin）；CLI `_handle_review_command` 改调 runner（`parent_agent=self.agent`）；gateway 新增 `_handle_review_command` + `_run_review_task`（仿 `/background` 异步后台）+ `_COMMAND_DISPATCH` 注册 `review`；TUI 经 `tui_gateway/slash_worker.py` → `IntellectCLI.process_command` 免费继承 | 手动验证：`run_code_review` 生命周期（run/extract/close）通过；三界面 `import` 全绿 |
| P2-1 tool_call_id 变体 | ✅ `_normalize_tool_call_id`（`\|` 前段规范）+ `make_tool_result_message` 应用 | `tests/agent/test_tool_dispatch_helpers.py` 32 passed |

**收尾批实施结果（P0-1 flag / P1-2 阶段 C / P2-3 env scrub）**：

| 项 | 结果 | 测试 |
|---|---|---|
| P0-1 `--run-budget` flag | ✅ `_parser.py` 顶层 + chat 子命令双注册；`main.py` `_TOP_LEVEL_VALUE_FLAGS` + env 透传 | argparse 手动验证：`--run-budget 300` 顶层/chat 均解析 |
| P1-2 阶段 C（DB 同步） | ✅ **复用既有 `SessionDB.replace_messages`**（`intellect_state.py:1997`，原子 delete+reinsert，`/retry`/`/undo`/`/compress` 同款），无需新加 `archive_and_compact`；钩子在 micro 吸收/defrag 后调 `replace_messages` + `_last_flushed_db_idx=len(messages)`，失败回退 index-shift | `test_micro_compaction.py` 25 passed；`test_context_compressor.py` 91 passed |
| P2-3 env scrub | ✅ `copilot_acp_client.py:_build_subprocess_env` 从全量 `os.environ.copy()` 改为 allowlist（HOME/PATH/终端 + INTELLECT_HOME），不再向 ACP 子进程泄漏密钥 | `test_copilot_acp_client.py` 21 passed |
| P0-4 读锁分离 | ✅ 29 个纯读方法脱离写锁：`sqlite_backend.py` 加独立 `_read_conn`（RW=0 回退）+ `connection` 返回锁免读连接（RW=1 用 Rust `read_conn`）；`intellect_state.py` 29 个 `with self._lock:` 读块移除（`optimize_fts`/`vacuum` 两个写方法保留锁） | `test_intellect_state.py` 20 failed / 213 passed（与 base 一致，预存 `KeyError:0`）；`test_intellect_state_compression_locks.py` 12 passed |
| P2-3 git worktree | ✅ `delegate_task` 加 `worktree` 参数 + schema 字段；复用 `intellect_cli/worktree_helpers.py`（`_setup_worktree`/`_cleanup_worktree`），子代理在 `<repo>/.worktrees/` 隔离工作区运行，`TERMINAL_CWD` 指向 worktree + `finally` 恢复清理；`background=True` 明确拒绝（worktree 需 outlive 子代理） | `test_delegate.py` 143 passed（含 `test_worktree_rejected_with_background`）；worktree 创建/清理冒烟通过 |

**待办（剩余）**：
- [ ] P2-2 merged tool-call carrier（依赖多后端，见第九节）。
- [ ] **MoA 后置优化**：M3「三路缓存策略统一」（现仅 Anthropic 一路，见第七节第四节）；M4「per_iteration / every_n cadence」（现仅 user_turn）。

> 注：P2-3 的「独立 SessionDB」已达成——`delegate_tool.py` 子代理经 `parent_session_id` 隔离（写自己的 `session_id`，不污染父会话），无需另开独立 DB（会破坏 lineage 查询）。

---

## 七、MoA 差距重分析（依据 `../hermes-agent/docs/moa-mechanism-analysis.md`）

> 本节**取代**原 P1-4 的「cadence + 鲁棒性 + 可见性」定位。原方案把 cadence 当独立优化项，对照 Hermes 机制后判定是**对差距性质的误判**。

### 1. 实况对照（分析时快照，M1–M5 实施前）

| 维度 | Hermes | intellect-agent 现状（`agent/moa_loop.py`） | 差距 |
|---|---|---|---|
| 行动能力 | 聚合器带**完整工具 schema**，可发 tool_call 多步执行 | `_FakeMessage.tool_calls=None`，**聚合器无工具** | ❌ 根本性 |
| 顾问视图 | `_reference_messages` 把全对话降噪成纯文本（去 system、tool_call 渲染成 `[called tool: ...]`、工具结果折叠、零 tool-role） | `_run_single_reference` 只发 `[{"role":"user","content": user_message}]` | ❌ 顾问对任务状态/工具历史全盲 |
| 顾问角色提示 | `_REFERENCE_SYSTEM_PROMPT` 把模型重构为「只读分析者」，否定式+正反例压制「虚构已执行动作」 | 无 | ❌ |
| fanout cadence | `user_turn`/`per_iteration`/`every_n` + SHA-256 签名 turn 级缓存 | 无 | ❌（单发下本无意义） |
| prompt cache | guidance 附末尾、peel/rebase、三路缓存策略统一（cache share 85%→2% 再修回） | 无 | ❌ |
| 成本核算 | 每顾问按自己模型计价、中断 late-accounting 回填 | `_FakeResponse.usage` 全 0 | ❌ |
| 上下文裁剪 | `_trim_messages_for_reference` 按各顾问窗口裁剪 | 无（小窗口顾问 400→`[failed]`） | ❌ |
| 容错 | 单顾问失败→`[failed]` 标记继续；全失败→聚合器单独行动（净化 prompt） | 有全失败 raise + 聚合器 fallback，无单顾问失败标记 | ⚠️ 部分 |

**已对的部分**：虚拟 provider facade（`api_mode="moa"`）、并行 fan-out、聚合器 fallback、trace 保存、中断感知（本分支已补）——方向都正确。

### 2. 核心结论

**cadence 是「顾问节奏」，它只在聚合器能调工具、一次 user turn 产生多个工具迭代时才成立。** 当前 MoA 是单发合成，一次 turn 只 fan-out 一次，所以没有「每次跑满 N+1」可省。**cadence 是补上工具调用之后的下游优化，不是独立项。**

换句话说，Hermes 的 cadence 是「顾问视图确定性 + prompt cache」的**结果**，不是原因。单独做 cadence 等于在没打地基时先装窗户。

### 3. 修正后的 MoA 优先级（M1–M5）

| 顺序 | 项 | 性质 |
|---|---|---|
| **M1** | 聚合器接入**完整工具 schema**（让 MoA 从「单发合成」变成「行动模型」） | 根本性，最大 |
| **M2** | 顾问**降噪视图**（`_reference_messages` 等价物）+ `_REFERENCE_SYSTEM_PROMPT` | 决定顾问建议质量 |
| **M3** | **prompt cache 治理**（guidance 附末尾、peel/rebase、缓存策略复用） | 决定长对话成本，Hermes 最难点 |
| **M4** | **cadence + SHA-256 签名缓存**（此时才有意义） | 依赖 M1–M3 |
| **M5** | 每顾问成本核算 + `_trim_messages_for_reference` + 单顾问失败标记 | 成本/鲁棒性收尾 |

这是一个**独立大 feature**（Hermes 用 feat 15 / fix 63 才做完），不是 P1 的 3 个 bullet。**P2 的「MoA facade」依赖其实也卡在 M1 上**（M1 已落地，见第四节；原「连工具都发不出」的定位已过时）。

### 4. 实施结果（M1–M5 已完成并测试）

> 落于 `feat/agent-core-hermes-gap-p0` → 已合入 `main`。核心 `agent/moa_loop.py`（620 行）+ `agent/transports/moa.py`（transport 注册）+ `agent/moa_trace.py`（trace 落盘）。测试 `tests/agent/test_moa_loop.py` **36 passed**。

| 项 | 结果 | 关键实现（`agent/moa_loop.py`） |
|---|---|---|
| **M1** 行动模型 | ✅ 聚合器接收完整 tool schema，可发 tool_calls | `run()` 转发 `kwargs["tools"]`（`:519`）；`_FakeMessage.tool_calls` 不再硬编码 None（`:617-620`）；`_extract_tool_calls`（`:49`） |
| **M2** 顾问降噪视图 + 角色提示 | ✅ | `_REFERENCE_SYSTEM_PROMPT`（`:170`，否定式 + 正反例压制「虚构已执行」）；`_reference_messages`（`:227`，去 system、tool_calls 渲染为 `[called tool: ...]`、工具结果折叠、零 tool-role） |
| **M3** prompt cache 治理 | ✅（Anthropic 一路） | guidance 附末尾（`:401`）；`apply_anthropic_cache_control`（`:507-516`）；peel/rebase（`:454-466`） |
| **M4** cadence + SHA-256 签名缓存 | ✅（user_turn cadence） | `_turn_signature`（`:134`，turn_prefix）+ `_task_signature`（`:153`，压缩稳定）+ `_moa_fanout_cache`（`:442-476`） |
| **M5** 成本核算 / 裁剪 / 单顾问失败 | ✅ | `_extract_usage`（`:63`）+ `_trim_reference_messages`（`:103`）+ 单顾问 `failed_label`（`:356`）+ 聚合 usage 汇总（`:561-570`） |

**边界（非逐字移植 Hermes，可后置）**：
- M3 只做了 **Anthropic** 一路 cache；Hermes「三路缓存策略统一」（cache share 85%→2% 再修回）未移植。**「三路」指三种线格式各自的 cache 治理**：chat-completions（无 native cache，靠前缀复用）、Anthropic（`cache_control` breakpoint）、Responses/Codex（`prompt_cache_key`）。当前 MoA 聚合器只对 Anthropic 聚合器打 `apply_anthropic_cache_control`（`moa_loop.py:507-516`），其余两路留待多后端接入时补。
- M4 当前为 **user_turn** cadence（一次 user turn 内跨 tool 迭代复用）；`per_iteration` / `every_n` 未做。
- 中断感知（读线程无关的 `agent._interrupt_requested` 短路 fan-out + 跳过聚合器）属 P1-4 子项，本次保留。

---

## 八、P1-2 Micro-compaction 详细分析

> 依据 `../hermes-agent/docs/micro-compaction.md`、`../hermes-agent/agent/context_compressor.py`（`_micro_compact` 等）、`../hermes-agent/tests/agent/test_micro_compaction.py` 逐条比对。数据来源目录名是 `hermes-agent`（原计划标题「hermes-agebt」为笔误，本节引用均以 `hermes-agent` 为准）。

### 0. 结论摘要

micro-compaction = 把一次性全量压缩摊薄成**每 turn 吸收一个 exchange** 的增量压缩。Hermes 已做成完整子系统（~700 行实现 + 900 行测试 + 独立设计文档 `docs/micro-compaction.md`）。

对 intellect-agent 不是"加个 per-turn 循环"这么简单。summarizer 逻辑已具备（serialize/redact/aux model 全在），真正障碍是四个结构性差异：

| # | 障碍 | 严重度 | intellect 现状 |
|---|---|---|---|
| 1 | 持久化模型：append-only + session-rotation vs 原位软归档 | 🔴 根本性 | `_flush_messages_to_session_db` 只追加；压缩靠 `end_session("compression")` + 新建子会话；SessionDB **无 `archive_and_compact`** |
| 2 | summary marker 身份：无 metadata key，纯 content-prefix | 🟠 高 | `_is_context_summary_content` 只认 `SUMMARY_PREFIX` 前缀，无 `COMPRESSED_SUMMARY_METADATA_KEY` / `MICRO_COMPACT_MARKER_KEY` |
| 3 | summary role 动态选择 | 🟠 高 | 批量 summary 角色动态选（user/assistant/merged-into-tail），micro 需固定 assistant-role |
| 4 | 无 finalize_turn 汇聚点 | 🟡 中 | ~30 处 `_persist_session` 散落 `conversation_loop.py`，无单一 turn 收尾函数 |

### 1. Hermes 设计本质

要解决的不是"省 token"，而是**把一次大停顿摊成多次小停顿** + **让上下文占用率保持低水位**（而非锯齿上升到阈值）。两条产品性质：

1. 一次 pass 只吸收一个 **exchange**（完整 agent turn：assistant + 其 tool results + 后续 assistant 迭代，到下一个 user 消息）。
2. **user 消息永不压缩**（最核心性质：assistant 输出是"做了什么"、可无损压缩；user 指令是"意图之源"、不可重构）。

三个保护区：**head**（system prompt + 开场）、**tail**（token 预算内最近消息）、**所有 user 消息**。micro 只在中间动。

它是 **opt-in**（`compression.micro_compact: true`），因为每次 pass 改写已发送历史 = 每 turn 破一次 prompt-cache 前缀（`docs/micro-compaction.md:202-240`）。

### 2. Hermes 实现全貌

入口 `_micro_compact()`（`agent/context_compressor.py:7056`），由 `turn_finalizer.py:405-462` 的 `finalize_turn()` 在每 turn 收尾（`_persist_session` 之前）调用。

| 机制 | 位置 | 要点 |
|---|---|---|
| cursor | `_resolve_compact_cursor` `:6744` | 内存 cursor 失效（resume）时从 transcript 扫最后 marker 恢复 |
| 找 exchange | `_find_one_exchange` `:6793` | 跳过 user + 已有 marker，消费整 turn；splice 边界必须是 user（保证 assistant marker 不产生同角色相邻） |
| 串行化 | `_serialize_one_exchange` `:6883` | 委托批量路径 `_serialize_for_summary` |
| rolling summary | `_micro_summarize_one` `:6930` | 单个累积摘要，**merge** 新 exchange 进去，不堆 per-exchange 摘要 |
| supersede | `_splice_micro_compact_result` `:7353` | 只留最新 marker（累积性使旧 marker 冗余）；双重 containment 门防误删 batch marker |
| defrag | `_needs_defrag` `:6987` / `_defrag_rolling_summary` `:6992` | rolling summary 超阈值自摘要；shape-neutral（不动 cursor/splice/user） |
| 失败跳过 | `:7159` | 同 exchange 连败 3 次跳过，防毒 exchange 每 turn 卡死 |
| DB 同步 | `_sync_micro_compact_to_db` `:7321` | `archive_and_compact` 原子软归档 + 重插，同 session id 原位 |
| 遥测 | `_emit_micro_compaction_telemetry` `:7253` | content-free JSON，含 `occupancy_pct`；只读缓存阈值，绝不触发 `/models` 探测 |
| reset | `:8352` | batch `compress()` 成功后清空 rolling summary + cursor |

配置（`config_defaults.py:871-890`）：`micro_compact` / `micro_compact_every_n_turns` / `micro_compact_defrag_threshold_tokens`，默认关。

### 3. intellect 现状对照

**已具备、可直接复用**（批量压缩路径本就是 Hermes 移植物）：
`_serialize_for_summary`（`:946`，含 `redact_sensitive_text`）、`summary_model`/`call_llm`（`_generate_summary` `:1217`）、`_prune_old_tool_results`（`:754`）、`_find_tail_cut_by_tokens`（`:1745`）、`_align_boundary_forward`（`:1631`）、`_protect_head_size`（`:1641`）、`_strip_summary_prefix`/`_with_summary_prefix`（`:1518/1534`）、token 估算、`_repair_message_sequence`。

**缺失、需新增**：micro 状态机（cursor/rolling_summary/cadence/连败计数/passes 计数）、`_find_one_exchange`、`_build_micro_summary_prompt`、`_splice_micro_compact_result`、`_emit_micro_compaction_telemetry`、配置三项、每 turn 调用钩子。

### 4. 四个根本性移植障碍

**障碍 1 — 持久化模型（最大风险，决定工作量量级）**：Hermes micro 依赖 `archive_and_compact`（**同 session id 下**软归档 active rows + 插入 compacted set + 打 `_DB_PERSISTED_MARKER` 让 append-only flush 跳过）。intellect 是 append-only（`run_agent.py:1564`，`_last_flushed_db_idx` 游标去重）+ session rotation（`conversation_compression.py:500-560`：`end_session(old, "compression")` + 新 `session_id` + `create_session(..., parent_session_id=old)`）。`intellect_state.py` 无 `archive_and_compact`（grep 全仓仅 `kanban_repository.archive_task` 命中）。选项：

- (a) 加 `archive_and_compact` 到 SessionDB——最忠实，但撞上 P0-4 已暴露的墙（`SESSIONDB_USE_RUST_RW=1` 时写走 Rust 单连接，Python 读连接看不到 rusqlite 提交，需 rust-core 第二读连接）。
- (b) micro-rotation——语义错（每 turn 一个子会话，lineage 爆炸 + title 自动编号爆炸），**不可取**。
- (c) **最小可行 = 纯内存 splice + 容忍 resume 双载**（Hermes `:7338-7351` 失败降级即如此）。阶段 A 起步，DB 同步留阶段 C。

**障碍 2 — summary marker 身份**：Hermes 的 supersede/defrag/cursor-recovery 全依赖 metadata key——`COMPRESSED_SUMMARY_METADATA_KEY`（通用 marker）、`MICRO_COMPACT_MARKER_KEY`（**区分 micro vs batch**，防止 defrag/supersede 误改 batch marker 持有的额外历史）、`COMPRESSED_SUMMARY_HAS_USER_TURN_KEY`（provenance）。intellect 只有 content-prefix 识别（`_is_context_summary_content` `:1540`）。batch 靠 rotation 避开新旧 marker 共存，micro 一旦原位运行，必须引入 key 体系，否则 resume 恢复和 supersede 都出错。

**障碍 3 — summary role 动态选择**：intellect 批量 summary 角色动态挑（`compress` `:1992-2027`，极端时 merge 进 tail 首条）。Hermes micro marker **恒为 assistant-role**，依赖"exchange 完整、两侧是 user"保证 alternation 合法。必须严格实现 `_find_one_exchange` 的边界约束，否则 `user→user→user` 被 `_repair_message_sequence` 合并、marker 元数据丢失（Hermes 测试 `test_spliced_transcript_survives_repair_message_sequence` 钉死）。

**障碍 4 — 无 finalize_turn 汇聚点**：intellect 正常完成 turn 的落点是 `conversation_loop.py:3747-3756` 的 final-response 分支（`should_compress` + `_persist_session`），但全程 ~30 处 `_persist_session` 散落各 exit path。micro 只需挂 final-response 一处，但建议**抽一个 `_finalize_turn` helper**（micro + persist），顺带归位 P0 批的 `_drop_trailing_empty_response_scaffolding` 等逻辑。

### 5. 推荐落地路径（对齐 P1-2 "最小版本"）

**阶段 A（最小可行 ~1d，纯内存，不碰 DB）**
1. `ContextCompressor.__init__` 加 micro 状态机字段（`_micro_compact_cursor` / `_micro_compact_rolling_summary` / `_micro_compact_enabled` / `_micro_compact_every_n_turns` / `_micro_compact_defrag_threshold_tokens` / `_micro_compact_consecutive_failures` / `_micro_compact_last_failure_cursor` / `_micro_compact_passes` / `_micro_compact_tokens_saved_total`）。
2. 移植 `_find_one_exchange` + `_serialize_one_exchange`（委托 `_serialize_for_summary`）+ `_build_micro_summary_prompt` + `_micro_summarize_one`（复用 `summary_model`/`call_llm`，merge prompt 而非结构化 JSON）。
3. 移植 `_splice_micro_compact_result`（**引入 `COMPRESSED_SUMMARY_METADATA_KEY` + `MICRO_COMPACT_MARKER_KEY`**，assistant-role marker）。
4. final-response 分支、`_persist_session` 前加调用（`_micro_compact_enabled is True` + `not _persist_disabled` 等 gate）。
5. `compress()` 成功后 reset micro 状态（Hermes `:8352` 语义）。
6. 配置三态 + 默认关。

**阶段 B（正确性补全 ~1-1.5d）**
7. `_resolve_compact_cursor` + `_rolling_summary_from_marker`（resume rehydration）。
8. defrag（`_needs_defrag`/`_defrag_rolling_summary`），否则 rolling summary 无界增长。
9. 连败跳过 + 遥测 `_emit_micro_compaction_telemetry`（含 `occupancy_pct`）。

**阶段 C（DB 同步 ~1-2d，依赖 rust-core）**
10. 加 `archive_and_compact`：RW=0 用 Python 读连接即可；RW=1 需 rust-core 第二读连接（与 P0-4 同根因，一并解）。
11. `_sync_micro_compact_to_db` + `_flush_scan_cursor_invalidated` 标志（defrag 改 marker 后失效 flush 游标）。

### 6. 风险与权衡

1. **prompt-cache 打破**：intellect 无 Hermes 级 cache 治理，故"每 turn 破 cache"代价可能更低；但后续补 cache 会直接冲突，需在文档显式标注交互。
2. **每 turn 延迟**：pass 是真实 aux 调用，串行阻塞 turn 末尾。核心经验（`docs/micro-compaction.md:242-283`）：compression model 用**小的非 reasoning instruct 模型**（reasoning 纯浪费）。`summary_model` 已支持独立配置，沿用即可。
3. **首 pass 负收益**：marker scaffolding ~400-450 token，首 pass 常 `tokens_delta > 0`（测试 `test_first_pass_costs_marker_overhead_then_pays_it_back`）。看会话轨迹，不判单条。
4. **user 消息永不压缩 → 有下限**：常年贴 10-20K prompt 则 middle 下不去，by design（`docs/micro-compaction.md:63-85`）。
5. **与 batch 交互**：micro 原位 + batch rotation，marker 共存时 supersede 必须靠 `MICRO_COMPACT_MARKER_KEY` 隔离（障碍 2 必做），否则吞 batch marker 额外历史。

### 7. 验收标准

- [ ] 默认关：`micro_compact` unset/false 时 `_micro_compact()` no-op，行为与现在完全一致
- [ ] 一次 pass 只吸收一个 exchange；assistant + tool results 整组被单个 assistant-role marker 替换
- [ ] user 消息全 session verbatim 存活（含 defrag 路径）
- [ ] head / tail 保护区不动
- [ ] cursor 连续推进；resume 后从 transcript 恢复、不重复摘要
- [ ] 毒 exchange 连败 3 次跳过，不每 turn 卡死
- [ ] defrag 只重写 summary 文本，shape-neutral（不动 cursor/splice/user）
- [ ] 遥测 content-free，`occupancy_pct` 只读缓存阈值
- [ ] 无 DB 绑定时 splice 不破坏 `_db_persisted` stamp（对照 `test_splice_preserves_db_persisted_stamps`）
- [ ] batch `compress()` 成功后 reset micro 状态

---

## 九、剩余项细化

> 本节原为 spec-only 预置，现多数已落地：**P1-2 阶段 C ✅ 已完成**（复用 `replace_messages`）、**P2-3 大半已完成**（env scrub + git worktree；独立 SessionDB 由 `parent_session_id` 隔离达成）。**当前仅 P2-2 仍是 spec-only**（阻塞于多后端）。

### 1. P1-2 阶段 C —— DB 同步（✅ 已完成，复用 `replace_messages`）

**结论**：无需新增 `archive_and_compact`——intellect 已有 `SessionDB.replace_messages(session_id, messages)`（`intellect_state.py:1997`，原子 delete+reinsert，`/retry`/`/undo`/`/compress` 同款），语义等价于「原位软归档 + 重插」。

**已落地**：`conversation_loop.py` 钩子在 micro 吸收/defrag 后调 `replace_messages` + `_last_flushed_db_idx = len(messages)`（标记全部已持久化，随后的 append-only flush 跳过）；失败回退到阶段 A/B 的 index-shift。defrag 的 marker 改写也经同一 `replace_messages` 路径重持久化（原 `_flush_scan_cursor_invalidated` no-op 已消除）。

**验收**：`test_micro_compaction.py` 25 passed；`test_context_compressor.py` 91 passed。resume 不双载（DB 存 spliced 转录，非原始+拼接）。

### 2. P2-2 —— merged tool-call carrier 结果 reconcile

**目标**：Hermes 的 repair pass 合并相邻 assistant turn（同一次 model 请求的多个 tool_call 迭代）时，追踪 tool_call id 的**并集**；intellect 的 `_repair_message_sequence`（`agent/agent_runtime_helpers.py:329`）目前缺等价 reconcile，合并后 carrier 的 tool_call id 与结果行配对可能错位。

**前置（线格式差异）**：只有 Responses / Codex 后端才产生需要 merge 的 carrier 形状；当前默认 `chat_completions` 后端不触发。三者的本质区别：

| | chat_completions（当前默认） | codex_responses / Codex |
|---|---|---|
| 工具调用 | `message.tool_calls=[{id, function:{name, arguments}}]`，工具结果回传 `role:"tool"` + 单 `tool_call_id` | `output[]` 里多个 `function_call` item（各带 `call_id`），结果回传 `function_call_output` item |
| 推理内容 | `reasoning_content` / `reasoning` 字段 | 独立 `reasoning` item，Codex 还加密重放（messages 表存 `codex_reasoning_items` / `codex_message_items`） |
| 一次请求的转录 | 一条 assistant 消息 + 一个 tool_calls 数组 | 一个 **carrier** 混着 `reasoning` + `message` + 多个 `function_call`，需拆/合并进转录 |

即：chat-completions 是「一条消息一个工具回合」，Responses/Codex 是「一个 carrier 多 item 混合」——后者才需要本项的 carrier 级 reconcile。默认 `chat_completions` 路径永远走不到该分支。

**步骤**：
1. 对照 Hermes `agent_runtime_helpers.py:579-720` 的 repair 合并逻辑，找出 intellect `_repair_message_sequence` 缺的「合并 turn 时保留 tool_call id 并集」分支。
2. 合并后给 carrier 保留 `tool_calls` 元数据（name/arguments/id），供 `_sanitize_tool_pairs` / `make_tool_result_message` 正确配对。

**验收**：Responses/Codex 后端下，合并 carrier 的工具结果能正确回填；`tool_call_id` 变体（`call_abc|def`）经 P2-1 规范后配对正确。

### 3. P2-3 —— 子代理隔离（独立 SessionDB / git worktree / env scrub）

**目标**：delegate / review 等子代理不再共享父 SessionDB，不继承父 ACP 环境。

**前置**：多后端（MoA M1 已完成，见第七节，已不再构成阻塞）。

**步骤**：
1. **独立 SessionDB**：`tools/delegate_tool.py:1161` 当前 `session_db=getattr(parent_agent, "_session_db", None)` → 改为为子代理开独立 `SessionDB`（或复用 parent 但 `parent_session_id` 隔离 + 只读）。**（待做）**
2. **opt-in git worktree**：`delegate_tool` 增 `worktree=True` 参数，用 `git worktree add` 给子代理一个隔离工作区（当前无）。**（待做）**
3. **env scrub**：✅ **已完成** —— `agent/copilot_acp_client.py:_build_subprocess_env` 改为 allowlist（`_SUBPROCESS_ENV_ALLOWLIST`：HOME/PATH/终端 + INTELLECT_HOME），不再向 ACP 子进程继承密钥/凭证。

**验收**：子代理写入不污染父会话；worktree 子代理改动隔离可丢弃；子进程环境不含父级敏感变量（env scrub 部分已验收：`test_copilot_acp_client.py` 21 passed）。

