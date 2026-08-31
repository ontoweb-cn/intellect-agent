# 阶段执行细化（路线图 → 文件级工作项）

> **日期**：2026-08-31。输入：`2026-08-31-phased-implementation-roadmap.md`（分期）。
> **粒度**：每包拆到「新建/修改哪些文件、函数签名草案、测试文件、验收命令」。
> **制定期核验（2026-08-31 实测）**：
> ① **P0-5 结论先行：G-08 不降级**——`agent/message_sanitization.py` 无 id 变体处理
> （仅 alternation 保留逻辑 L249-278，无别名集）；
> ② **A1-2 注入点已存在**——`agent/tool_executor.py:480/:1005` 两处
> `_append_guardrail_observation`（concurrent+sequential）+ `run_agent.py:4611` 定义（after_call
> + warn/halt 处理），streak/notice/stub 扩展进此现成函数即可；
> ③ rust-ci.yml 已有三 job 结构（rust-unit / rust-full / parity），P0-1 的 CI 冒烟是**加 step**
> 而非新 workflow。

---

## Phase 0（M0）

### P0-1 · rust 绑定版本 fail-fast（G-20）
**修改** `intellect_community_core/__init__.py`：
- 在「Import the compiled extension」后追加版本握手：import 成功后读取扩展的
  `__version__`（若无则跳过并 debug log），与 `pyproject.toml` 的 rust-core 期望版本比较，
  不匹配 → `RuntimeError` 带修复指引（`maturin develop --release`）。
- 保持「site-packages 优先」既有逻辑不动。
**修改** `.github/workflows/rust-ci.yml`：在 parity job 末尾加 `python -c "import
intellect_community_core as c; assert c.__version__"` 冒烟 step（3 行）。
**测试** `tests/test_community_core_init.py`：版本缺失跳过、版本不匹配 raise、匹配通过。
**验收**：`python -c "import intellect_community_core"` 本地过；CI 冒烟 step 绿。

### P0-2 · 基准 harness
**新建** `scripts/bench/bench_sessions_rich.py`：构造 10k sessions 的临时 state.db（复用
SessionDB 写 API），计时 `list_sessions_rich` p50/p95，输出 JSON 到 stdout。
**新建** `scripts/bench/bench_stream_parse.py`：合成 1MB SSE 流喂 StreamAccumulator
（rust）与 python 回退，对比吞吐。
**新建** `scripts/bench/bench_compression.py`：200-turn 会话上下文跑
`estimate_request_tokens_rough` + 压缩入口计时。
**验收**：三条脚本 `--json` 可跑通且数字写入 `docs/plans/bench-baseline.json`。

### P0-3 · MP-00 架构 ADR
**新建** `docs/plans/2026-08-31-adr-multiplex-architecture.md`：
- §1 审计清单（①~④已列，落到文件:符号粒度——20 个模块级缓存逐条 + config/DB/插件/cron）；
- §2 (a)/(b) 对比表（内存/延迟/隔离/工作量）；§3 裁决 + 理由；§4 spike 记录。
- spike 脚本 `scripts/bench/spike_supervisor.py`（可后置到 B1 前完成）：起两个
  `intellect -p X gateway --child` 子进程 + 前端端口转发 + kill B 冒烟。
**验收**：ADR 合入；spike 数字入 ADR §4（或标注「B1 前补测」）。

### P0-4 · 凭据面审计 + get_secret 迁移（MP-01）
**新建** `scripts/audit_credential_reads.py`：grep 归并 `os.environ`/`os.getenv`/
`.env` 文件读于 `agent/ gateway/ plugins/ tools/`，输出 CSV（文件:行:键模式:归类）。
**产出** `docs/plans/2026-08-31-credential-audit.csv`（迁移清单 + 豁免注记）。
**修改**（迁移，逐文件）`agent/credential_pool.py`、`agent/credential_sources.py`、
`agent/auxiliary_client.py`（auth.json 模块缓存惰性化）——读点改 `secret_scope.get_secret()`。
**新建** `.github/scripts/check_credential_reads.sh`（CI grep 门禁：diff 基线外新增裸读即
fail）+ lint.yml 挂载。
**测试** `tests/agent/test_secret_scope_migration.py`：scope 安装时优先读 scope、未安装回退
environ（单 profile 行为不变）、multiplex_active + 无 scope → raise。
**验收**：审计 CSV 覆盖率 100%（脚本重跑零未归类）；迁移点单测绿；CI 门禁生效。

### P0-5 · G-08 前置核验 ✅（本文档制定期已完成）
结论：`message_sanitization.py` 无变体处理，**G-08 维持完整范围**（A1-5 不降级）。

---

## Phase A1（M1）

### A1-1 · deadline 层（G-02）
**新建** `agent/deadline.py`（移植 Hermes 651 行，按深潜 §2 裁剪）：
- `DeadlineExpired(TimeoutError)`（label/timeout_s）、`BoundedResult`、`clamp_timeout`、
  `resolve_timeout(key, *, default, env_var)`（config `timeouts.<dotted>` → env → default，
  经 `load_config_readonly`）、`run_bounded_async`（daemon Timer + cancel+abandon +
  +5s faulthandler watchdog）、`run_bounded_sync`、`kill_process_tree`（psutil 已 pinned）。
**修改** `agent/tool_executor.py`：并发批超时改走
`resolve_timeout("tools.concurrent_batch")`（默认无界=历史行为；到期 cancel 未启动 +
abandon 运行中，结果收集处给出显式 deadline 文案）。
**修改** `tools/mcp_tool.py`：`resolve_timeout("mcp.tool_call")`（模块常量仅为构造前
回退；两个消费点在构造/run 时用时解析，config 热更新可生效）。
**sequential_call 刻意不接线**（评审裁决，对齐 Hermes Phase 2a）：顺序执行器的人类审批
窗口会动态拉长执行——固定 deadline 原语不适用；resolver 键保留，待审批感知的
deadline 方案（审批窗口排除秒数）后再接。
**测试** `tests/agent/test_deadline.py`（21 例 + 取消传播/完成优先）+
`tests/agent/test_deadline_wiring.py`（接线点生效值与基线断言表对比）。
**验收**：门-1 超时等价验收；`timeout=0` 语义不变（无界）。

### A1-2 · stall 断路器 + continue-intent（G-01）
**修改** `agent/tool_guardrails.py`：controller 加 `_identical_streak_*` 四字段 +
`observe_call(tool_name, args, result, *, failed)`（签名+结果双哈希；notice ≥3、stub ≥2 且
≥512 字符；poller 豁免表 `STALL_GUARD_REPEATABLE_TOOLS/SUFFIXES`）+ `_build_result_stub()`。
**修改** `agent/tool_executor.py` 两处 `_append_guardrail_observation` 调用点后接
observe（**顺序：先 observe 原始结果，再走现有 warn 逻辑**——guardrail 后缀含计数会破坏
结果恒等匹配）。
**修改** `agent/conversation_loop.py` 一处：turn 末尾 continue-intent 检测（按门-0 裁决：
interim 回调复用 or 新建剥离钩子）。
**修改** `rust-core/src/tool_utils.rs`：`rust_canonical_tool_args(args) -> str`（sort_keys
紧凑 JSON）+ `#[pyfunction]` 导出；`tests` 在 rust 侧 parity。
**测试** `tests/agent/test_stall_guards.py`（移植 17 例）+ `tests/rust/test_tool_utils.py`。
**验收**：同调用 ×3 触发 notice、×2 且 ≥512 触发 stub；换 args/结果变化重置；默认
`agent.stall_guards: true`（与 Hermes 对齐，notice/stub 均不改历史——cache 安全由构造点
注入保证）。

### A1-3 · 跨 turn stale 断路器（G-03）
**修改** `agent/chat_completion_helpers.py`：`_consecutive_stale_streams` 三函数 + bump/reset
9 处（非流式成功 :1317/:1824 对应点、fallback 激活、Bedrock/主流 stale kill、partial stub）
+ `_check_stale_giveup`（env `INTELLECT_STREAM_STALE_GIVEUP` 默认 5）+ provider 交换三处
reset（`agent_runtime_helpers.py` switch_model/restore + helpers 内 fallback）。
**测试** `tests/run_agent/test_stream_stale_circuit_breaker.py` + `test_..._reset.py`（移植）。
**验收**：连续 5 次 stale 直接 raise 不发网络请求；换模成功清零、失败回滚不清零。

### A1-4 · usage 锚定 + 413 字节（G-04/G-05）
**新建** `agent/usage_anchor.py`：`capture_usage_anchor(agent, usage)`（五字段 dict 存
`agent._usage_anchor`）、`anchored_context_tokens(agent, messages)`（四条 fail-closed）。
**修改** `agent/conversation_loop.py` 响应汇合处唯一写点 + 压力检查读点。
**修改** `rust-core/src/usage.rs`：`ContextAnchor` 序列化结构（state.db 单键存取的编解码）。
**修改** `agent/conversation_loop.py` 413 分支：`serialized_messages_bytes()`（并入
`message_sanitization.py`）+ ≥5%/消息数进步判定 + 剥图兜底（前置门：压缩器原地缩减核验）。
**测试** `tests/agent/test_usage_anchor.py`（12 例）+ `test_413_byte_recovery.py`（3 例）。
**验收**：锚定值驱动压缩触发；413 字节化以断路器同标准回归。

### A1-5 · tool_call_id 变体（G-08，P0-5 已确认全量做）
**新建** `agent/tool_call_id.py`：`id_variants(cid) -> frozenset`（provider 前缀剥/加、大小写、
composite 桥接）、`results_match(call, result)`。
**修改** `agent/message_sanitization.py`：配对消费走变体交集；pre-call 去重 variant-aware。
**修改** `rust-core/src/tool_utils.rs`：变体集生成（纯函数）。
**测试** `tests/agent/test_tool_call_id_variants.py`（前缀/大小写/复合 id 各 3 例 + 去重保留）。
**验收**：变体 id 配对全过；同 id 重复调用结果不丢。

### A1-6 · SessionDB 读池（G-14）
**修改** `agent/storage/sqlite_backend.py`：`_read_conns: threading.local`（per-thread 只读
连接，`mode=ro` URI + WAL 门控——`journal_mode != "wal"` 时池禁用）+ `state.read_pool`
config（默认 on-wal，off 直退单锁）。
**修改** `intellect_state.py`：39 个纯读方法路由到读连接（`_read` contextmanager）。
**修改** `rust-core/src/backend.rs`：`list_sessions_rich` CTE 迁移（对照 gateway/session.py
同源查询）。
**测试** `tests/agent/test_read_pool.py`（双 journal 模式 + 并发读写无 locked + kill 开关）+
基准对比（P0-2 基线）。
**验收**：两段验收（路线图门-1）；DELETE 回退路径行为与主干一致。

---

## Phase A2（M2）

### A2-1 · proactive prune（G-06）
**修改** `agent/context_compressor.py`：`prune_tool_results_only()`（触发：`current_tokens ≥
compression.proactive_prune_tokens`（默认 0）+ rearm 低水位；提交：reclaim ≥
`min_reclaim_tokens`（4096），否则原对象返回）+ rearm 持久化（`replace_messages` +
session 元数据单键，位置 PR 内裁决）。
**修改** `agent/conversation_loop.py`：压缩 cooldown 阻塞 elif 分支接入。
**测试** `tests/agent/test_proactive_pruning.py`（rearm/reclaim/no-op 契约）。
**验收**：默认 off 下逐字节一致；开启后五趟裁剪与 reclaim 门生效。

### A2-2 · 历史清洁（G-07）
**修改** `agent/message_sanitization.py` + `rust-core/src/sanitize.rs`：orphan tool result
丢弃、未配对 call 注入桩（`[Result unavailable — see context summary above]`）、carrier
完整性。压缩与 resume 两入口接线。
**测试** `tests/agent/test_history_sanitization.py`（脏历史 6 例）。

### A2-3 · delegation 三件（G-12）
①**新建** `agent/steer_markers.py`（marker 常量/`format_steer_marker`/`peel_steer_marker`）
+ `tools/delegate_tool.py` subagent steering（锁内 owner 三元组对象同一性校验、
`accepting_steer`、`missed_steer`）+ 两 drain 点（工具批后 + pre-API，接既有
`apply_pending_steer_to_tool_results`）。
②**新建** `tools/delegation_live_log.py`（直搬 Hermes 429 行：`cache/delegation/live/` 路径、
per-line redact、append-per-write、manifest）+ delegate dispatch 接线 + `/agents` tail RPC。
③**新建** durable 完成队列：`tools/async_delegation.py` 扩 SQLite 表（claim 三态 + 尝试上限
8 + 48h 重放 + 崩溃恢复），复用 `gateway/delivery_ledger.py` 骨架。
**测试** `tests/tools/test_subagent_steer.py`（移植，含伪造 owner 拒绝/missed_steer）、
`test_delegation_live_log.py`（17 例）、`test_async_delegation_durable.py`（kill-9 恢复）。

### A2-4 · MoA 补齐（G-13）
**修改** `agent/moa_loop.py`：`_coerce_fanout`（user_turn/per_iteration/every_n:N）+ 双签名
状态机 + off-cadence cache 钉扎；guidance 三形态 attach + `peel_reference_guidance` 精确逆；
事件族 `moa.reference/progress/phase/aggregating`。
**测试** `tests/agent/test_moa_every_n.py` + `test_moa_events.py`。

---

## Phase B1（M3）/ B2（M4）/ A3+PT（M5）

B1/B2 按 MP-00 裁决形态细化（B1-2 的文件面在 ADR 定稿后补本节）；A3 各包均为单点小改
（error_classifier.rs 匹配表、usage.rs per-key、keyless_mcp.py 移植、skill_utils 项目发现、
foreign_sessions 直搬、mcp doctor、release.py 增量、stream_consumer 迁移门控），
不再逐包展开——每包开工前按本模板补 5 行工作项即可。

---

## 实施顺序（本轮交付）

本会话实施 **Phase 0 全部可代码化项**：P0-1（fail-fast + CI step）、P0-2（三脚本 + 基线）、
P0-4（审计脚本 + 清单 + 迁移 + 门禁）、P0-5（已完成，结论记录）、P0-3 ADR 文档与审计清单
（spike 标注 B1 前补测）。A1 起的包在 Phase 0 门过后按路线图排期执行。
