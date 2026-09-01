# Intellect Agent 提升计划（对照 Hermes 2026-06-15→08-30 更新窗口）

> **日期**：2026-08-31
> **依据**：
> - `hermes-agent/docs/update-summary-2026-06-15-to-2026-08-30.md`（14,510 提交总览）
> - `hermes-agent/docs/update-summary-2026-06-15-to-2026-08-30-agent-core.md`（agent core 五大主题）
> - 两仓代码实测对照（intellect HEAD = `c6f4d54`）
> **约束**：intellect 已有 21 个 Rust 模块（`rust-core/src/`，~7.8k LOC，pyo3 绑定 `intellect_community_core`）。
> 每项任务都明确「Python 接线 / Rust 落点 / 两者分工」，并延续 **TODO-010** 的迁移路线。
> **纪律**：best-effort、失败关闭、不破坏 prompt cache、模块先行接线后置、每项交付含单测。
> **修订记录**：2026-08-31 技术评审后修订——MP 新增 MP-00 架构决策（multiplex 真实工作量
> 是进程级全局状态而非「接线」）、修正 MP-03 分离声明、MP-01 扩为凭据面审计、G-14 改
> 读连接池方案、G-06 增 cache 政策边界、BT-02 落实注入机制、G-08/G-12 勘误。
> 同日深潜增补：新功能对照 Hermes 源码的实现级分析见
> `2026-08-31-hermes-new-features-deep-dive.md`，G-01/06/12/13/15/18 按分析结论修正
> （多为「升级已有」而非「全新」），索引见 §五。
> 深潜评审修订（同日第二轮）：G-05 补 §4 参照与字节化范围；G-06 前置收窄（`replace_messages`
> 已存在）；G-12 差距确证（完成队列纯内存）；G-15/G-16 落默认值裁定
> （keyless 默认关、project_discovery 默认开+检疫门同批）。

---

## 〇、已具备能力盘点（不再重复实施）

对照 Hermes 更新逐项核实，intellect **已有**下列能力，计划中只做「对齐差距」不重造：

| Hermes 更新 | intellect 现状（锚点） |
|---|---|
| 墙钟预算 80% wrap-up（`803397ecc3`） | ✅ `agent/conversation_loop.py:482-581`（`_run_budget_seconds` + 0.8 阈值注入 wrapup + `_strip_run_budget_wrapup_nudge`） |
| 相同参数重试熔断 | ✅ `agent/tool_guardrails.py:253,326`（identical-args 检测） |
| 微压缩 per-turn | ✅ `agent/context_compressor.py`（`MICRO_COMPACT_MARKER_KEY` 体系） |
| 流式 stale 检测 | ✅ `agent/chat_completion_helpers.py:298`（单 turn 内）；❌ 跨 turn 断路器见 G-03 |
| delegation 结构化输出 schema | ✅ `tools/delegate_tool.py:1184`（response_format json_schema 路由） |
| delegation background / worktree | ✅ `tools/delegate_tool.py:2040,2171`（background=True、worktree 隔离） |
| MoA fanout cadence 签名 | ✅ `agent/moa_loop.py:134`（`_turn_signature` / `_task_signature`） |
| cron 补触发窗口 | ✅ `cron/jobs.py:345`（catch-up window，半周期 clamp 120s–2h） |
| 413 分类 | ✅ `agent/error_classifier.py:48`（`payload_too_large`）；按字节恢复见 G-05 |
| /review 子代理 | ✅ `agent/code_review.py` + `background_review.py` |
| gateway 可靠性地基 | ✅ 本仓 Phase 0/1 已交付（turn-lease、delivery ledger、off-loop+ASYNC 门禁、看门狗+systemd、WS 重放、control socket、profile fail-closed） |
| skills 信任根 | ✅ `agent/skill_commands.py:66-78`（trusted_roots）；项目本地技能见 G-16 |
| **多 profile multiplex 的 seam 层** | ✅ `intellect_constants.py:20`（`set_intellect_home_override` contextvar）+ `agent/secret_scope.py`（`set_secret_scope`/`build_profile_secret_scope`/fail-closed `get_secret`/`set_multiplex_active`）——**实现完整但零调用方**。⚠️ 评审提醒：seam 只覆盖 contextvar 可达的状态；模块级缓存与进程级子系统（config 加载、DB 句柄、插件发现、子进程 env）不受其保护，见 MP-00 |
| profile 生命周期管理 | ✅ `intellect_cli/profiles.py`（1,611 行：create/list/normalize/validate/wrapper/meta/distribution） |
| per-credential 作用域锁 | ✅ `gateway/status.acquire_scoped_lock`（token 锁防两 profile 撞同一凭据） |

---

## 一、差距清单（G-xx）与 Rust 落点

> 优先级：P0 = agent 可靠性主干；P1 = 上下文治理与性能；P2 = 生态与体验。
> 「Rust 落点」列说明该任务哪些部分进 `rust-core`，哪些留 Python。

### 主题 A：运行期防护（Hermes 主题 1）

**G-01 · stall 断路器统一层 + continue-intent 恢复**（P0）
- 差距：Hermes `449471c334` 把「相同 tool_call 空转」做成统一断路器并支持 continue-intent 恢复；intellect 的 guardrails 只在 prompt 层劝阻（`tool_guardrails.py:253`），无硬断路、无恢复语义。
- **深潜修正（2026-08-31 深挖）**：intellect `ToolCallGuardrailController` 已有 identical-args 检测（halt-decision 式）。真实差距 = ①`observe_call` streak 状态（签名+结果双哈希，第 3 次起 notice、第 2 次起 ≥512 字符结果替换为引用桩）②`trailing_continue_intent` 恢复（尾部 160 字符窗口 + interim-assistant + nudge，每 turn 2 次上限）。落点：扩展现有 controller（不动 halt 路径），notice/stub 在工具结果构造点注入（append-only 保 cache）。实现细节见深潜文档 §1。
- 动作：扩展 `agent/tool_guardrails.py`（streak/notice/stub）+ `agent/conversation_loop.py` 恢复注入；可配置 `agent.stall_guards`。
- Rust 落点：**canonical-args 归一化进 `rust-core/src/tool_utils.rs`**（纯函数、每 turn 热路径：参数 dict → 规范 JSON 哈希）。Python 保留状态机与 prompt 注入。
- 验收：注入式单测（同调用 ×N 触发 notice/stub；换 args 或结果变化不触发；continue-intent 后 turn 正常收尾）。

**G-02 · 统一 deadline 层（有界执行原语 + 超时解析器）**（P0）
- 差距：Hermes `083f8a6071`/`367f0c21ed` 把散落的超时收拢为一个原语；intellect 的 stale timeout、tool timeout、child timeout 各自为政（`chat_completion_helpers.py:304`、`delegate_tool.py` child_timeout、cron 3-min 硬中断）。
- 动作：新增 `agent/deadline.py`：`bounded(coro, budget)` + `resolve_timeout(kind, model_class, run_budget)` 单一解析点；各处改调用。run-budget 已有（见盘点），此处是把「按 deadline 比例缩放 stale 阈值」接入解析器。
- Rust 落点：**纯 Python**。asyncio 原语与取消语义不适合跨 FFI；rust 无收益。
- 验收：单测覆盖 resolver 表（reasoning 模型 stale 下限、预算缩放、clarify 豁免）；sabotage 测试：绕过 resolver 直接写裸 timeout 的调用点 lint 报警（自定义 ruff/grep 门禁可选）。

**G-03 · 跨 turn 流式 stale 断路器**（P1）
- 差距：Hermes `985e19c110`；intellect stale 检测均在单次 API 调用内，provider 流跨 turn 长时间无推进无熔断。
- 动作：在 conversation_loop 层维护 last-progress 时间戳（token/事件到达即更新），超过 `stream_stale_budget`（默认 = max(stale_timeout × K, 300s)）→ 中断 + 走既有错误分类恢复。
- Rust 落点：**复用 `rust-core/src/stream.rs` 的 StreamAccumulator**：在 accumulator 里加 last_event_monotonic 暴露给 Python，避免 Python 层再记一遍。
- 验收：fake provider 停止发事件 → 断路器触发 → 错误分类进入重试路径。

### 主题 B：上下文治理（Hermes 主题 2）

**G-04 · 上下文锚定到 provider 上报 usage**（P0）
- 差距：Hermes `d3a1c46510`；intellect 估算体系（`agent/model_metadata.py` estimate_*）没有「以 provider `usage` 为准、估算只管最后一 turn」的锚定层。
- 动作：`agent/context_anchor.py`：每 turn 保存 provider 上报的 prompt/completion tokens；估算 = anchor + 上一 turn 增量；压缩阈值判断读锚定值。修复估算器对重复 content 的重复计数（对照 `e3bc517034`）。
- Rust 落点：**扩展 `rust-core/src/usage.rs`**（Usage 标准化 + TokenAccumulator 已在）：新增 `ContextAnchor` 结构（序列化进 state.db 单键），Python 只做读写接线。
- 验收：注入 fake usage（远偏于估算）→ 压缩触发点跟随真实值；估算与上报偏差遥测上报上下文占用率而非"省了多少"。

**G-05 · 413 恢复按字节计量**（P1）
- 动作：`payload_too_large` 恢复路径从 token 估算改为按请求体字节数裁剪（对齐 `b855f86bc8`）。
- 深潜参照（§4）：`serialized_messages_bytes` 精确度量 + 进步判定「字节降 ≥5% **或** 消息数减少」+ 无字节进步时剥 tool 消息图片 part 兜底（token 估算对图片 flat 计价看不见 base64 字节，这是根因）。⚠️ Hermes 仅 413 分支字节化（`conversation_loop.py:5763`），同文件 `:5909/:6074` 仍按 token——属其未完成迁移，intellect 不必跟随扩散。
- Rust 落点：字节测量与裁剪决策进 **`rust-core/src/compression.rs`**（纯计算），Python 处理重试编排。

**G-06 · positional prune（大窗口模型的过期工具结果裁剪）**（P1）
- 差距：Hermes `cb481e2f2b`/`fa4800414c`/`93f4dc7561`；intellect 无对应机制。
- **深潜修正（2026-08-31 深挖）**：压缩内的 `_prune_old_tool_results` **已存在**（`context_compressor.py:816`，摘要化同源）。真实差距 = ①proactive 独立触发入口（压缩被 cooldown 阻塞时的 elif 分支）②reclaim gate（`before-after ≥ min_reclaim_tokens`（4096）才提交，否则整体 no-op）③rearm runway 持久化（防反复 break cache 的迟滞）。前置已核实（深潜评审）：`replace_messages`（`intellect_state.py:2019`，rust 路径 `:2084`）原子换 transcript 原语**已存在**；剩余仅 rearm 值的持久化位置（session model_config 单键或等价物）实施时定。详见深潜文档 §5。
- 动作：对 context ≥ 阈值的会话，主动把过期（非最近 K turn）工具结果替换为引用桩；带 prompt-cache 回收门槛（预估 cache 命中损失 > 收益则跳过）；variant-aware（按 provider 家族开关）。
- Rust 落点：**`rust-core/src/compression.rs`**（裁剪评分 + 引用桩生成纯函数）；token 计数复用 `tokens.rs`。Python 决定何时触发。
- **政策对齐（评审新增）**：AGENTS.md 规定「唯一允许改历史上下文的时机是压缩」。prune 受同一政策管辖而非豁免——执行边界写死：仅在压缩/turn 边界触发、**默认关闭**（`compression.positional_prune: off`）、cache 损益门槛不达标即跳过；实施 PR 须引用本条说明合规。
- 验收：构造 60% 过期工具结果的会话 → prune 后估算下降且 cache 门槛逻辑可单测；默认配置下行为与现状逐字节一致。

**G-07 · 历史清洁：sidecar / 未配对 tool result 清理**（P1）
- 动作：压缩与 resume 路径统一过一遍「剥离过期 sidecar 内容、删除未配对 tool result、合并 assistant carrier 完整性」（对齐 `f0ac2c8f12`/`e1762bd30b`/`6b7aee2f80`）。
- Rust 落点：**`rust-core/src/sanitize.rs` 扩展**（消息对完整性校验纯函数已有雏形，扩展 tool_call_id 配对矩阵）。
- 验收：脏历史注入用例（缺 tool result、重复 id、空 assistant carrier）→ 清洁后可过 provider schema 校验。

### 主题 C：协议健壮性（Hermes 主题 4/5）

**G-08 · tool_call_id 变体配对**（P0）
- 差距：Hermes 一整类 fix（`1a83b1e588` 等 6+ 提交）；intellect 未见 id 变体处理（关键词级 grep 零命中）。多 provider 下 id 前后缀/大小写变体会导致 tool result 配对失败。
- ⚠️ 勘误（评审）：「零命中」是关键词级结论——配对/去重逻辑可能以别的命名存在于 `message_sanitization` 等路径。实施首日先人工核验，已有别名处理则本任务降级为补测试，不重复实现。
- 动作：在消息进出两处做 id 规范化 + 变体索引（`agent/tool_call_id.py`）：结果按所有变体可查、重调用同 id 不丢结果、pre-call 去重 variant-aware。
- Rust 落点：**规范化 + 变体索引进 `rust-core/src/tool_utils.rs`**（热路径字符串处理）。
- 验收：变体 id（带 provider 前缀/去前缀/大小写差异）注入用例全过；重复调用同 id 结果保留。

**G-09 · 图片损坏恢复矩阵**（P1）
- 动作：错误分类补齐 `image_too_large`（"media exceeds size limit"）、Kimi/Moonshot 400 → 自动 strip 图片重试、下载响应损坏归类（对齐 `b3f4f50771` 等）。重试期间保持 canonical history 完整。
- Rust 落点：**`rust-core/src/error_classifier.rs`**（分类字符串匹配天然适合；已有 13 函数 786 行）。

**G-10 · billing/cooldown 边界分类**（P2）
- 动作：Anthropic "out of extra usage" 歧义贯穿 classification→cooldown→terminal；确定性空响应不重复计费；relay 包装 429 路由到 output-cap handler。
- Rust 落点：同 G-09，`error_classifier.rs`。

**G-11 · per-model 用量追踪（支撑 mid-session 换模）**（P1）
- 差距：intellect 无 per-model 累计（grep 零命中）；`/model` 换模后成本视图断档。
- 动作：account_usage 侧按 (session, model) 累计，`/usage` 与 TUI 展示分模型。
- Rust 落点：**`rust-core/src/usage.rs` TokenAccumulator 扩展 per-key 维度**；Python 写 state.db。

### 主题 D：delegation / MoA（Hermes 主题 3 + 三）

**G-12 · delegation 扩容 + 实时可观测**（P1）
- 差距：并发 3（config 默认）vs Hermes 10；迭代上限 vs Hermes 250；child transcript 不可 tail；无 live steering。
- **深潜修正（2026-08-31 深挖）**：intellect 已有 `_active_subagents` registry（`delegate_tool.py:155`）、parent 级 steer 通道（`run_agent.py:2148` + `apply_pending_steer_to_tool_results:2490`）、background 委派（`:2040`）。真实差距 = ①subagent steering 面（registry 锁内 owner 三元组对象同一性校验 + `accepting_steer` + 竞态 `missed_steer`；marker 格式见深潜 §6，不可简化为裸提示）②live transcript（`delegation_live_log.py` 近乎直搬：`cache/delegation/live/` 路径约定 + per-line redact + append-per-write）③durable 完成队列（SQLite claim 三态 + 尝试上限 + 崩溃恢复——与 gateway delivery ledger 同构，模式可复用；**已核实**：intellect `async_delegation.py:24` 的 `_completion_queue` 为纯内存 List、rust registry 仅 register/complete/cancel，durable 队列确认缺失）。详见深潜文档 §6。
- 动作：
  1. `max_concurrent_children` 默认 3→8（**config.yaml 默认值调整**——勘误：delegation 上限在 config 不在 DB，初版「DB migration」系照抄 Hermes 语境），子代理 `max_iterations` 上限提高并按角色分档（leaf/orchestrator）；
  2. child transcript 落 `cache/delegation/live/<delegation_id>/task-<n>.log`（远程后端只读挂载点，任何后端可 `tail -f`），`/agents` 可 tail；
  3. subagent steering：扩展现有 registry + steer 通道（锁内身份校验、双 drain 点：工具批后 + pre-API 兜底）。
- Rust 落点：**`rust-core/src/delegation.rs` 已有 DelegationRegistry**（`tools/async_delegation.py:64` 在用）：扩 steering 消息队列与 transcript ring 到 registry；Python 只做命令面。
- 验收：4 child 并发不再排队饥饿；tail 输出与最终 summary 一致；steering 消息出现在 child 下一轮上下文；伪造 owner 的 steer 被拒。

**G-13 · MoA 补齐**（P2）
- **深潜修正（2026-08-31 深挖）**：facade（`moa_loop.py:575` `_ChatNamespace`）与 `moa/<preset>` 虚拟 provider（`agent_init.py:329`）**已存在**，preset 虚拟模型不再是差距。真实差距 = ①`every_n:<N>` cadence（状态签名真正前进才消耗槽位，off-cadence 钉 cache key 复用 guidance）②guidance 三形态 attach + 精确 peel（保 KV 前缀缓存）③事件族渲染（`moa.reference{label,index,count}` 标签块 / `moa.progress` / `moa.phase`）④`enabled:false` 不隐式匹配 + 禁递归校验。详见深潜文档 §7。
- 动作：按上述四项补齐；全部 reference 失败时 aggregator 单独行动、中断可中止 fan-out（`moa_loop.py` 已有 interrupt 骨架）。
- Rust 落点：纯 Python（编排/UI）；token 裁剪复用 `tokens.rs`。

### 主题 E：State 性能（Hermes 主题 4/state）

**G-14 · SessionDB 读路径脱离写锁 + 读热路径迁 Rust**（P0，与 TODO-010 合并）
- 差距：Hermes 39 个纯读方法脱离写锁；intellect `SessionDB` 读写共用 backend 单锁（`intellect_state.py:365-371`），且 `list_sessions_rich`（1586）是每次会话列表/切换的热路径。
- 动作（三步，**评审修订**：初版「Python 层读写锁分离」在单连接下无意义）：
  1. backend 增**只读连接池**：实测 `agent/storage/sqlite_backend.py:45-69` 为单连接 + `threading.Lock`，WAL 的并发读收益只在**多连接**间成立——为读方法提供 per-thread 只读连接（WAL 安全），写路径保持独占写连接 + 锁；
  2. ~~`list_sessions_rich` 的 SQL 迁入 `rust-core/src/backend.rs`~~ **关闭（2026-08-31 profile 裁决）**：cProfile 实测 SQL 执行占 92%、python 组装 ~8%——迁移无收益（A3-8 纪律：无收益即关闭）。真杠杆是 SQL 形态（preview 相关子查询 → JOIN/索引）。读池第一段已把 p50 35.7 → 30.8ms；
  3. 别名/变量读取盲点对齐（对照 Hermes 3.9）。
- 验收：并发读写在隔离测试下无 `database is locked` 且读吞吐随并发提升（基准对比单锁基线）；`list_sessions_rich` 基准（10k sessions）读延迟显著下降；写路径行为不变。

### 主题 F：生态与平台（对齐 Hermes 4.x/3.x 其余亮点）

**G-15 · keyless provider + 免费网络检索池**（P1）
- 动作：web_search_provider 体系加 keyless 层（Exa/Parallel MCP 免费端点 + Tavily keyless header + Firecrawl/Keenable 匿名层，5 厂商轮询 + 一次性救援），`intellect tools` 可选免费/付费端点。
- **深潜修正（2026-08-31 深挖）**：①初版「50/50 流量分流」系文档误传，源码实为 5 厂商均匀轮询（进程随机播种 cursor + 仅 rate-limit 形错误前进）；②`plugins/web/` 已有 exa/parallel/tavily/firecrawl 四家 keyed 版（缺 keenable）——移植 = 新增 `keyless_mcp.py`（自包含）+ 各 provider 补 `is_keyless_available()`（`is_available()` 保持只看 key，防 legacy 序路由错）+ registry `_resolve` 加第 4 步 keyless walk（严格最后）+ 救援挂 web_tools 分发层（结果不写缓存、非粘性）。实现细节见深潜文档 §8。
- Rust 落点：纯 Python（网络 IO）；不作 rust 候选。
- **默认值裁定（评审 2026-08-31）**：Hermes `web.keyless_fallback` 默认 **true**（用户查询默认流向匿名第三方端点）。intellect 取**默认 false** + 显式开启（首次开启提示查询将离开本机流向匿名端点）——与仓库 fail-closed 纪律一致；与 Hermes 的差异在 website docs 显式记录。
**G-16 · 项目本地技能 + 每仓库信任门**（P1）
- 动作：发现 `<project>/.intellect/skills/`，首次加载弹信任门（非交互场景继承显式配置）；与既有 trusted_roots 打通（`skill_commands.py:66`）。
- **默认值裁定（评审 2026-08-31）**：`skills.project_discovery` 跟随 Hermes 默认 **true**，**硬前提**：内容扫描/检疫门与发现功能**同一 PR 交付**——它是「trust 后 git pull 注入恶意技能」的唯一防线，不可后补。
- **深潜补充（2026-08-31 深挖）**：Hermes 形态 = 信任记录存 config.yaml `skills.trusted_project_dirs` 绝对路径列表（非独立文件）、无首次交互 prompt（banner 提示 + 显式 `skills trust` 命令）、**内容级第二道门**（信任只放行发现，加载仍过内容扫描，scanner 崩溃 fail-closed 检疫——防 trust 后 git pull 注入）；非交互继承靠 `TERMINAL_CWD` per-surface workdir。见深潜文档 §9。
**G-17 · 会话导入（Claude Code / Codex CLI）**（P2）
- 动作：`intellect sessions import <format>` 把外部会话 JSONL 转入 state.db（沿用 SessionDB 写 API），`--continue` 每终端恢复。
- **深潜补充（2026-08-31 深挖）**：转换契约三条不可破坏——只产纯 user/assistant 文本、**从不伪造 tool_calls**（工具史成括号摘要）、system payload 永不导入；写入仅用 SessionDB 三方法 + `origin.imported_from` 元数据；`--continue` 的 per-terminal breadcrumb（tty/multiplexer env 推导）可作第二步。见深潜文档 §9。
**G-18 · MCP 结果治理**（P2）
- 动作：MCP 工具结果 >50K 告警 + 相同重调用转引用桩（省 token，与 G-06 共用桩生成器）；MCP 健康扫描命令（`intellect mcp doctor`）；OAuth 锁范围收窄审查。
- **深潜修正（2026-08-31 深挖）**：`tools/tool_result_storage.py` 的 spillover（`<persisted-output>` + 100K/200K 预算）**已存在**。真实差距 = ①`budget_config.py` 加 MCP 紧门槛层（`mcp_` 前缀 50K，优先序 PINNED > tool_overrides > mcp 前缀 > 默认）②identical-result 引用桩（与 G-01 的 stub **同一实现两处复用**，桩格式见深潜 §1/§8）③健康扫描。见深潜文档 §8。
- Rust 落点：引用桩生成与 G-06 同源（`compression.rs`）。
**G-19 · 更新机制硬化**（P2）
- 动作：`/update` 非交互 `--yes`、回滚快照命名保留、锁定 uv sync 保留项目配置。对照 `scripts/release.py` 与既有 update 流程增量补齐。

### 主题 H：多 profile multiplex（2026-08-31 决策：开放；同日按技术评审修订）

> 背景：原计划将「多 profile 路由」列为不做（GW-302 仅 fail-closed）。应产品决策翻转：
> 单 owner 的多 profile 服务形态正式开放。**这不是 multi-user**——members/teams/projects
> 仍 WONTFIX；multiplex 是「同一 owner 的多个隔离 profile」的隔离扩展。
> 参照 Hermes `gateway/profile_routing.py`（层级匹配 + specificity 排序 +
> `ProfileRouteRejected`）与 `run.py` multiplex 半边（`_multiplex_profile_homes` /
> `_profile_runtime_scope` / 二级 profile 端口绑定冲突检测）。
>
> **评审修订**：初版「seam 已就绪、主要是接线」的判断**不成立**。两个 contextvar seam
> 只覆盖 contextvar 可达的状态；以下四类**进程级**状态不受保护，是 multiplex 的真实
> 工作量：
> ① **模块级 `get_intellect_home()` 缓存**——全仓 20 个文件 import 时冻结路径
> （`agent/auxiliary_client.py:424` auth.json、`tools/skills_tool.py:91`、
> `tools/skills_hub.py:50`、`gateway/mirror.py:21`、`gateway/hooks.py:32` 等），
> profile B 的 turn 会读写启动 profile 的目录（AGENTS.md 第 3 条裁定前提是单 profile
> 进程）；
> ② **进程级子系统无 per-profile 归属**——gateway config 启动时从默认 profile YAML
> 加载一次（`gateway/config.py:793`）、SessionDB 句柄（tui_gateway `_get_db` 全局
> 缓存）、插件发现（`model_tools` import 时扫默认 home）、cron scheduler、curator、
> kanban dispatcher、skin、memory provider；
> ③ **子进程 env 桥接**——contextvar 不进 subprocess；工具子进程需显式
> `INTELLECT_HOME` 注入，且 multiplex 下不能按 turn 改共享的 `os.environ`；
> ④ **agent 工厂**——`GatewayRunner(config)` 单 config 按 session 建 AIAgent，需
> per-profile config 加载与构造（Hermes 为此专设 `gateway/agent_cache_pressure.py`）。
> MP-00 做架构决策后，MP-03+ 的形态才确定。

**MP-00 · 架构决策：supervisor 多进程 vs in-process（P0，先于一切 MP 实施）**
- 候选：
  - **(a) supervisor 多进程（推荐起步）**——薄前端路由进程 + 每 profile 一个既有
    单 profile gateway 子进程（`intellect -p <name> gateway --child`）；前端持有唯一
    HTTP/WS listener，按 `/p/<profile>/` 前缀与 profile_routes 转发（WS 反代或经
    control socket RPC）。per-profile 的 state.db/心跳/config/插件/cron/凭据由**进程
    边界**天然隔离，①~④ 整体消失；run.py duplicate-instance guard 的「每 profile
    一个 INTELLECT_HOME 可并发」注释正是此形态的设计预留。代价：多进程内存、转发
    层、每 profile 单独升级。
  - **(b) Hermes 式 in-process**——单进程内 per-profile「runtime bundle」（config
    加载 + DB 句柄 + agent 工厂 + env 桥接 + seam 安装）。省内存低延迟，但 ①~④
    全部要审计改造（20 个模块级缓存逐一惰性化），风险与工作量显著高于 (a)。
- 动作：产出 ADR（含 ①~④ 审计清单 + 内存/延迟 footprint 预期）；**默认走 (a)**，
  (b) 作为后续优化路径保留（两个 contextvar seam 不废弃，在 (a) 前端自身与远期 (b)
  中继续使用）。
- 验收：ADR 落盘；(a) spike——两 profile supervisor 起停 + 前缀路由转发 + 杀 B 不影响
  A 的冒烟。

**MP-01 · 凭据面审计 + `get_secret` 迁移（安全前置，P0；(a)/(b) 均必做）**
- 现状（**评审修订**：初版只数 9 处，实际面更宽）：`agent/credential_pool.py`
  （~6 处，`:1711-1717` 还优先读 `~/.intellect/.env` 文件字典）、
  `agent/credential_sources.py`（~3 处）、`agent/auxiliary_client.py:424`
  （`_AUTH_JSON_PATH` **模块级缓存**）、web_search/image_gen 等 provider 插件的 env
  读取、平台适配器经启动 config 拿凭据的路径。multiplex 下任一未迁移点 = 跨 profile
  泄 key（单 profile 部署下该迁移也具备纵深防御价值，独立可交付）。
- 动作：
  1. 产出凭据读取全量清单（裸 `os.environ`/`os.getenv` + `.env` 文件读 + config
     凭据字段流），按「必须迁移 / 存量豁免（注明原因）」归类，随 PR 存档；
  2. 迁移到 `secret_scope.get_secret()`（非 multiplex 模式透明回退 `os.environ`，
     行为不变）；
  3. CI grep 门禁：`agent/`+`gateway/`+`plugins/` 禁止新增裸凭据 env 读（allowlist
     豁免）。
- Rust 落点：纯 Python。
- 验收：multiplex_active 下无 scope 读凭据 → `UnscopedSecretError` 冒烟；单 profile
  回归零变化。

**MP-02 · profile 路由模块（P0）**
- 动作：新增 `gateway/profile_routing.py`（对齐 Hermes 语义）：`ProfileRoute`
  （platform+guild/chat/thread 判别子、AND 合取、父链匹配、specificity 排序）、
  `parse_profile_routes`（含 `validate_profile_name` 防路径穿越）、
  `match_profile_route`、`ProfileRouteRejected`。config 键：`gateway.profile_routes`。
- Rust 落点：**Python**（配置量小、每消息一次匹配非热路径；rust 化收益不抵 FFI 成本，
  显式标注「评估过，不做」）。
- 验收：纯单测覆盖 specificity 顺序/合取/父链/非法 profile 名拒绝。

**MP-03 · multiplex 运行时接线（P0，形态取决于 MP-00）**
- 若 MP-00 = (a)：实现 supervisor——按 serve 集拉起/监护每 profile gateway 子进程，
  启动序校验二级 profile 端口绑定冲突（拉起前拒），子进程崩溃按 profile 粒度重启
  （不连坐其他 profile）。
- 若 MP-00 = (b)：实现 `_profile_runtime_scope(home)`（同时 set
  `set_intellect_home_override` + `set_secret_scope(build_profile_secret_scope(home))`，
  turn 结束 reset；`copy_context()` 已保证向 worker 线程传播），**且必须完成 MP-00
  审计清单 ①~④ 的全部改造**：① 20 个模块级缓存惰性化（改为调用点取路径）；
  ② per-profile config 加载 + SessionDB 句柄池 + 按需插件/skills 发现；
  ③ 子进程 spawn 显式 `env=dict(os.environ, INTELLECT_HOME=<profile>)`；
  ④ per-profile agent 工厂。
- 无论 (a)/(b)：无路由匹配 → 默认 profile；命中但不在 serve 集 →
  `ProfileRouteRejected` 显式拒（fail-closed 延续）。
- **勘误（评审）**：初版「每 profile 的 state.db/心跳/control socket 天然按
  INTELLECT_HOME 分离，无需改动」**不成立**——multiplex 单进程只有启动 profile 的
  心跳/control socket/pid 文件；进程级看门狗/心跳只有一份（属 supervisor/前端）；
  state.db 分离在 (a) 下由进程边界成立、(b) 下需显式 per-profile 句柄。MP-06 的
  `served_profiles` 观测面对此补盲。
- Rust 落点：纯 Python。
- 验收：两 profile 并发 turn 互不可见对方 key/会话/skills（注入式隔离测试，参照
  Hermes `test_profile_isolation_runtime.py`）；路由拒绝路径有可见错误；(a) 下杀
  profile B 子进程不影响 A 服务。

**MP-04 · HTTP 平台 `/p/<profile>/` 前缀复用（P1）**
- 动作：绑定 host 端口的平台（`api_server.py`、`webhook.py`）的 listener 统一持有——
  (a) 属 supervisor 前端、(b) 属默认 profile——按 URL 前缀 `/p/<profile>/` 服务各
  profile；二级 profile 配置独立端口绑定时**启动即拒**
  （`SecondaryPortBindingConfigError` 语义），防单个 profile 拖死整个 multiplexer。
- Rust 落点：纯 Python。
- 验收：两 profile 同端口不同前缀各自收到回调；冲突端口配置启动失败且报错可读。

**MP-05 · WS `/p/<profile>` 路由解锁（P1，改写 GW-302）**
- 动作：`tui_gateway/ws.py` 现守卫升级为路由器：multiplex on 且前缀合法 → 进对应
  profile 的会话命名空间（session dict 按 profile 分片）；multiplex off → **维持现状
  4404 fail-closed**（守卫代码保留，条件分支解锁）。
- 鉴权（**评审新增**）：multiplex 下单一 `TUI_AUTH_TOKEN` = 一票通全部 profile。
  默认 per-profile token（`TUI_AUTH_TOKEN_<PROFILE>`，未配置回退全局）；若产品上接受
  单 owner 信任模型，须在 MP-07 文档显式记录该取舍。
- 验收：off 时旧行为不变（既有测试直接复用）；on 时 `/p/a`、`/p/b` 会话与事件流隔离，
  event_replay 的 epoch/seq 命名空间按 profile 分片；per-profile token 下 A 的 token
  访问不了 B 的前缀。

**MP-06 · 可观测与运维面（P2）**
- 动作：`write_runtime_status(served_profiles=...)`（对勘误中「进程级心跳/看门狗只有
  一份」的补盲：status 需暴露每 profile 子进程的 pid/state）；`intellect doctor`
  检查 serve 集内各 profile 的凭据/锁冲突；control socket `identify` 增加 `profile`
  字段（(a) 下 supervisor 聚合各子进程 status）；`intellect gateway status` 展示
  multiplex 拓扑。
- 归属说明（**评审新增**）：cron scheduler、curator、kanban dispatcher 均为进程级
  单例——(a) 下天然 per-profile（各子进程各自调度自己的任务集）；(b) 下需 per-profile
  实例化或明确只归属默认 profile（文档写死）。本计划不引入跨 profile 的 cron/kanban
  混布。
- Rust 落点：`gateway.rs` 若后续承接 status 聚合可加字段，当前纯 Python。

**MP-07 · 政策与文档同步（P0，与 MP-03 同 PR）**
- 动作：AGENTS.md「Single-user (permanent)」节增补：multiplex = 多 profile 单 owner，
  members/teams/projects 仍 WONTFIX 不变，并同步修订 AGENTS.md 第 3 条「模块级
  `get_intellect_home()` 缓存没问题」的适用前提（multiplex (b) 路线下该模式被禁，
  见 MP-00 ①）；website docs 新增多 profile 服务指南（config 示例、端口/凭据/锁
  规则、鉴权取舍）；MP-00 的 ADR 归档至 `docs/plans/`。GW-302 的 fail-closed 决策
  记录改写为「multiplex off 时的默认行为」。

### 主题 I：Bot Mode（依赖主题 H，P1/P2）

> 形态对齐 Hermes Bot Mode：每个 profile 即一个 bot agent，bots 互 DM 组成 agent 团队，
> 可进群房间。**边界声明**：参与者是同一 owner 的 profiles + 平台既有的群聊成员，
> 不是 multi-user 成员系统（沿用 intellect 既有群聊能力，不新做成员管理）。

**BT-01 · Bot Chat 骨架 + 本地 roster（P1）**
- 动作：multiplex serve 集内的 profile 列表即 roster（`~/.intellect/bot_mode/roster.json`
  由 gateway 维护）；Bot Chat 会话 = 与目标 profile 的一次 `intellect -p <name> chat`
  单轮交互（对齐 Hermes 传输层），走既有 `terminal(background=True,
  notify_on_complete=True)` 唤醒路径——intellect 已具备全部底座。
**BT-02 · `message_agent` 工具（P1）**
- 动作：`tools/bot_mode_dm.py` 语义移植：schema 仅注入 Bot Chat 会话、执行时
  title-gated 二次校验（defense in depth）、目标对 roster 校验、attribution 服务端前缀。
- 注入机制（**评审勘误**：初版写「经 gateway builtin_hooks 注入 schema」——
  builtin_hooks 是生命周期钩子扩展点，**不支持工具 schema 注入**）。正确机制二选一：
  1. `get_tool_definitions()` 动态后处理（AGENTS.md 既有模式，`browser_navigate`/
     `execute_code` 后处理块是先例）：Bot Chat 会话的请求上下文中追加 schema；
  2. 独立插件仓经 `ctx.register_tool(check_fn=...)` 注册，`check_fn` 内做 title-gate。
  两条路径都**不进核心 toolset**，dispatch 时再校验一次会话来源（forged call 返回
  结构化错误而非投递）。
- Rust 落点：纯 Python。
**BT-03 · 跨网关 relay（P2，可裁剪）**
- Hermes 的 relay 依赖 Desktop 连接线聚合；intellect 无桌面形态，裁剪为：
  peer 网关 = 配置的远端 gateway URL（复用 MP-04 的 `/p/<profile>/` 前缀 + 既有
  TUI_AUTH_TOKEN 鉴权），`outbox/replies` 文件协议同 Hermes。首期可不做，仅保留
  roster 本地模式。
**BT-04 · 房间体验（P2）**：群房间预算（每房间 token/turn 上限）、可编辑房间名、
  确定性 blob 头像（纯算法生成，`agent/avatar.py`，无网络依赖）。

### 主题 J：Pets（独立体验项，P2）

- **PT-01** 移植 `agent/pet/` 包：store（安装至 profile `pets/` 目录，走
  `get_intellect_home`）+ manifest（petdex gallery 拉取，网络失败降级本地清单）+
  doctor。**PT-02** `intellect pets` CLI（list/install/select/doctor，写
  `display.pet.*` config）。**PT-03** TUI 渲染：`ui-tui` 新增 petSprite 组件
  （kitty/Ghostty 图形协议，不支持时静默禁用——平台门控沿用 skills 的 platforms 机制）。
- Rust 落点：无（TSX 渲染 + Python CLI）；头像/pet 图资源不得进 rust-core。

### 主题 G：Rust 基建自身

**G-20 · pyo3 绑定打包硬化**（P1）
- 现状：`intellect_community_core/__init__.py` 里有一段 sys.path 重排 hack（源码目录 vs site-packages 竞争），且 `tools/async_delegation.py:68` 提示「rebuild 才可用」的降级路径。
- 动作：maturin 构建产物校验（import 时 fail-fast 报版本不匹配而非 AttributeError）、`rust-core/README.md` 补构建矩阵、CI 加 `cargo test + maturin develop + import 冒烟` 单 job。
**G-21 · stream_consumer / delivery 迁移收尾（TODO-010 第 2/3 项）**（P2）
- `gateway/stream_consumer.py` SSE 缓冲解析增量进 `stream.rs`；`gateway/delivery.py` 路由纯函数进 `gateway.rs`。先 benchmark 后迁移，无收益即关闭该项。

---

## 二、实施顺序（双轨并行 + 独立体验轨）

```
双轨 A：agent 可靠性（与 multiplex 无耦合）
  A-1  G-01 stall 断路器 ─┐
      G-02 deadline 层  ─┼─ 同属运行期防护，建议同 PR 系列交付
      G-08 tool_call_id ─┘   （实施首日先核验「零命中」结论，见该条勘误）
      G-04 usage 锚定（独立）
      G-14 SessionDB 读连接池 + 热路径迁 Rust（按修订后的多连接方案）
  A-2  G-05 → G-06（受 cache 政策边界约束，默认 off）→ G-07（共享 compression/sanitize 扩展）
      G-03 跨 turn stale（依赖 G-02 resolver）
      G-12 delegation 扩容 + steering
      G-20 rust 绑定硬化（为 rust 改动铺路，可提前）
  A-3  G-09/G-10 + 长尾：G-11/13/15/16/17/18/19/21

双轨 B：multiplex（评审修订：MP 体量重估后独立成轨，不再挤占 A 的轮次）
  B-0  MP-00 架构决策（推荐 (a) supervisor；与 A-1 并行启动）
      MP-01 凭据面审计 + get_secret 迁移（(a)/(b) 均必做，独立有价值，与 A-1 并行）
  B-1  MP-02 路由模块（纯单测，随时可做）
  B-2  MP-03（按 MP-00 形态）+ MP-07 政策文档 → MP-04 → MP-05 → MP-06
  B-3  BT-01/BT-02（依赖 MP-03）→ BT-04 →（可选）BT-03

独立体验轨：PT-01/02/03（Pets，无依赖，随时）
```

> 顺序说明（评审修订）：初版「multiplex 边际成本主要在 MP-01」的判断已被推翻——
> 真实工作量在四类进程级全局状态（见主题 H 评审修订注），故 MP 独立成轨并以
> MP-00 决策开头，避免挤占 A 轨既定的上下文治理轮次；A/B 两轨无共享代码路径
> （G-14 的 backend 改动与 MP 的进程编排不冲突），可并行推进。MP-01 即便最终
> 不做 multiplex 也值得先做（凭据读取纵深防御）。

## 三、每任务统一交付物

- 模块代码 + 单测（stdlib + pytest + mock，无网络）；
- 接线类任务附一个行为测试（断路器真的断 / 锚定值真的驱动压缩 / 读写真的并发 /
  **multiplex 下 profile A 的 turn 读不到 profile B 的 key**）；
- 涉及 rust 的任务：`cargo test` 通过 + Python 侧 import 冒烟 + `intellect_community_core` 构建说明更新；
- 遵守仓库门禁：`ruff check .` 绿（含 ASYNC 规则）、`scripts/run_tests.sh` 相关目录绿、无 change-detector 测试。

## 四、显式不做（与仓库政策对齐）

> 2026-08-31 修订：多 profile multiplex 与 Bot Mode/pets 由「不做」翻转为实施项
> （主题 H/I/J）。**不变的部分**：multiplex 是单 owner 的 profile 隔离扩展，
> members/teams/projects 的 multi-user 语义仍然 WONTFIX（MP-07 会把这条边界写进
> AGENTS.md）；GW-302 的 fail-closed 守卫保留为 multiplex 关闭时的默认行为。

- **桌面端 Desktop/HUD/看板 UI**（Hermes 3.1，fix 1499）— intellect 无桌面端形态，对应能力走 WebUI/TUI，另立计划评估，不在本计划。
- **multi-user members/teams/projects** — 永久 WONTFIX（v0.5.0 移除，不随 multiplex 开放而回归）。
- **在树内新增 memory provider** — 违反 2026-05 政策，一律独立插件仓。
- **BT-03 跨网关 relay 的 Desktop 半边** — intellect 无桌面形态，relay 首期只做
  peer-gateway URL 模式（见 BT-03），Desktop 聚合线不移植。

## 五、新功能深潜索引（2026-08-31 增补）

> 对本计划列举的功能做了「新功能 vs 升级 vs 强化」分类，并对全部**新功能/升级项**对照
> Hermes 源码做了实现级分析（锚点/机制/数据格式/config/测试/intellect 真实差距），
> 落盘于 **`docs/plans/2026-08-31-hermes-new-features-deep-dive.md`**。要点：

- **血统警告**：两仓共享祖先——`tool_guardrails.py`、`chat_completion_helpers.py`、
  `tool_result_storage.py`、`context_compressor.py`、steer 体系在 intellect 均有同源文件。
  「升级已有」与「全新移植」工作量差一个量级，实施前必须先 diff 同名文件。
- **全新功能**：G-02 deadline 层、G-03 跨 turn stale 断路器、G-04 usage 锚定、
  G-15 keyless 检索池、G-16 项目技能信任门、G-17 会话导入、MP（gateway 半边）、BT、PT。
- **实为升级**（深潜后降级工作量）：G-01（guardrails 已有 halt 式检测）、G-06（压缩内
  prune 已有）、G-12（registry/steer 通道/background 均已有）、G-13（MoA facade +
  `moa/<preset>` 已存在）、G-18（spillover 已有）。
- **纠错**：G-15 的「50/50 分流」为文档误传（实为 5 厂商均匀轮询）；G-12 transcript
  路径应为 `cache/delegation/live/`（远程后端只读挂载点）而非 `~/.intellect/delegation/`。
- 深潜文档 §10 同时是 MP-00 (b) 路线的完整 checklist（scope 处处包裹/handle 缓存/
  agent cache 三重边界/ingress 前 stamp）。
