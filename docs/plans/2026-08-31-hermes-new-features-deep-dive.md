# Hermes 新功能深潜 — 对照源码的实现级分析

> **日期**：2026-08-31
> ** companion 文档**：`docs/plans/2026-08-31-intellect-improvement-plan-from-hermes.md`（主计划）
> **方法**：对主计划列举的能力逐一对照 intellect HEAD（`c6f4d54`）与 Hermes HEAD（`4209d371`）
> 源码核实。**关键背景**：两仓共享祖先——`agent/tool_guardrails.py`、`agent/chat_completion_helpers.py`、
> `tools/tool_result_storage.py`、`agent/context_compressor.py`、`run_agent.py`（steer 系）在 intellect
> 中均已存在同源文件。因此每个功能必须先 diff 同名文件，「升级已有」与「全新移植」的工作量差一个量级。
> 本文是深潜索引：每功能给出 Hermes 实现锚点、机制精要、数据格式、config、测试参照，
> 以及 **intellect 现状与真实差距**。主计划相应条目已按本文修正。
> **使用约定**：文中 file:line 钉在两仓快照（Hermes `4209d371` / intellect `c6f4d54`），
> 行号会随提交漂移——**实施时一律以符号名（函数/常量/class）为锚定位，行号仅作参考**。
> 2026-08-31 评审修订：14 项承重断言已抽样回验源码通过；§5/§6 两处「待验证」已当场裁决；
> §4 补字节化范围说明；§8/§9 补默认值裁定。

---

## 〇、总分类（对照结论）

| 主计划条目 | 分类 | intellect 现状 |
|---|---|---|
| G-01 stall 断路器 + continue-intent | **升级 + 新增恢复** | `agent/tool_guardrails.py` 已有 identical-args 检测（halt-decision 式，L253/326）；**缺** streak 观测/notice/result-stub/continue-intent |
| G-02 统一 deadline 层 | **全新** | 无 `agent/deadline.py`；无 resolver 表 |
| G-03 跨 turn stale 断路器 | **全新** | `chat_completion_helpers.py` 仅有单次调用内 stale 检测（:298）；无跨 turn streak |
| G-04 usage 锚定 + G-05 413 字节 | **全新** | 无 `capture_usage_anchor`；413 已分类（`error_classifier.py:48`）但按 token 恢复 |
| G-06 positional prune | **升级** | `context_compressor.py:816` 已有压缩内 `_prune_old_tool_results` 摘要化；**缺** proactive 独立触发/reclaim gate/rearm runway |
| G-12 delegation steering/tail/bg | **升级×3** | `_active_subagents` registry 已有（`delegate_tool.py:155`）；steer 通道已有（`run_agent.py:2148` + `apply_pending_steer_to_tool_results:2490`）；background 委派已有（`delegate_tool.py:2040`）；**缺** subagent steering 面、live transcript、durable 完成队列 |
| G-13 MoA preset 虚拟模型 | **升级** | **已有** facade（`moa_loop.py:575` `_ChatNamespace`）+ `moa/<preset>` 虚拟 provider（`agent_init.py:329`）+ cadence 签名雏形（`_turn_signature:134`）；**缺** every_n/精确 guidance peel/事件族渲染 |
| G-15 keyless 检索池 | **全新** | `plugins/web/` 已有 exa/parallel/tavily/firecrawl 的 keyed 版；无 `keyless_mcp.py`、无 keenable |
| G-16 项目本地技能 + 信任门 | **全新** | 无 `PROJECT_SKILLS_SUBDIRS`/`trusted_project_dirs` |
| G-17 会话导入 | **全新** | 无 `foreign_sessions.py` |
| G-18 MCP 门槛/桩/健康扫描 | **全新（部分）** | `tools/budget_config.py`+`tool_result_storage.py` spillover 已有；**缺** MCP 50K 紧门槛、identical-result 引用桩、健康扫描 |
| MP multiplex | **全新（gateway 半边）** | seam 已有（前文评审已详）；Hermes 全部参照见 §10 |
| BT bot mode | **全新** | 零对应 |
| PT pets | **全新** | 零对应 |
| G-07/08/09/10/11/14/19/20/21 | 强化/性能/加固（非新功能，不在本文展开） | — |

---

## 1. G-01 · stall 断路器 + continue-intent 恢复

**Hermes 锚点**：`agent/tool_guardrails.py`（observe_call L540-617、`IdenticalCallObservation` L221、
stub 生成 L629、阈值 L85/93/98）；`agent/agent_runtime_helpers.py`（`trailing_continue_intent` L4370，
尾部 160 字符窗口 + 全文 ≤400）；`agent/conversation_loop.py` L8144-8201（恢复注入点）。

**机制精要**：
- streak 键 = `(tool_name, sha256(canonical_args))` + 结果哈希（JSON 可解析则 canonical 重序列化后
  sha256）；**签名与结果都相同**才累计，任一变化即清零。状态挂 controller、每 turn `reset_for_turn`。
- 两级动作：**notice**（第 3 次起追加到工具结果尾部，纯观测永不阻断）；**result-stub**（第 2 次起、
  结果 ≥512 字符且成功 → 结果替换为引用桩，工具仍真实执行——是 context 去重不是缓存）。
- poller 豁免表：`process` 工具与 `_get_result`/`_poll` 后缀豁免 notice（不豁免 stub）。
- **continue-intent 恢复**：turn 结束、无 tool call、final 文本尾部命中 intent 正则 → 注入
  interim assistant + `[System: Continue now...]` user nudge，每 turn 上限 2 次；nudge 由压缩器
  识别并在持久化 transcript 剥离。
- **cache 安全**：notice/stub 都在结果构造时注入（append-only，不改已发送历史）——移植必须保留。

**stub 格式**：`[hermes note: this result is byte-identical to the {tool} result earlier this turn
(tool_call_id {id}). Refer to that result; it has not changed. Args: {args_json[:120]}…]`，若首调已
spill 到磁盘则追加 spill 路径行。⚠️ 移植时品牌词替换为 `[intellect note: ...]`（notice 同理），
不要照抄 Hermes 文案。

**config**：`agent.stall_guards: true`；阈值 3/512/120 为模块常量。

**intellect 适配差异**：intellect 的 `ToolCallGuardrailController` 是 before/after + halt-decision 式
（能拒执行），与 Hermes 的 observe-式互补。移植方案：在现有 controller 上**加** `observe_call`
streak 状态（不动 halt 路径），notice/stub 在 `run_agent` 工具结果构造点（对应 Hermes run_agent
L8367 的位置）接入；continue-intent 需要 interim-assistant 机制——先确认 intellect 压缩器是否
有等价的合成消息剥离钩子，没有则 G-01 恢复部分单独评估。

**测试参照**：`tests/agent/test_stall_guards.py`（Hermes）。

## 2. G-02 · 统一 deadline 层

**Hermes 锚点**：`agent/deadline.py`（651 行，**自包含**，仅依赖 config 只读加载 + psutil）。

**机制精要**：
- `resolve_timeout(key, default, env_var)` 全树唯一 resolver：config `timeouts.<dotted>` > 旧 env
  > default；bool/NaN 拒绝并 fall-through；`clamp_timeout`：None/≤0 = 无界、封顶 365 天。
- `run_bounded_async`：deadline 由 **daemon threading.Timer** 驱动（不依赖事件循环 timer——loop 被
  同步调用卡死时依然生效）；超时 cancel + **放弃**子 task（不等 cancellation 完成，anyio/MCP 的
  cancellation-shield 正是永久卡死源）；第二只 +5s watchdog 未被处理则 `faulthandler` 全线程转储。
- `run_bounded_sync`：daemon 工作线程 + Event；**注释明令禁止热循环使用**（每次超时永久泄漏弃线程）。
- `DeadlineExpired(TimeoutError)` 携带 label——喂 error_classifier 时不得归因 provider。
- `kill_process_tree`：Windows taskkill /T；POSIX psutil 快照后代（父死后 reparent 前拍）→ 组长
  killpg → 逐个 signal；PID+创建时间防复用。

**消费点**：`tools.concurrent_batch` / `tools.sequential_call`（未配置时继承前者，防两路径漂移）/
`mcp.tool_call`；sequential 刻意**不用** `run_bounded_sync`（人类审批窗口动态延长 deadline，固定
deadline 原语不适用）。

**intellect 适配差异**：全新文件可直接移植（自包含）；接线点为 intellect 的
`tool_executor.py` 并发/顺序批量、`mcp_tool.py`、`environments/` 进程树清理。四条不变量必须保住：
操作异常原样传播 / timeout 具象化且与 transport 超时可区分 / 非正=无界 / sync 版不进热循环。

## 3. G-03 · 跨 turn 流式 stale 断路器

**Hermes 锚点**：`agent/chat_completion_helpers.py`（状态三函数 L709-729、giveup L803-814、
bump/reset 共 9 处、中断计数 L730-755）。

**机制精要**：
- 状态 = `agent._consecutive_stale_streams`（**agent 实例属性、跨 turn 存活**——与「每次调用一个
  watchdog」的本质区别）。`HERMES_STREAM_STALE_GIVEUP=5`：连续 5 次 stale kill 后**不做任何网络
  尝试**直接 raise（动机：观察到一个会话连续 494 次重试 3 天）。
- 进度时间戳在 chunk 到达的**最早点**刷新（Relay 拦截器处理之前），stale = 无 chunk 超过阈值
  （默认 180s；本地端点 900s；上下文 >100k 抬到 ≥300s；reasoning floor 只抬不压；**显式配置永不
  被缩放**）。
- **成功即清零**（含 partial-stream stub 返回——收到过 delta 即证明 provider 活着）。
- **provider 交换三处 reset**：switch_model（成功后）、restore_primary_runtime、try_activate_fallback
  ——漏任何一处则健康新 provider 被旧 streak 永久短路。
- 用户中断也计证：响应开始前中断且已等 ≥30s = 无响应证据。

**intellect 适配差异**：`chat_completion_helpers.py:298` 的单次 stale 检测是同源基础；新增 streak
计数 + giveup + 三处 provider-swap reset。依赖的 Hermes 基建（claim_stream_writer、request-local
client 关闭）需对照 intellect 流式路径的等价物，缺则连「关连接重试」一起评估。

## 4. G-04/G-05 · usage 锚定 + 413 按字节

**Hermes 锚点**：`agent/model_metadata.py:3772-3854`（anchor）、`agent/conversation_loop.py:4230`
（唯一写点）、`agent/message_sanitization.py:403`（`serialized_messages_bytes` 精确字节）、
恢复分支 `conversation_loop.py:5652-5804`。

**机制精要**：
- anchor dict：`{prompt_tokens, completion_tokens, base_count, base_last_id, base_last_role}`；
  **失效判定 fail-closed 回退全量估算**：pt≤0 / 消息数变少 / `id(messages[base_count-1])` 或角色
  变（压缩/拼接/重写都会触发）。公式 = `anchor.pt + anchor.ct + estimate(messages[base_count:])`。
- 写点唯一（主循环响应汇合处；MoA/aux 不经过不污染）。
- 413：压缩前后 `len(json.dumps(messages).encode())` 精确度量，**字节降 ≥5% 或消息数减少**才算
  进步；无进步 → 剥 tool 消息中的图片 part 再试（token 估算对图片 flat 计价看不见 base64 字节，
  这是根因）；`max_compression_attempts=3` 后终态 `compression_exhausted`。
- ⚠️ **字节化范围（评审核实）**：Hermes 仅 413 分支按字节（`conversation_loop.py:5763`
  `new_bytes < original_bytes * 0.95`）；同文件 `:5909`/`:6074` 的其余恢复路径仍按 token
  （`new_tokens < original_tokens * 0.95`）——属其未完成迁移，intellect 移植只需覆盖 413 分支，
  不必跟随扩散到其它路径。

**intellect 适配差异**：锚定是全新小模块（rust usage.rs 扩展为主计划的 Rust 落点）；字节度量一行
函数；413 分支并入 intellect 既有错误恢复路径（`payload_too_large` 已有分类）。**前提**：压缩器
能做同消息数原地缩减，否则字节永无进步——先验证 intellect 压缩能力。

## 5. G-06 · proactive prune（升级压缩内 prune）

**Hermes 锚点**：`agent/context_compressor.py`（`prune_tool_results_only` L4248 触发入口、
`_prune_old_tool_results` L3950 五趟裁剪、`_estimate_msg_budget_tokens` L1548 评分）；主循环接线
`conversation_loop.py:7658`（「超阈值但压缩被 cooldown 阻塞」的 elif 分支）。

**机制精要**：
- 触发：`current_tokens ≥ proactive_prune_tokens`（默认 0=关）+ 防抖低水位 rearm + DB 能力门。
- **reclaim gate（cache 损益）**：`before-after ≥ min_reclaim_tokens`（默认 4096）才提交，否则整个
  no-op（caller 以 `result is not input` 判断）；量化的是「一次 cache break 的代价」——hysteresis
  设计而非成本模型。
- **rearm runway**：提交时持久化 `next_rearm = after + max(reclaimed, trigger, min_reclaim)` 到
  session 的 model_config（`archive_and_compact` 原子换 transcript）——必须再长满一个 trigger
  才准再 break 一次 cache。
- 五趟：MD5 去重 → 大结果摘要化（≥8000 字符，保 skill marker 防幽灵技能）→ assistant 工具参数
  JSON 内截断（>500 字符）→ 图片退役（仅保最新 3 张）→ 尾部压力降级。

**intellect 适配差异**：`_prune_old_tool_results` **已存在**（摘要化同源）——真实差距是①独立
触发入口（压缩 cooldown 阻塞时的 elif 分支）②reclaim gate ③rearm runway 持久化。**前置已
裁决（2026-08-31 评审）**：`intellect_state.py:2019` 的 `replace_messages`（rust 路径 `:2084`）
即是原子换 transcript 原语，**已存在**；剩余仅 rearm 值的持久化位置（session model_config 单键
或等价物）实施时定。主计划的 cache 政策边界（默认 off）不变。

## 6. G-12 · delegation 三件（升级）

**steering**（Hermes `delegate_tool.py` steer_subagent L290-335 + `run_agent.steer` L3521）：
写侧在**同一把 registry 锁内**校验 `accepting_steer` + owner 三元组（transport/session_record 用
**对象同一性**比较防伪造）后 append 到 child `_pending_steer`；读侧两个 drain 点——工具批结束后
追加到批内最后一条 `role:"tool"` 消息 content 尾部 + pre-API 兜底 drain。消息格式是自描述 marker
（`[OUT-OF-BAND USER MESSAGE …]` 带 provenance 与 replay 规则）——历史上裸 `User guidance:` 被
模型拒绝执行，不能简化。child 结束原子关 steering；竞态输了则精确文本写入完成条目 `missed_steer`。
**intellect**：`_active_subagents` registry 与 steer 通道（parent 级）都有——差距集中在 subagent 面
（锁内身份校验 + owner 捕获 + missed_steer）。

**live transcript tail**（`tools/delegation_live_log.py`，429 行自包含）：路径
`<home>/cache/delegation/live/<delegation_id>/task-<n>.log`（选 `cache/delegation` 因它被只读挂载进
Docker/Modal/SSH 后端，任何后端都能 `tail -f`）；行格式 `HH:MM:SS role | text`，append-per-write
无长驻句柄；**每行强制 redact**（redactor 不可用则整行扣留——目录对沙箱可读）；`manifest.json`
记 per-task 状态；7 天保留。纯 side-channel，prompt cache 零影响。**intellect 可近乎直搬**。

**background fan-out 差距**：intellect 已有 background 委派；Hermes 的增量是 **durable 完成队列**
（SQLite 持久化 + claim 三态 + 尝试上限 8 + 48h 重放窗 + 崩溃恢复 `restore_undelivered_completions`
+ 目标分类 deliver/terminal/retry——父会话被 /new 终结则 drop）。**intellect** 的
`tools/async_delegation.py` 已有 rust DelegationRegistry。**已核实（2026-08-31 评审）**：
`async_delegation.py:24` 的 `_completion_queue` 为模块级**纯内存 List**，rust registry 亦只有
register/complete/cancel——durable 完成队列**确认缺失**（SQLite 持久化 + claim 三态 + 崩溃恢复
需新建；与 gateway delivery ledger 同构，模式可复用）。

## 7. G-13 · MoA 补齐（升级，非新建）

**intellect 已有**：facade（`_ChatNamespace`/`_CompletionsNamespace`）、`moa/<preset>` 虚拟
provider（`agent_init.py:329`）、cadence 签名雏形。**真实差距**（Hermes `agent/moa_loop.py`）：
- `every_n:<N>` cadence：turn 前缀签名变化重置计数 + **advisory 状态真正前进才消耗 cadence 槽**
  （流式重试不算），off-cadence 把 cache key 钉到上次 on-cadence 的 key——命中不重跑 advisor、
  不重复记账、不发 trace。
- guidance **append-at-end + 精确 peel**（三形态 attach：string 追加/trailing text part/新 user
  消息，严格保角色交替与 KV 前缀缓存；peel 是 failover redecoration 的精确逆操作）。
- 事件族渲染：`moa.reference{label,index,count,text}`（每 advisor 一块 thinking 风格标签块）、
  `moa.progress{refs_done/total}`、`moa.phase/aggregating`。
- `enabled:false` preset 不被隐式 `/model` 匹配；禁递归（slot provider=="moa" 写时拒绝）。

## 8. G-15 · keyless 检索池

**Hermes 锚点**：`plugins/web/keyless_mcp.py`（自包含，仅依赖 requests + config）+
`agent/web_search_registry.py:159-325`（`_resolve` 四步：显式 config → 唯一可用 → legacy 偏好 →
**keyless walk 严格最后**）+ 救援 `tools/web_tools.py:431-578`。

**机制精要**：
- keyless provider **不是**新注册类型：仍是普通 `WebSearchProvider` 插件，差异仅两方法——
  `is_available()` 只看 key（防 legacy 序把 keyed 用户路由到匿名层）、`is_keyless_available()`
  只被 registry 第 4 步调用。
- 分流三态 `web.provider_tier.<name>`：`free` 强制 keyless / `paid` 强制 keyed / auto（有 key 用
  keyed）。
- 5 厂商轮询环 `("exa","parallel","tavily","firecrawl","keenable")`：cursor 由 per-process 随机
  session id 播种（多进程舰队均匀分布）+ 每请求 +1；**仅 rate-limit 形错误前进**，非限流错立即
  返回。⚠️ 主计划的「50/50 分流」是文档口径误传，实际是均匀轮询——已修正。
- 一次性救援：主 backend 失败 → 单次 keyless 尝试，结果标 `rescued_from`、**永不写缓存、非粘性**。
- 厂商端点：Exa/Parallel 走 MCP JSON-RPC、Tavily `X-Tavily-Access-Mode: keyless`、Firecrawl
  keyless client、Keenable 公共端点头。

**intellect 适配差异**：`plugins/web/` 已有同名 5 厂商中的 4 个（exa/parallel/tavily/firecrawl，
无 keenable）——移植 = 新增 `keyless_mcp.py` + 各 provider 加 `is_keyless_available` +
registry 第 4 步 + 救援挂 intellect 的 web_tools 分发层。

**默认值裁定（2026-08-31 评审）**：Hermes `web.keyless_fallback` 默认 **true**——用户查询**默认**
流向匿名第三方端点。intellect 取**默认 false** + 显式开启（首次开启提示查询将离开本机流向匿名
端点）：与仓库 fail-closed 纪律一致，隐私姿态比 Hermes 保守；差异写入 website docs。

## 9. G-16/G-17 · 项目技能信任门 + 会话导入

**G-16**（`agent/skill_utils.py:633-905`）：候选目录 `(".hermes/skills", ".agents/skills")`（intellect
对应 `.intellect/skills`）；信任记录**不是独立文件**而是 `config.yaml` 的 `skills.trusted_project_dirs`
绝对路径列表 + `skills.project_discovery` 总门；无首次交互 prompt——banner 一行提示 + 显式
`skills trust [path]`（无参 = cwd 的 git 根）；非交互继承靠 repo 级全局存储 + `TERMINAL_CWD`
per-surface workdir（无 workdir 的 surface 解析不到项目就什么都不加载）；**内容级第二道门**：
信任只放行发现，每次加载仍过内容扫描（hash 缓存 + dangerous 判定 → 检疫，scanner 崩溃也
fail-closed 检疫）——防「trust 后 git pull 注入恶意技能」。intellect 需同时移植扫描器或对接
既有 skills 审查面。**默认值裁定（2026-08-31 评审）**：`skills.project_discovery` 跟随 Hermes
默认 **true**，**硬前提**：内容扫描/检疫门与发现功能**同一 PR 交付**——它是「trust 后注入」的
唯一防线，不可后补。

**G-17**（`hermes_cli/foreign_sessions.py`，489 行）：双源解析（Claude Code
`~/.claude/projects/*/*.jsonl`、Codex `~/.codex/sessions/**/rollout-*.jsonl`）；**转换契约**：只产
纯 user/assistant 文本、从不伪造 tool_calls（工具史成括号摘要）、system payload 永不导入、连续
同角色合并、leading-assistant 补唯一 user stub；写入只用 SessionDB 三方法
（`create_session(source, cwd, origin_json)` / `append_message` / `set_session_title`）+
`origin.imported_from` 元数据。intellect 的 SessionDB 同源，近乎直搬；`--continue` 的 per-terminal
breadcrumb（tty/multiplexer env 推导 terminal-id）可作第二步。

## 10. MP · multiplex 的 Hermes 全参照（in-process 路线）

**核心原语**：`_profile_runtime_scope(home)` = 同时 set home override + secret scope，**所有以
profile 为单位的窗口都包它**（每 turn、secondary adapter 创建/重连、cron/后台任务、config 重载、
api_server 每请求）——任何绕过 scope 直读 os.environ/config 的路径都是泄漏点。

**进程内组织**（对主计划 MP-00(b) 的实现参照）：
- adapter：`runner._profile_adapters[profile][Platform]`，secondary 在其 scope 内 create+connect；
  **ingress 前 `set_owner_profile`**（session key 命名空间化先于 handler）。
- DB：`RecoverableHandleCache` 按 db_path（随 home override 变化）per-path 缓存 handle——两 profile
  永不共享。
- agent cache：`OrderedDict[session_key → (agent, config_signature)]`，LRU 128 / idle TTL 3600 /
  内存压力阀（RSS 对比 cgroup 派生预算 0.65 分位、floor 512MB，走软驱逐下回合重建）。
- 冲突分级：端口绑定冲突 → `SecondaryPortBindingConfigError` **只跳过该 profile**（不拖垮
  multiplexer）；同凭证/同 listener → 拒启 + `duplicate_credential/duplicate_listener` 标记。
- HTTP 前缀：default profile 持唯一 listener；`/p/<profile>/...` 解析三态（None/名字/404 拒绝），
  **multiplex off 时仅自指前缀放行**（防跨 profile 能力泄漏，intellect 的 GW-302 守卫同向）。

**intellect 适配**：主计划 MP-00 推荐 (a) supervisor——上述 (b) 的复杂度清单（scope 处处包裹、
handle 缓存、agent cache 三重边界、ingress 前 stamp）就是「(a) 起步」的论据；若未来切 (b)，
本文档是逐项 checklist。

## 11. BT · Bot Mode

**message_agent**（`tools/bot_mode_dm.py`）：schema 仅两参数（target/message，≤16000 字符）；
注入 = `ensure_message_agent_tool` 每 turn 幂等注入 `agent.tools`（gate 链：config → session
title == "Bot Chat" → install managed）；执行 = tool_executor **硬编码分发分支** + 双层 title-gate
（执行时重读 title 与 home——home 从 `_session_db.db_path` 推导而非环境变量，多 profile 下环境
不可信）。attribution：`Message from 🤖 {handle} (@{handle}): ` 服务端前缀。消息体从不进 shell
参数——写 0o700 per-user temp 文件，后台 runner 拥有并在 finally 删除。本地传输 =
`hermes -p <profile> chat --in ~ -c "Bot Chat" --create-if-missing -Q --query-file <tmp>`，全部走
`terminal_tool(background=True, notify_on_complete=True)`——**回复经完成通知在发送者下一回合
到达**（fire-and-forget 契约）。

**roster/协议注入**（`tools/bot_mode_probe.py`）：roster = default + `profiles/*` 目录名；协议
section（含 roster 行）注入 Bot Chat 的 SOUL/system prompt，按 (process, home) 缓存保证压缩重建
字节一致；**capability epoch**（能力面 sha256[:12] 指纹嵌 prompt）实现「用户改能力 → 下一条
消息生效」。

**relay 文件协议**（`tools/bot_relay.py`）：`<root>/bot_relay/{outbox,claimed,replies,locks}/`；
envelope JSON 六字段 + uuid id；claim = 原子 rename outbox→claimed 防双投；TTL 900s 过期写
`queued_expired` reply；回复 waiter 是 stdlib-only 轮询脚本（2s 间隔、上限 900s）经 background
完成通知回投；per-profile turn lock = flock + 120s 等待。**gateway 侧零网络**——所有跨连接 IO
由 Desktop 持有 socket 完成（intellect 对应形态 = MP-04 前缀 + TUI_AUTH_TOKEN，见主计划 BT-03）。

**房间预算**（Desktop 常量块）：`MAX_ROUNDS=3 / MAX_MESSAGES=10 / MAX_CONTINUATIONS=2 /
HISTORY_LIMIT=24 / MAX_MEMBERS=6`，continuation 独立于 message cap。**blob 头像**：FNV-1a +
xorshift 确定性 PRNG（name → face 跨会话稳定），npm `blobatar`。

## 12. PT · Pets

**Python 核心单源**（`agent/pet/`，CLI/TUI 共用）：constants（帧几何 192×208、6 帧/state、
LOOP_MS=1100；8 行 legacy 与 9 行 Codex 两种 atlas taxonomy，按 sheet 实际行数自动选择）、
state（活动信号 → 状态优先级）、manifest（petdex.dev 拉取 + 300s TTL + **host-pin 防 SSRF**：
仅允许 petdex.dev/*.petdex.dev）、store（`<home>/pets/<slug>/{pet.json, spritesheet.webp}`，
slug 防 traversal）、render（kitty/iTerm2/sixel/unicode 四协议；纯 env 探测**不发 DA1 查询**防
管道挂死；TUI 走 kitty Unicode placeholder + 224 codepoint 变音符表；unicode 降级 = 半块网格
truecolor，有独立清晰度下限）。
**config**：`display.pet.{enabled:false, slug, render_mode:auto, scale:0.33, unicode_cols}`——纯
display 关注点，对 prompt/toolset/cache 零影响。
**TUI 半边**：`petSprite.tsx`（cells → Ink `<Text>` 双色半块；kitty 帧不触发 Ink repaint）+
`pet.cells` RPC（进 long-handler 池）。
**移植要点**：row taxonomy 必须从实际 sheet 推断；变音符表照抄 kitty 规范；本地孵化
（generate/：base drafts → hatch per-state → atlas 合成校验，`_MIN_FILLED_STATES=6`）可后置。

---

## 附：主计划据此修正的条目

1. **G-01**：注明「identical-args 检测已有（halt 式），新增为 streak 观测 + stub + continue-intent
   恢复，落点扩展现有 `ToolCallGuardrailController`」。
2. **G-06**：注明「压缩内 `_prune_old_tool_results` 已有；差距 = proactive 触发 + reclaim gate +
   rearm runway（依赖 SessionDB 原子换 transcript 能力，实施前验证）」。
3. **G-12**：注明「steer 通道与 background 委派已有；差距 = subagent steering 面（锁内身份校验）、
   live transcript、durable 完成队列（与 delivery ledger 同构）」。
4. **G-13**：从「preset 注册为虚拟模型」降级为「补 every_n cadence + guidance peel + 事件族」
   （facade 与 `moa/<preset>` 已存在）。
5. **G-15**：删除「50/50 流量分流」表述（源码为 5 厂商均匀轮询）；注明 plugins/web 已有 4/5 厂商
   keyed 版。
6. **G-18**：注明「spillover 已有（`tool_result_storage.py`）；差距 = MCP 50K 紧门槛 +
   identical-result 引用桩（与 G-01 stub 同源，一次实现两处复用）+ 健康扫描」。

### 第二轮修订（2026-08-31 评审，14 项承重断言抽样回验通过后）

7. **§5/G-06 前置裁决**：`replace_messages`（`intellect_state.py:2019`）已存在，原子换
   transcript 原语不再是缺口；剩余仅 rearm 值持久化位置。
8. **§6/G-12 差距确证**：`async_delegation.py:24` 完成队列纯内存——durable 队列确认缺失，
   不再「需对照」。
9. **§4/G-05 字节化范围**：仅 Hermes 413 分支按字节，其余路径仍按 token，intellect 不跟随扩散。
10. **§8/G-15 默认值裁定**：`web.keyless_fallback` intellect 默认 **false**（Hermes true，
    隐私姿态更保守）。
11. **§9/G-16 默认值裁定**：`skills.project_discovery` 默认 true，检疫门须同批交付。
12. **使用约定**：行号钉快照，实施以符号名为锚；§1 stub 文案品牌词替换为 `[intellect note:]`。
