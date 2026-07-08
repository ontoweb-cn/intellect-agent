# Hermes 移植 Phase 2 — 任务细化与评审

> 文档日期：2026-07-08
> 范围：HP-103f（溢出）/ HP-201…204（TODO Phase 2，设计 §5.3 / §5.5 / §5.8）
> 上游：`2026-07-08-hermes-v0.16-v0.18-port-design.md` §九评审记录
> 前置：Phase 1 已完成（`3498fb1`，HP-101/102/103 MVP）

## 1. Phase 2 定位

| 维度 | 内容 |
|------|------|
| **目标** | P1 上两项：**后台委派**（最大单项）+ **`/learn` 技能蒸馏**（可并行） |
| **并行度** | HP-201 → HP-202 → HP-203 串行；**HP-204 与 HP-202 在 HP-201 之后可并行**；HP-103f 独立可选 |
| **总工期** | 设计估 **2–3 周**；评审修订后 **2.5–3.5 周**（含 spike 与 E2E） |
| **Rust 要求** | HP-202 必须新增 `delegation.rs` + `cargo test`；HP-204 可选 `validate_skill_frontmatter_rs` |
| **出口** | 全量 `scripts/run_tests.sh`；HP-202/203 需 gateway E2E（完成事件不丢、不与 double guard 死锁） |

### 1.1 与 Phase 1 / Phase 3 边界

| 方向 | 规则 |
|------|------|
| **← Phase 1** | HP-103f（FAL edit）可为本 Phase 首项，**不阻塞** HP-201 |
| **→ Phase 3** | HP-202 **不** 引入跨进程持久委派；MoA / 验证证据 / 蓝图 **不在本 Phase** |
| **政策红线** | 不修改 `run_agent.py` 主 loop（`/learn` 走 slash + auxiliary，后台委派走 tool + watcher） |
| **既有能力** | `/background`（并行会话）与 `delegate_task`（同步子代理）**保持语义不变** |

---

## 2. 评审发现：命名冲突（HP-203 前置决议）

设计 §5.3 提议新增 `/bg` 查询后台委派状态，但 intellect **已占用** `/bg`：

```99:100:intellect_cli/commands/registry.py
    CommandDef("background", "Run a prompt in the background", "Session",
               aliases=("bg", "btw"), args_hint="<prompt>"),
```

| 命令 | 现有语义 | Hermes 原意 |
|------|----------|-------------|
| `/background` / `/bg` | 启动**独立会话**的后台 agent（fire-and-forget） | 查询 **delegate_task 句柄** |
| `delegate_task` | 同步阻塞子代理 | `background=true` 异步 + 句柄 |

**评审定案（HP-203）**：**不复用 `/bg` 主名**。委派状态命令采用：

```text
/delegations          # canonical：list | show <id> | cancel <id>
别名：/dg、/dlgt（可选 /delegation）
```

`/background` 文档中注明与 `/delegations` 的区别（并行会话 vs 父会话内异步子代理）。Gateway double-guard bypass 名单增加 `delegations`（与 `background` 并列）。

---

## 3. HP-103f — FAL 图像编辑（Phase 1 溢出，可选）

**设计依据**: §5.8 | **规模**: 1–2 人日 | **依赖**: HP-103 MVP | **评审**: ⚠️ 可选

| ID | 子任务 | 产出 | 估时 |
|----|--------|------|------|
| HP-103f-a | FAL edit API spike（端点、参数、响应格式） | spike 笔记 | 2h |
| HP-103f-b | `plugins/image_gen/fal/` 实现 `edit()` + catalog | plugin | 4h |
| HP-103f-c | 复用 `validate_local_image_file()`；无 silent fallback | tool 路由 | 1h |
| HP-103f-d | 测试：FAL mock + forbidden path | `test_image_edit.py` | 2h |

**验收**: 与 OpenAI edit 相同安全契约；`image_gen.provider: fal` 时 `source_image` 可走 edit。

**评审结论**: ⚠️ **可选** — 可与 HP-201 spike 并行，不进入 HP-202 关键路径。

---

## 4. HP-201 — CLI 完成回流 Spike（HP-202 前置）

### 4.1 现状核查

| 能力 | 位置 | 状态 |
|------|------|------|
| Gateway 终端完成回流 | `process_registry` + `_run_process_watcher` + `MessageEvent(internal=True)` | ✅ 已验证 |
| CLI 同步 REPL drain | `cli.py` `_process_loop` idle/post-turn → `drain_notifications()` | ✅ 终端后台已用 |
| `/background` 完成 UI | Rich panel，**不**经 `_pending_input` | ✅ 独立路径 |
| 多完成合并为单回合 | — | ❌ 未实现 |
| 委派完成队列 | — | ❌ 未实现 |

### 4.2 Spike 问题清单（必须一页结论）

1. CLI 下委派完成：复用 `process_registry.completion_queue` + 现有 drain，还是专用 `delegation_queue`？
2. 用户在 agent **运行中**输入时，完成通知是 interrupt 级还是排队到下一轮？
3. **扇出**（N 个 background 子代理同批完成）：合并为 **一条** synthesis prompt 还是 N 条顺序回合？
4. 降级方案：CLI 仅 `/delegations list`，不自动注入 — 是否可接受为 v1 fallback？

### 4.3 子任务分解

| ID | 子任务 | 产出 | 估时 |
|----|--------|------|------|
| HP-201a | 梳理 terminal notify 全链路（gateway + CLI）序列图 | spike 文档 §A | 2h |
| HP-201b | CLI 原型：mock 完成事件 → `_pending_input.put(synth)` | spike 分支或笔记 | 3h |
| HP-201c | 扇出合并 prompt 格式草案（`[IMPORTANT: N delegations completed…]`） | spike 文档 §B | 1h |
| HP-201d | 一页结论：**可行方案 / 降级方案 / HP-202 接口约束** | `docs/plans/…-hp201-spike.md` 或本节附录 | 2h |

### 4.4 验收

- [ ] 明确 HP-202 gateway 回流 **必须** 走 `internal=True` + `adapter.handle_message()`（禁止仅 `_pending_messages`）
- [ ] 明确 fan-out 合并策略（评审倾向：**单回合合并**，避免 N 次 interrupt）
- [ ] CLI 结论二选一：**自动 drain** 或 **仅 `/delegations`**，写入 HP-202 验收

**评审结论：✅ 必须先于 HP-202 代码；估 1 人日。**

---

## 5. HP-202 — 后台委派注册表 + `delegate_task(background=true)`

### 5.1 现状核查

- `tools/delegate_tool.py`：同步阻塞；`_active_subagents` 仅进程内观测；schema **无** `background`。
- 并发：`delegation.max_concurrent_children`（默认 3）— **后台 + 同步应共享配额**（评审定案）。
- 中断：父 interrupt 当前会取消**同步**子代理；后台子代理 **不级联**（设计 §九 Q1 已定案）。
- Rust：`PlatformRetryScheduler`（`rust-core/src/gateway.rs`）为注册表 PyO3 模板；**无** `delegation.rs`。

### 5.2 架构定案

```text
delegate_task(background=true)
  → Rust DelegationRegistry.register(goal, parent_session_key) → handle_id
  → Python thread: _run_single_child(...)  # 复用现有子代理构建
  → on complete/fail: registry.complete(handle_id, summary|error)
  → registry.drain_completions(parent_session_key) → [(handle_id, synth_prompt), ...]
  → Gateway: merge → MessageEvent(internal=True) → 新 agent 回合
  → CLI: 按 HP-201 结论 drain 或等 /delegations
```

**持久化**：**无** — 进程退出清空注册表；与 `delegate_task` 非 durable 政策一致。

### 5.3 Rust API 草案（`delegation.rs`）

| 方法 | 语义 |
|------|------|
| `register(parent_session_key, goal) -> str` | 分配 `d-<uuid>`，状态 `running` |
| `complete(handle_id, status, summary)` | `running` → `completed` \| `failed` \| `cancelled`；入完成队列 |
| `cancel(handle_id) -> bool` | 请求取消；Python 线程协作中断 |
| `get(handle_id) -> dict` | 状态查询 |
| `list(parent_session_key?) -> list` | `/delegations` 数据源 |
| `drain_completions(parent_session_key) -> list` | 消费队列，供 watcher 调用 |
| `count_running() -> int` | 与 sync 子代理合计配额 |

状态机单元测试在 Rust 层 **100% 覆盖**（`cargo test delegation`）。

### 5.4 子任务分解

| ID | 子任务 | 产出 | 估时 |
|----|--------|------|------|
| HP-202a | `rust-core/src/delegation.rs` + `lib.rs` 导出 + PyO3 | Rust | 2d |
| HP-202b | `intellect_rust.py` 薄导出 + Rust 状态机测试 | tests in rust | 1d |
| HP-202c | `tools/async_delegation.py`：线程生命周期、`complete()` 回调、cancel 协作 | 新模块 | 1d |
| HP-202d | `delegate_tool.py`：`background: bool` schema；true 时立即返回 handle JSON | schema + 分支 | 4h |
| HP-202e | 配额：`count_running()` + sync 子代理 **共享** `max_concurrent_children` | delegate_tool | 3h |
| HP-202f | Gateway：`delegation_watcher` 对齐 `_run_process_watcher`；fan-out merge | `agent_runner.py` | 1.5d |
| HP-202g | CLI 回流：按 HP-201 结论 | `cli.py` | 0.5–1d |
| HP-202h | 父 `/stop` 不 cancel 后台子代理；显式 cancel API | delegate + registry | 4h |
| HP-202i | E2E：`tests/gateway/test_background_delegation.py`（3 并发 + agent 运行中完成） | 测试 | 1d |

### 5.5 验收补充

- [ ] `background=false`（默认）与 main **比特级一致**
- [ ] 完成事件在 agent busy 时不丢失（gateway E2E）
- [ ] 不与 `_pending_messages` / approval 路径死锁（复用 internal 路径测试模式）
- [ ] schema 描述保留「跨回合持久 → cronjob / terminal(notify_on_complete)」指引
- [ ] `cargo test` delegation 模块全绿

### 5.6 风险

| 级别 | 项 | 缓解 |
|------|-----|------|
| **高** | Gateway double message guard | HP-202f 强制 internal 路径；对照 `test_internal_event_bypass_pairing.py` |
| **高** | 扇出合并 prompt 过长 | 摘要截断 + 最多合并 3 条（可配置 `delegation.max_merged_completions`） |
| 中 | sync/bg 配额竞争 | 统一 `count_running`；超额返回 tool error |
| 中 | cancel 与运行中 thread | 复用 `interrupt_subagent` 模式 + registry 状态 |
| 低 | 进程 crash 丢委派 | 文档明确非 durable；不写入 state.db |

**评审结论：✅ 批准实施，**必须** HP-201 结论 + HP-202a Rust 先行；估 1–2 周。**

---

## 6. HP-203 — `/delegations` slash 命令

### 6.1 子任务分解

| ID | 子任务 | 产出 | 估时 |
|----|--------|------|------|
| HP-203a | `CommandDef("delegations", …)` aliases `dg`/`dlgt`；CLI + gateway | `commands/registry.py` | 2h |
| HP-203b | CLI handler：`list` / `show <id>` / `cancel <id>` | `cli.py` | 3h |
| HP-203c | Gateway handler + `ACTIVE_SESSION_BYPASS_COMMANDS` | `command_handlers.py`, `run.py` | 4h |
| HP-203d | 输出格式：Rich 表格（CLI）/ 纯文本（gateway）；状态枚举对齐 registry | handlers | 2h |
| HP-203e | Gateway 通知分级：复用 `display.background_process_notifications` | config bridge | 2h |
| HP-203f | 测试：list/cancel；gateway bypass；与 `/background` 不冲突 | 新测试文件 | 4h |

### 6.2 验收

- [ ] `/bg` 仍解析为 `/background`（回归）
- [ ] `/delegations cancel` 可取消 `running` 句柄
- [ ] Gateway help / Telegram menu 自动派生新命令

**评审结论：✅ 批准；依赖 HP-202；估 1 人日。**

---

## 7. HP-204 — `/learn` 技能蒸馏

### 7.1 现状核查

| 组件 | 状态 |
|------|------|
| `agent/background_review.py` | ✅ 被动增量修补；origin `background_review` |
| `tools/skill_manager_tool.py` | ✅ create/patch + `_validate_frontmatter()` |
| `tools/skill_provenance.py` | ✅ ContextVar；**无** `learn_command` |
| `auxiliary.learn` | ❌ 未在 `DEFAULT_CONFIG` |
| `/learn` CommandDef | ❌ |

### 7.2 交互定案（首版）

```text
/learn [skill-name]
  → 收集当前会话（压缩摘要 + 工具轨迹 + 最终结论）
  → auxiliary.call_llm(task="learn", …) 生成 SKILL.md 草案
  → 展示草案（CLI Rich / gateway 消息）
  → 用户确认：y → skill_manage(create) + provenance learn_command + mark_agent_created()
  → 用户拒绝：无落盘
```

**缓存**：仅追加用户消息触发蒸馏，**不**改 system prompt / toolsets — 无 caching 破坏。

### 7.3 子任务分解

| ID | 子任务 | 产出 | 估时 |
|----|--------|------|------|
| HP-204a | `auxiliary.learn` in `DEFAULT_CONFIG` + `OPTIONAL_ENV` 无（非 secret） | `intellect_cli/config.py` | 30m |
| HP-204b | `agent/learn_prompt.py`：消息选取、工具 trace 摘要、技能规范约束写入 prompt | 新模块 | 4h |
| HP-204c | `LEARN_COMMAND = "learn_command"` in `skill_provenance.py` + curator 挂钩 | provenance | 2h |
| HP-204d | `CommandDef("learn", …)` CLI + gateway | registry | 1h |
| HP-204e | CLI handler：生成 → confirm → `skill_manage` | `cli.py` | 4h |
| HP-204f | Gateway handler：confirm 流（gateway 可用 reply 或二次 `/learn save`） | `command_handlers.py` | 6h |
| HP-204g | 测试：mock LLM；拒绝无副作用；frontmatter 校验失败不落盘 | `tests/agent/test_learn_command.py` | 4h |
| HP-204h | （可选）`validate_skill_frontmatter_rs` in `tool_utils.rs` | Rust | 4h |

### 7.4 验收补充

- [ ] 生成 SKILL.md 通过 `_validate_frontmatter()`（description ≤60 等）
- [ ] `created_by: agent` + origin `learn_command` → curator `.usage.json` 可见
- [ ] 拒绝保存时 `~/.intellect/skills/` 无新目录
- [ ] Gateway 长会话：消息选取有 **token 上限**（复用 compression 摘要或末 N 轮）

### 7.5 风险

| 级别 | 项 | 缓解 |
|------|-----|------|
| 中 | 蒸馏质量不稳定 | 用户确认；auxiliary 模型可配置 |
| 中 | Gateway 确认 UX | 首版 `/learn save` / `/learn discard` 子命令 |
| 低 | 与会话 PII | prompt 仅本地 auxiliary；不落日志明文 |
| 低 | 与 background_review 重复 | 文档区分主动蒸馏 vs 被动修补 |

**评审结论：✅ 批准；与 HP-202 **可并行**（HP-201 后）；估 3–4 人日。**

---

## 8. 推荐执行顺序

```text
Week 1
├── HP-201（spike，阻塞 202）──────────────────► 结论文档
├── HP-103f（可选，∥ 201）────────────────────► 独立 PR
└── HP-204a→c（learn 基础，∥ 201/202）────────► 无 gateway 依赖部分

Week 2–3
├── HP-202a→b（Rust registry）─────────────────► cargo test 绿
├── HP-202c→e（Python async + schema）──────────► 单元测试
├── HP-202f→i（gateway + E2E）─────────────────► 最大风险集中
├── HP-203（/delegations）─────────────────────► 依赖 202
└── HP-204d→g（handlers + tests）──────────────► 可与 202 后期并行

缓冲
└── HP-204h（Rust frontmatter，可选）
```

---

## 9. 评审总表

| 任务 | 设计估时 | 评审估时 | 风险 | 决议 |
|------|----------|----------|------|------|
| HP-103f | 1–2d | 1–2d | 中 | ⚠️ **可选**（Phase 1 溢出） |
| HP-201 | 1d | 1d | 中 | ✅ **必须先做** |
| HP-202 | 1–2w | 1.5–2w | **高** | ✅ **批准**（201 + Rust 先行） |
| HP-203 | 1d | 1d | 低-中 | ✅ **批准**（改名 `/delegations`） |
| HP-204 | 3–4d | 3–4d | 中 | ✅ **批准**（与 202 并行） |

### 9.1 通过条件（Phase 2 出口）

1. HP-201 一页结论 **合并进本仓库**（spike 文档或本文件附录）后方可合并 HP-202f。
2. HP-202 **禁止**仅通过 `_pending_messages` 投递完成事件。
3. HP-203 **不得**占用 `/bg` canonical 名（保持 `/background` 别名不变）。
4. HP-204 必须 **confirm-before-write**；无确认不落盘。
5. 全量 `scripts/run_tests.sh` + HP-202 `cargo test`。

### 9.2 与 Phase 3 边界

- HP-202 完成队列 **不** 写入 `state.db`（Phase 3 HP-303 验证台账独立 schema）。
- HP-204 产出的技能可被 Phase 3 HP-401 `/journey` 引用，但 **不** 在本 Phase 建图谱表。
- MoA（HP-301/302）与 HP-202 **无依赖**，可 Phase 3 并行启动 spike。

---

## 10. 评审签字

| 角色 | 结论 | 日期 |
|------|------|------|
| 技术评审 | Phase 2 方案细化完成；HP-203 改名；HP-201 为 202 硬门禁 | 2026-07-08 |
| 待产品确认 | Gateway `/learn` 确认交互（reply vs `/learn save`） | HP-204f 前 |
| 待产品确认 | `delegation.max_merged_completions` 默认（建议 3） | HP-202f 前 |
